"""Job postings from NAV's official job vacancy feed (pam-stilling-feed.nav.no).

Flow per run: page through the feed once (ads changed in the last N days) and index ACTIVE ads by employer
name tokens. For each company, name matches are only candidates: an ad is published only when its detail
record names an employer org number equal to the company or one of its registered sub-units.
The feed uses NAV's published public token; Finn.no ads are not part of the feed.
"""

from __future__ import annotations

import email.utils
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from signalpost.facts import PUBLIC_JOBS, Fact, Source, make_fact
from signalpost.http_client import BudgetExceeded, HttpClient
from signalpost.modules import AVAILABLE, BLOCKED, FAILED, NOT_AVAILABLE, ModuleResult
from signalpost.sources.discovery import name_tokens, slug_tokens

FEED_BASE = "https://pam-stilling-feed.nav.no"
TOKEN_URL = FEED_BASE + "/api/publicToken"
FEED_URL = FEED_BASE + "/api/v1/feed"
AD_URL_PREFIX = "https://arbeidsplassen.nav.no/stillinger/stilling/"
SOURCE_NAME = "NAV job vacancy feed (pam-stilling-feed.nav.no)"
MAX_ADS_PER_COMPANY = 3
FEED_SCOPE = "active NAV feed ads changed in the last {days} days (Finn.no ads are not in the feed)"


@dataclass
class FeedIndex:
    days: int
    ads: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_token: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    pages: int = 0
    complete: bool = False
    error: str | None = None

    def add(self, item: dict[str, Any]) -> None:
        entry = item.get("_feed_entry") or {}
        uuid = entry.get("uuid")
        if not uuid:
            return
        if entry.get("status") != "ACTIVE":
            self.ads.pop(uuid, None)
            return
        self.ads[uuid] = {"uuid": uuid, "title": entry.get("title"), "business_name": entry.get("businessName") or "",
                          "municipal": entry.get("municipal"), "url": item.get("url"),
                          "modified": item.get("date_modified")}
        for token in set(name_tokens(entry.get("businessName") or "")):
            self.by_token[token].add(uuid)

    def candidates(self, company_name: str) -> list[dict[str, Any]]:
        tokens = slug_tokens(company_name)
        if not tokens:
            return []
        sets = sorted((self.by_token.get(t, set()) for t in tokens), key=len)
        uuids = set.intersection(*sets) if sets and sets[0] else set()
        found = [self.ads[u] for u in uuids if u in self.ads]
        return sorted(found, key=lambda a: a.get("modified") or "", reverse=True)


def public_token(client: HttpClient) -> str:
    resp = client.get(TOKEN_URL, accept="text/plain", retries=1)
    if resp.status != 200:
        raise ConnectionError(f"NAV public token returned HTTP {resp.status}")
    lines = [line.strip() for line in resp.text().splitlines() if line.strip()]
    token = lines[-1] if lines else ""
    if token.count(".") != 2:
        raise ConnectionError("NAV public token not found in response")
    return token


def build_index(client: HttpClient, token: str, days: int, deadline: float, max_pages: int = 200) -> FeedIndex:
    index = FeedIndex(days)
    since = email.utils.formatdate(time.time() - days * 86400, usegmt=True)
    headers = {"Authorization": f"Bearer {token}", "If-Modified-Since": since}
    url: str | None = FEED_URL
    while url and index.pages < max_pages:
        if time.monotonic() > deadline:
            index.error = "time budget reached while reading the feed"
            return index
        try:
            resp = client.get(url, headers=headers, timeout=60, retries=3)
        except (ConnectionError, BudgetExceeded) as err:
            index.error = str(err)[:200]
            return index
        if resp.status != 200:
            index.error = f"feed page returned HTTP {resp.status}"
            return index
        page = resp.json()
        index.pages += 1
        for item in page.get("items") or []:
            index.add(item)
        next_url = page.get("next_url")
        if not page.get("items") or not next_url:
            index.complete = True
            return index
        url = FEED_BASE + next_url
        headers = {"Authorization": f"Bearer {token}"}
    index.complete = url is None
    return index


