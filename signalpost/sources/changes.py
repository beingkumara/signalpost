"""Changes recorded by Brønnøysund for one company (official change feeds).

This lets a first run report real, dated updates without any stored snapshot:
entity field changes come as JSON-patch operations, role updates as dated events.
"""

from __future__ import annotations

import time
from typing import Any

from signalpost import config
from signalpost.facts import OFFICIAL_REGISTRY, Fact, Source, make_fact
from signalpost.http_client import HttpClient
from signalpost.modules import FAILED, ModuleResult, from_facts

WINDOW_DAYS = 365
MAX_EVENTS = 25
ENTITY_FEED = config.ENHET_UPDATES_URL + "?organisasjonsnummer={orgnr}&includeChanges=true&dato={since}&size=100"
ROLE_FEED = config.ROLLE_UPDATES_URL + "?organisasjonsnummer={orgnr}&afterTime={since}&size=100"
STABLE_ENTITY_FEED = config.ENHET_UPDATES_URL + "?organisasjonsnummer={orgnr}&includeChanges=true"

FIELD_LABELS = {
    "navn": "name", "forretningsadresse": "business address", "postadresse": "postal address",
    "beliggenhetsadresse": "location address", "antallAnsatte": "employees",
    "harRegistrertAntallAnsatte": "employees", "hjemmeside": "website", "epostadresse": "email",
    "telefon": "phone", "mobil": "mobile phone", "naeringskode1": "main industry", "naeringskode2": "industry",
    "naeringskode3": "industry", "kapital": "share capital", "vedtektsfestetFormaal": "stated purpose",
    "aktivitet": "activity", "vedtektsdato": "articles of association", "konkurs": "bankruptcy",
    "konkursdato": "bankruptcy", "underAvvikling": "liquidation", "underAvviklingDato": "liquidation",
    "underTvangsavviklingEllerTvangsopplosning": "forced dissolution", "sisteInnsendteAarsregnskap": "annual accounts filed",
    "registrertIMvaregisteret": "VAT registration", "organisasjonsform": "organisation form",
    "institusjonellSektorkode": "sector", "erIKonsern": "group membership", "slettedato": "deletion",
    "registreringsdatoAntallAnsatteNAVAaregisteret": "employees", "registreringsdatoAntallAnsatteEnhetsregisteret": "employees",
    "hjelpeenhetskode": "auxiliary unit code", "paategninger": "register annotations", "overordnetEnhet": "parent unit",
    "fravalgRevisjonDato": "audit opt-out", "fravalgRevisjonBeslutningsDato": "audit opt-out",
    "registrertIForetaksregisteret": "business register", "registreringsdatoForetaksregisteret": "business register",
    "registreringsdatoMerverdiavgiftsregisteret": "VAT registration", "maalform": "language form",
    "frivilligMvaRegistrertBeskrivelser": "voluntary VAT registration", "historiskeNavn": "former names",
}
MATERIAL = {"name", "business address", "bankruptcy", "liquidation", "forced dissolution", "organisation form",
            "share capital", "main industry", "deletion", "roles", "annual accounts filed"}


def fetch_official_changes(client: HttpClient, orgnr: str, days: int = WINDOW_DAYS) -> ModuleResult:
    since_date = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - days * 86400))
    entity_url = ENTITY_FEED.format(orgnr=orgnr, since=since_date)
    resp = client.get(entity_url)
    if resp.status != 200:
        return ModuleResult("official_changes", FAILED, (), f"entity change feed HTTP {resp.status}", entity_url)
    data = resp.json()
    items = (data.get("_embedded") or {}).get("oppdaterteEnheter") or []
    pages = (data.get("page") or {}).get("totalPages", 1)
    last_resp = resp
    if pages > 1:  # the feed is oldest first; the newest changes are on the last page
        last_resp = client.get(entity_url + f"&page={pages - 1}")
        if last_resp.status == 200:
            items = (last_resp.json().get("_embedded") or {}).get("oppdaterteEnheter") or items
    stable = STABLE_ENTITY_FEED.format(orgnr=orgnr)  # no date in values, so re-runs do not look like changes
    events = [_entity_event(i, stable) for i in items if i.get("organisasjonsnummer") == orgnr]

    role_url = ROLE_FEED.format(orgnr=orgnr, since=since_date)
    role_resp = client.get(role_url)
    if role_resp.status == 200:
        role_days = sorted({(e.get("time") or "")[:10] for e in role_resp.json() or []
                            if (e.get("data") or {}).get("organisasjonsnummer") == orgnr and e.get("time")})
        events += [{"date": d, "type": "roles_updated", "fields": ["roles"], "material": True,
                    "details": [], "source_url": config.ROLLER_URL.format(orgnr=orgnr)} for d in role_days]
    events = _merge_same_day(events)
    source = Source(config.SOURCE_ENHETSREGISTERET, entity_url, config.HUMAN_ENHET_URL.format(orgnr=orgnr),
                    OFFICIAL_REGISTRY, last_resp.sha256)
    if not events:
        return from_facts("official_changes", (), entity_url, resp.fetched_at,
                          f"Brønnøysund recorded no changes in the last {days} days")
    fact = make_fact("official_changes", events[:MAX_EVENTS], source, resp.fetched_at,
                     json_path="_embedded.oppdaterteEnheter[].endringer", as_of=events[0]["date"],
                     as_of_basis="latest_change_date", label=f"Changes recorded by Brønnøysund (last {days} days)",
                     extra_evidence={"matched_orgnr": orgnr, "role_feed_url": role_url,
                                     "role_feed_sha256": role_resp.sha256 if role_resp.status == 200 else None},
                     extraction_method="json_patch_feed")
    return from_facts("official_changes", (fact,), entity_url, resp.fetched_at, "")


def _entity_event(item: dict[str, Any], url: str) -> dict[str, Any]:
    kind = {"Ny": "registered", "Sletting": "deleted", "Fjernet": "deleted"}.get(item.get("endringstype"), "updated")
    details = []
    for change in item.get("endringer") or []:
        key = str(change.get("path", "")).strip("/").split("/")[0]
        details.append({"field": FIELD_LABELS.get(key, key), "op": change.get("op"),
                        "value": _short(change.get("value"))})
    fields = sorted({d["field"] for d in details if d["field"]})
    return {"date": (item.get("dato") or "")[:10], "type": kind, "fields": fields,
            "material": kind != "updated" or bool(set(fields) & MATERIAL), "details": details[:10],
            "source_url": url}


def _merge_same_day(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        key = (event["date"], event["type"])
        if key in merged:
            prev = merged[key]
            merged[key] = {**prev, "fields": sorted(set(prev["fields"]) | set(event["fields"])),
                           "details": (prev["details"] + event["details"])[:10],
                           "material": prev["material"] or event["material"]}
        else:
            merged[key] = event
    return sorted(merged.values(), key=lambda e: e["date"], reverse=True)


def _short(value: Any) -> Any:
    if isinstance(value, dict):
        value = value.get("beskrivelse") or value.get("adresse") or value.get("belop") or value
    text = value if isinstance(value, (int, float, bool)) or value is None else str(value)
    return text[:120] if isinstance(text, str) else text
