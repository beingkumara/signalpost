"""Build a reproducible, diverse sample of org numbers from Enhetsregisteret search."""

from __future__ import annotations

import random

from signalpost import config
from signalpost.http_client import HttpClient

SEARCH_CAP = 10_000  # the API rejects size * (page + 1) above this
PAGE_SIZE = 50
PER_MUNICIPALITY = 12


def municipalities(client: HttpClient) -> list[str]:
    resp = client.get(config.BRREG_BASE + "/enhetsregisteret/api/kommuner?size=1000")
    if resp.status != 200:
        raise ConnectionError(f"municipality list returned HTTP {resp.status}")
    items = (resp.json().get("_embedded") or {}).get("kommuner") or []
    return [k["nummer"] for k in items if k.get("nummer") and k["nummer"] != "0999"]


def sample_orgnrs(client: HttpClient, count: int, seed: int = 42) -> list[str]:
    rng = random.Random(seed)
    kommuner = municipalities(client)
    rng.shuffle(kommuner)
    chosen: list[str] = []
    seen: set[str] = set()
    for kommune in kommuner:
        if len(chosen) >= count:
            break
        base = f"{config.ENHET_SEARCH_URL}?kommunenummer={kommune}&size={PAGE_SIZE}"
        first = client.get(base + "&page=0")
        if first.status != 200:
            continue
        total_pages = min(first.json().get("page", {}).get("totalPages", 0), SEARCH_CAP // PAGE_SIZE)
        if total_pages == 0:
            continue
        page = rng.randrange(total_pages)
        resp = first if page == 0 else client.get(f"{base}&page={page}")
        if resp.status != 200:
            continue
        units = (resp.json().get("_embedded") or {}).get("enheter") or []
        picks = rng.sample(units, min(PER_MUNICIPALITY, len(units)))
        for unit in picks:
            orgnr = unit.get("organisasjonsnummer")
            if orgnr and orgnr not in seen:
                seen.add(orgnr)
                chosen.append(orgnr)
    return chosen[:count]
