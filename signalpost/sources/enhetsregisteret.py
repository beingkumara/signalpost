"""Enhetsregisteret (Central Coordinating Register for Legal Entities)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import hashlib
import json

from signalpost import config
from signalpost.facts import OFFICIAL_REGISTRY, Fact, Source, is_present, make_fact
from signalpost.http_client import HttpClient
from signalpost.labels import industry_en, org_form_en

BATCH_SIZE = 100

# (output field, json key, label, date key giving the as-of date)
_SIMPLE_FIELDS: tuple[tuple[str, str, str, str | None], ...] = (
    ("name", "navn", "Registered name", None),
    ("founded_date", "stiftelsesdato", "Founded (stiftelsesdato)", "stiftelsesdato"),
    ("registered_date", "registreringsdatoEnhetsregisteret", "Registered in Enhetsregisteret",
     "registreringsdatoEnhetsregisteret"),
    ("business_register_date", "registreringsdatoForetaksregisteret", "Registered in Foretaksregisteret",
     "registreringsdatoForetaksregisteret"),
    ("articles_date", "vedtektsdato", "Date of articles of association", "vedtektsdato"),
    ("website", "hjemmeside", "Website", None),
    ("email", "epostadresse", "Email", None),
    ("phone", "telefon", "Phone", None),
    ("mobile", "mobil", "Mobile phone", None),
    ("latest_annual_accounts_year", "sisteInnsendteAarsregnskap", "Latest annual accounts submitted", None),
    ("in_corporate_group", "erIKonsern", "Part of a corporate group", None),
    ("language_form", "maalform", "Language form", None),
    ("parent_orgnr", "overordnetEnhet", "Parent unit org number", None),
    ("vat_registered", "registrertIMvaregisteret", "Registered in the VAT register",
     "registreringsdatoMerverdiavgiftsregisteret"),
    ("in_business_register", "registrertIForetaksregisteret", "Registered in Foretaksregisteret", None),
    ("in_foundation_register", "registrertIStiftelsesregisteret", "Registered in Stiftelsesregisteret", None),
    ("in_voluntary_register", "registrertIFrivillighetsregisteret", "Registered in Frivillighetsregisteret", None),
    ("in_party_register", "registrertIPartiregisteret", "Registered in Partiregisteret", None),
)

_STATUS_FLAGS: tuple[tuple[str, str, str, str], ...] = (
    ("bankrupt", "konkurs", "konkursdato", "Bankrupt"),
    ("under_liquidation", "underAvvikling", "underAvviklingDato", "Under voluntary liquidation"),
    ("under_forced_dissolution", "underTvangsavviklingEllerTvangsopplosning", "", "Under forced dissolution"),
)


@dataclass(frozen=True)
class EntityResult:
    orgnr: str
    kind: str  # "enhet", "underenhet", "deleted", "not_found"
    raw: dict[str, Any] | None
    facts: tuple[Fact, ...] = field(default_factory=tuple)
    source_url: str = ""
    retrieved_at: str = ""
    http_status: int = 0


def fetch_entity(client: HttpClient, orgnr: str) -> EntityResult:
    """Look up the main unit, falling back to the sub-unit register. Never searches by name."""
    for kind, url_tpl, human_tpl in (
        ("enhet", config.ENHET_URL, config.HUMAN_ENHET_URL),
        ("underenhet", config.UNDERENHET_URL, config.HUMAN_UNDERENHET_URL),
    ):
        url = url_tpl.format(orgnr=orgnr)
        resp = client.get(url)
        if resp.status == 404:
            continue
        if resp.status != 200:
            raise ConnectionError(f"{url} returned HTTP {resp.status}")
        raw = resp.json()
        if raw.get("organisasjonsnummer") != orgnr:
            # Identity guard: never publish a record for a different number.
            raise ValueError(f"{url} returned org number {raw.get('organisasjonsnummer')}, expected {orgnr}")
        return entity_from_raw(raw, kind, url, resp.fetched_at, resp.sha256)
    return EntityResult(orgnr, "not_found", None, (), config.ENHET_URL.format(orgnr=orgnr), "", 404)


def entity_from_raw(raw: dict[str, Any], kind: str, url: str, fetched_at: str, sha256: str,
                    via: dict[str, Any] | None = None) -> EntityResult:
    orgnr = raw["organisasjonsnummer"]
    human_tpl = config.HUMAN_UNDERENHET_URL if kind == "underenhet" else config.HUMAN_ENHET_URL
    source = Source(config.SOURCE_ENHETSREGISTERET, url, human_tpl.format(orgnr=orgnr), OFFICIAL_REGISTRY, sha256)
    is_deleted = raw.get("respons_klasse", "").startswith("Slettet") or "slettedato" in raw
    final_kind = "deleted" if is_deleted else kind
    facts = extract_facts(raw, source, fetched_at, final_kind, via)
    return EntityResult(orgnr, final_kind, raw, facts, url, fetched_at, 200)


def fetch_entities_batch(client: HttpClient, orgnrs: list[str]) -> dict[str, EntityResult]:
    """Look up to 100 main units per request. Missing numbers are left for single lookups."""
    found: dict[str, EntityResult] = {}
    for start in range(0, len(orgnrs), BATCH_SIZE):
        chunk = orgnrs[start:start + BATCH_SIZE]
        url = f"{config.ENHET_SEARCH_URL}?organisasjonsnummer={','.join(chunk)}&size={BATCH_SIZE}"
        resp = client.get(url)
        if resp.status != 200:
            raise ConnectionError(f"batch entity lookup returned HTTP {resp.status}")
        wanted = set(chunk)
        for raw in (resp.json().get("_embedded") or {}).get("enheter") or []:
            orgnr = raw.get("organisasjonsnummer")
            if orgnr not in wanted:
                continue  # identity guard: ignore anything we did not ask for
            record_sha = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            via = {"retrieved_via": "batch_lookup", "batch_response_sha256": resp.sha256}
            found[orgnr] = entity_from_raw(raw, "enhet", config.ENHET_URL.format(orgnr=orgnr), resp.fetched_at,
                                           record_sha, via)
    return found


def extract_facts(raw: dict[str, Any], source: Source, retrieved_at: str, kind: str,
                  via: dict[str, Any] | None = None) -> tuple[Fact, ...]:
    orgnr = raw["organisasjonsnummer"]
    guard = {"matched_orgnr": orgnr, **(via or {})}

    def fact(name: str, value: Any, path: str, label: str, as_of: str | None = None) -> Fact:
        return make_fact(name, value, source, retrieved_at, json_path=path, as_of=as_of,
                         label=label, extra_evidence=guard)

    facts: list[Fact] = [fact("entity_type", kind, "respons_klasse", "Register entry type")]
    for name, key, label, date_key in _SIMPLE_FIELDS:
        value = raw.get(key)
        if is_present(value):
            facts.append(fact(name, _clean_website(value) if name == "website" else value, key, label,
                              raw.get(date_key) if date_key else None))

    facts.extend(_structured_facts(raw, fact))
    facts.extend(_status_facts(raw, fact, kind))
    return tuple(facts)


def _structured_facts(raw: dict[str, Any], fact: Any) -> list[Fact]:
    out: list[Fact] = []
    form = raw.get("organisasjonsform") or {}
    if form.get("kode"):
        out.append(fact("organization_form", {"code": form["kode"], "description": form.get("beskrivelse"),
                                              "description_en": org_form_en(form["kode"])},
                        "organisasjonsform", "Organisation form"))
    for key, name, label in (
        ("forretningsadresse", "business_address", "Business address"),
        ("beliggenhetsadresse", "location_address", "Location address"),
        ("postadresse", "postal_address", "Postal address"),
    ):
        addr = raw.get(key)
        if addr:
            out.append(fact(name, format_address(addr), key, label))
            if name in ("business_address", "location_address") and addr.get("kommune"):
                out.append(fact("municipality", {"name": addr.get("kommune"), "code": addr.get("kommunenummer")},
                                f"{key}.kommune", "Municipality"))
    industries = [
        {"code": raw[k]["kode"], "description": raw[k].get("beskrivelse"),
         "description_en": industry_en(raw[k]["kode"])}
        for k in ("naeringskode1", "naeringskode2", "naeringskode3") if raw.get(k, {}).get("kode")
    ]
    if industries:
        out.append(fact("industry_codes", industries, "naeringskode1..3", "Industry (NACE) codes"))
    if raw.get("harRegistrertAntallAnsatte") and raw.get("antallAnsatte") is not None:
        out.append(fact("employees", raw["antallAnsatte"], "antallAnsatte", "Registered employees",
                        raw.get("registreringsdatoAntallAnsatteNAVAaregisteret")
                        or raw.get("registreringsdatoAntallAnsatteEnhetsregisteret")))
    purpose = raw.get("vedtektsfestetFormaal") or raw.get("aktivitet")
    if purpose:
        path = "vedtektsfestetFormaal" if raw.get("vedtektsfestetFormaal") else "aktivitet"
        out.append(fact("purpose", " ".join(purpose), path, "Stated purpose / activity"))
    sector = raw.get("institusjonellSektorkode") or {}
    if sector.get("kode"):
        out.append(fact("sector", {"code": sector["kode"], "description": sector.get("beskrivelse")},
                        "institusjonellSektorkode", "Institutional sector"))
    capital = raw.get("kapital") or {}
    if capital.get("belop") is not None:
        out.append(fact("share_capital", {
            "amount": capital["belop"], "currency": capital.get("valuta"), "type": capital.get("type"),
            "shares": capital.get("antallAksjer"),
        }, "kapital", "Share capital", capital.get("innfortDato")))
    names = raw.get("historiskeNavn") or []
    if names:
        history = [{"name": n.get("navn"), "from": _date(n.get("fraDato")), "to": _date(n.get("tilDato"))}
                   for n in names]
        out.append(fact("former_names", history, "historiskeNavn", "Former names"))
    if raw.get("fravalgRevisjonDato"):
        out.append(fact("audit_opt_out", True, "fravalgRevisjonDato", "Opted out of statutory audit",
                        raw["fravalgRevisjonDato"]))
    return out


def _status_facts(raw: dict[str, Any], fact: Any, kind: str) -> list[Fact]:
    out: list[Fact] = []
    if kind == "deleted":
        out.append(fact("deleted_date", raw.get("slettedato"), "slettedato", "Deleted from the register",
                        raw.get("slettedato")))
    for name, flag, date_key, label in _STATUS_FLAGS:
        if flag in raw:
            out.append(fact(name, bool(raw[flag]), flag, label, raw.get(date_key) if date_key else None))
    forced = {k: v for k, v in raw.items() if k.startswith("tvang") and k.endswith("Dato") and v}
    if forced:
        out.append(fact("forced_dissolution_events", forced, "tvang*Dato", "Forced dissolution events",
                        max(forced.values())))
    return out


def format_address(addr: dict[str, Any]) -> dict[str, Any]:
    lines = [line for line in addr.get("adresse") or [] if line]
    postcode_city = " ".join(p for p in (addr.get("postnummer"), addr.get("poststed")) if p)
    text = ", ".join([*lines, postcode_city, addr.get("land") or ""]).strip(", ")
    return {
        "text": text, "street": lines, "postcode": addr.get("postnummer"), "city": addr.get("poststed"),
        "municipality": addr.get("kommune"), "country_code": addr.get("landkode"),
    }


def _clean_website(value: str) -> str:
    value = value.strip()
    return value if value.lower().startswith(("http://", "https://")) else "https://" + value


def _date(value: str | None) -> str | None:
    return value[:10] if value else None


def fetch_parent_name(client: HttpClient, parent_orgnr: str, retrieved_for: str) -> tuple[Fact, ...]:
    """For a sub-unit: the parent's registered name, looked up by its exact org number."""
    url = config.ENHET_URL.format(orgnr=parent_orgnr)
    resp = client.get(url)
    if resp.status != 200:
        return ()
    raw = resp.json()
    if raw.get("organisasjonsnummer") != parent_orgnr or not raw.get("navn"):
        return ()
    source = Source(config.SOURCE_ENHETSREGISTERET, url, config.HUMAN_ENHET_URL.format(orgnr=parent_orgnr),
                    OFFICIAL_REGISTRY, resp.sha256)
    return (make_fact("parent_name", raw["navn"], source, resp.fetched_at, json_path="navn",
                      label="Parent unit name",
                      extra_evidence={"matched_orgnr": parent_orgnr, "child_orgnr": retrieved_for}),)