def jobs_for_company(client: HttpClient, token: str, index: FeedIndex, orgnr: str, name: str,
                     subunits: set[str]) -> ModuleResult:
    scope = FEED_SCOPE.format(days=index.days)
    candidates = index.candidates(name)
    if not candidates:
        state = NOT_AVAILABLE if index.complete else BLOCKED
        why = f"no {scope} with this employer name" if index.complete else f"feed incomplete: {index.error}"
        return ModuleResult("jobs", state, (), why, FEED_URL)
    wanted = {orgnr, *subunits}
    verified: list[tuple[dict[str, Any], Any]] = []
    for ad in candidates[:MAX_ADS_PER_COMPANY * 2]:
        if len(verified) >= MAX_ADS_PER_COMPANY:
            break
        try:
            resp = client.get(FEED_BASE + ad["url"], headers={"Authorization": f"Bearer {token}"}, retries=1)
        except BudgetExceeded:
            return ModuleResult("jobs", BLOCKED, (), "run request budget exhausted", FEED_URL)
        except ConnectionError as err:
            return ModuleResult("jobs", FAILED, (), str(err)[:300], FEED_URL)
        if resp.status != 200:
            continue
        content = (resp.json() or {}).get("ad_content") or {}
        employer = content.get("employer") or {}
        if employer.get("orgnr") in wanted:
            verified.append((_posting(content, employer), resp))
    if not verified:
        return ModuleResult("jobs", NOT_AVAILABLE, (), f"ads with a similar employer name in the {scope} "
                            "belong to other org numbers", FEED_URL)
    return ModuleResult("jobs", AVAILABLE, _facts(verified, orgnr), None, FEED_URL, verified[0][1].fetched_at,
                        {"employer_homepages": [p["employer_homepage"] for p, _ in verified
                                                if p.get("employer_homepage")]})


def _posting(content: dict[str, Any], employer: dict[str, Any]) -> dict[str, Any]:
    location = next(iter(content.get("workLocations") or []), {})
    return {
        "title": (content.get("title") or "")[:200],
        "job_title": content.get("jobtitle"),
        "published": (content.get("published") or "")[:10] or None,
        "expires": (content.get("expires") or "")[:10] or None,
        "location": location.get("city") or location.get("municipal"),
        "positions": content.get("positioncount"),
        "url": _ad_url(content),
        "employer_orgnr": employer.get("orgnr"),
        "employer_homepage": employer.get("homepage"),
        "ad_id": content.get("uuid"),
    }


def _ad_url(content: dict[str, Any]) -> str | None:
    """The single NAV ad. `sourceurl` can be the employer's careers landing page, so it is never used."""
    link = content.get("link") or ""
    if link.startswith(AD_URL_PREFIX):
        return link
    return AD_URL_PREFIX + content["uuid"] if content.get("uuid") else None


def _facts(verified: list[tuple[dict[str, Any], Any]], orgnr: str) -> tuple[Fact, ...]:
    ordered = sorted(verified, key=lambda v: (v[0]["published"] or "", v[0]["ad_id"] or ""), reverse=True)
    first, first_resp = ordered[0]
    source = Source(SOURCE_NAME, first_resp.url, first.get("url"), PUBLIC_JOBS, first_resp.sha256)
    evidence = {"matched_orgnr": orgnr, "identity": "employer_orgnr_on_ad",
                "ads": [{"url": r.url, "content_sha256": r.sha256, "employer_orgnr": p["employer_orgnr"]}
                        for p, r in ordered]}
    postings = [{k: v for k, v in p.items() if k != "employer_homepage"} for p, _ in ordered]
    span = f"employer orgnr {first['employer_orgnr']}: {first['title']}"
    return (
        make_fact("job_postings", postings, source, first_resp.fetched_at, label="Active job postings (NAV)",
                  json_path="ad_content.employer.orgnr", as_of=first["published"],
                  as_of_basis="latest_published_date", extra_evidence=evidence, claim_span=span),
        make_fact("hiring", True, source, first_resp.fetched_at, label="Currently advertising jobs",
                  as_of=first["published"], as_of_basis="latest_published_date", extra_evidence=evidence,
                  claim_span=span, extraction_method="derived_from_job_postings"),
    )
