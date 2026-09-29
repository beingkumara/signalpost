"""Sub-units (establishments) belonging to a main unit."""

from __future__ import annotations

from typing import Any

from signalpost import config
from signalpost.facts import Fact, Source, make_fact
from signalpost.http_client import HttpClient
from signalpost.sources.enhetsregisteret import format_address


def fetch_subunits(client: HttpClient, orgnr: str) -> tuple[Fact, ...]:
    url = config.UNDERENHETER_SEARCH_URL.format(orgnr=orgnr, size=config.SUBUNITS_LISTED)
    resp = client.get(url)
    if resp.status != 200:
        return ()
    source = Source(config.SOURCE_ENHETSREGISTERET, url, config.HUMAN_ENHET_URL.format(orgnr=orgnr))
    return extract_subunit_facts(resp.json(), source, resp.fetched_at, orgnr)


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
        "employees": u.get("antallAnsatte") if u.get("harRegistrertAntallAnsatte") else None,
    } for u in units]
    return (
        make_fact("subunit_count", total, source, retrieved_at, json_path="page.totalElements",
                  label="Number of registered sub-units", extra_evidence=guard),
        make_fact("subunits", listed, source, retrieved_at, json_path="_embedded.underenheter",
                  label=f"Sub-units (first {len(listed)})", extra_evidence=guard),
    )
