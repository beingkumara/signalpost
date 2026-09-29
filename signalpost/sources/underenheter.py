"""Sub-units (establishments) belonging to a main unit."""

from __future__ import annotations

from typing import Any

from signalpost import config
from signalpost.facts import OFFICIAL_REGISTRY, Fact, Source, make_fact
from signalpost.http_client import HttpClient
from signalpost.modules import FAILED, ModuleResult, from_facts
from signalpost.sources.enhetsregisteret import format_address

FETCH_SIZE = 100  # enough to recognise job ads posted by a workplace


def fetch_subunits(client: HttpClient, orgnr: str) -> ModuleResult:
    url = config.UNDERENHETER_SEARCH_URL.format(orgnr=orgnr, size=FETCH_SIZE)
    resp = client.get(url)
    if resp.status != 200:
        return ModuleResult("workplaces", FAILED, (), f"HTTP {resp.status}", url, resp.fetched_at)
    data = resp.json()
    source = Source(config.SOURCE_ENHETSREGISTERET, url, config.HUMAN_ENHET_URL.format(orgnr=orgnr),
                    OFFICIAL_REGISTRY, resp.sha256)
    facts = extract_subunit_facts(data, source, resp.fetched_at, orgnr)
    units = (data.get("_embedded") or {}).get("underenheter") or []
    subunit_orgnrs = {u["organisasjonsnummer"] for u in units if u.get("overordnetEnhet") == orgnr}
    result = from_facts("workplaces", facts, url, resp.fetched_at, "no registered sub-units")
    return ModuleResult(result.module, result.state, result.facts, result.reason, url, resp.fetched_at,
                        {"subunit_orgnrs": sorted(subunit_orgnrs)})


def extract_subunit_facts(data: dict[str, Any], source: Source, retrieved_at: str, orgnr: str) -> tuple[Fact, ...]:
    total = (data.get("page") or {}).get("totalElements", 0)
    units = (data.get("_embedded") or {}).get("underenheter") or []
    units = [u for u in units if u.get("overordnetEnhet") == orgnr]
    if not total or not units:
        return ()
    guard = {"matched_orgnr": orgnr, "filter": "overordnetEnhet"}
    listed = [{
        "orgnr": u.get("organisasjonsnummer"),
        "name": u.get("navn"),
        "address": format_address(u["beliggenhetsadresse"])["text"] if u.get("beliggenhetsadresse") else None,
        "municipality": (u.get("beliggenhetsadresse") or {}).get("kommune"),
        "employees": u.get("antallAnsatte") if u.get("harRegistrertAntallAnsatte") else None,
    } for u in sorted(units, key=lambda u: u.get("organisasjonsnummer") or "")[:config.SUBUNITS_LISTED]]
    return (
        make_fact("subunit_count", total, source, retrieved_at, json_path="page.totalElements",
                  label="Number of registered sub-units", extra_evidence=guard),
        make_fact("subunits", listed, source, retrieved_at, json_path="_embedded.underenheter",
                  label=f"Sub-units (first {len(listed)})", extra_evidence=guard),
    )
