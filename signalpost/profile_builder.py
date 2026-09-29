"""Assemble one company profile from all sources."""

from __future__ import annotations

from typing import Any, Callable

from signalpost import __version__, config, orgnr as orgnr_mod
from signalpost.explain import explain
from signalpost.facts import Fact
from signalpost.http_client import BudgetExceeded, HttpClient, utc_now_iso
from signalpost.sources.enhetsregisteret import EntityResult, fetch_entity, fetch_parent_name
from signalpost.sources.regnskap import fetch_accounts
from signalpost.sources.roller import fetch_roles
from signalpost.sources.underenheter import fetch_subunits
from signalpost.sources.website import fetch_website_facts

SCHEMA_VERSION = "1.0"


def build_profile(client: HttpClient, raw_input: str, *, with_website: bool = True) -> dict[str, Any]:
    started = utc_now_iso()
    try:
        orgnr = orgnr_mod.parse(raw_input)
    except orgnr_mod.InvalidOrgnr as err:
        return _empty_profile(raw_input.strip(), "invalid_input", started, [str(err)])

    try:
        entity = fetch_entity(client, orgnr)
    except (ConnectionError, ValueError, BudgetExceeded) as err:
        return _empty_profile(orgnr, "lookup_failed", started, [f"Enhetsregisteret lookup failed: {err}"])
    if entity.kind == "not_found":
        return _empty_profile(orgnr, "not_found", started,
                              ["No main unit or sub-unit with this number exists in Enhetsregisteret."])

    facts: list[Fact] = list(entity.facts)
    warnings: list[str] = []
    sources_checked = [_source_row("Enhetsregisteret", entity.source_url, entity.http_status, entity.retrieved_at)]

    for label, url, fetcher in _secondary_sources(client, entity, with_website):
        try:
            new_facts, extra_warnings = fetcher()
            facts.extend(new_facts)
            warnings.extend(extra_warnings)
            sources_checked.append(_source_row(label, url, 200, utc_now_iso(), found=bool(new_facts)))
        except (ConnectionError, ValueError, BudgetExceeded) as err:
            warnings.append(f"{label} unavailable: {err}")
            sources_checked.append(_source_row(label, url, 0, utc_now_iso(), error=str(err)))

    profile = {
        "schema_version": SCHEMA_VERSION,
        "agent_version": __version__,
        "orgnr": orgnr,
        "orgnr_formatted": orgnr_mod.pretty(orgnr),
        "name": _value(facts, "name"),
        "status": derive_status(entity.kind, facts),
        "checked_at": started,
        "fields": {f.field: f.value for f in facts},
        "facts": [f.to_dict() for f in facts],
        "sources_checked": sources_checked,
        "warnings": warnings,
    }
    return {**profile, "explanation": explain(profile)}


Fetcher = Callable[[], "tuple[tuple[Fact, ...], list[str]]"]


def _secondary_sources(
    client: HttpClient, entity: EntityResult, with_website: bool
) -> list[tuple[str, str, Fetcher]]:
    """Sources beyond the core record. Only main units have roles, accounts and sub-units."""
    orgnr = entity.orgnr
    raw = entity.raw or {}
    if entity.kind == "underenhet" and raw.get("overordnetEnhet"):
        parent = raw["overordnetEnhet"]
        return [("Enhetsregisteret (parent unit)", config.ENHET_URL.format(orgnr=parent),
                 lambda: (fetch_parent_name(client, parent, orgnr), []))]
    if entity.kind != "enhet":
        return []
    fetchers: list[tuple[str, str, Fetcher]] = [
        ("Enhetsregisteret roller", config.ROLLER_URL.format(orgnr=orgnr),
         lambda: (fetch_roles(client, orgnr), [])),
        ("Regnskapsregisteret", config.REGNSKAP_URL.format(orgnr=orgnr),
         lambda: (fetch_accounts(client, orgnr), [])),
        ("Enhetsregisteret underenheter",
         config.UNDERENHETER_SEARCH_URL.format(orgnr=orgnr, size=config.SUBUNITS_LISTED),
         lambda: (fetch_subunits(client, orgnr), [])),
    ]
    website = next((f.value for f in entity.facts if f.field == "website"), None)
    if with_website and website:
        fetchers.append(("Company website", website,
                         lambda: fetch_website_facts(client, orgnr, raw.get("navn", ""), website)))
    return fetchers


def derive_status(kind: str, facts: list[Fact]) -> str:
    values = {f.field: f.value for f in facts}
    if kind == "deleted":
        return "deleted"
    if kind == "underenhet":
        return "subunit"
    if values.get("bankrupt"):
        return "bankrupt"
    if values.get("under_forced_dissolution"):
        return "under_forced_dissolution"
    if values.get("under_liquidation"):
        return "under_liquidation"
    return "active"


def _value(facts: list[Fact], name: str) -> Any:
    return next((f.value for f in facts if f.field == name), None)


def _source_row(name: str, url: str | None, status: int, at: str, *, found: bool = True,
                error: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"source": name, "checked_at": at, "http_status": status, "found_data": found}
    if url:
        row["url"] = url
    if error:
        row["error"] = error
    return row


def _empty_profile(orgnr: str, status: str, started: str, warnings: list[str]) -> dict[str, Any]:
    profile = {
        "schema_version": SCHEMA_VERSION,
        "agent_version": __version__,
        "orgnr": orgnr,
        "name": None,
        "status": status,
        "checked_at": started,
        "fields": {},
        "facts": [],
        "sources_checked": [],
        "warnings": warnings,
    }
    return {**profile, "explanation": explain(profile)}
