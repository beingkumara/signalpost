"""Company-owned website crawl with an exact-entity publication gate.

- Registry-listed site: publish when the org number (high) or registered name (medium) is on a crawled page.
- Discovered site (guessed domain or job-ad homepage): publish only with the org number on a crawled page.
Anything else is `ambiguous` (registry site) or rejected (discovered site). No content is published otherwise.
"""

from __future__ import annotations

import threading
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from signalpost import config
from signalpost.facts import COMPANY_OWNED, Fact, Source, make_fact
from signalpost.http_client import BudgetExceeded, HttpClient, Response
from signalpost.modules import AMBIGUOUS, AVAILABLE, BLOCKED, FAILED, NOT_AVAILABLE, ModuleResult
from signalpost.webparse import (Page, classify_link, dated_items, emails, name_span, orgnr_span, parse_page,
                                 phones, registrable_domain, same_site, social_links)

PAGE_PRIORITY = ("contact", "about", "careers", "news")


class RobotsCache:
    """One robots.txt fetch per host per run."""

    def __init__(self) -> None:
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def allowed(self, client: HttpClient, url: str) -> bool:
        parsed = urlparse(url)
        key = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            known = key in self._cache
            parser = self._cache.get(key)
        if not known:
            parser = self._fetch(client, key)
            with self._lock:
                self._cache[key] = parser
        return True if parser is None else parser.can_fetch(config.USER_AGENT, url)

    @staticmethod
    def _fetch(client: HttpClient, base: str) -> urllib.robotparser.RobotFileParser | None:
        try:
            resp = client.get(base + "/robots.txt", accept="text/plain", timeout=config.WEBSITE_TIMEOUT_SECONDS,
                              max_bytes=200_000, retries=0, untrusted=True)
        except ConnectionError:
            return None
        if resp.status != 200:
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(resp.text().splitlines())
        return parser


ROBOTS = RobotsCache()


@dataclass
class _Crawl:
    pages: list[tuple[Page, Response]] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


def crawl_site(client: HttpClient, start_url: str, orgnr: str, name: str, *, discovered: bool,
               extra_pages: int, origin: str = "registry") -> ModuleResult:
    module = "website"
    try:
        crawl = _crawl(client, start_url, extra_pages, orgnr)
    except ConnectionError as err:
        return ModuleResult(module, FAILED, (), f"website unreachable: {err}"[:300], start_url)
    except BudgetExceeded:
        return ModuleResult(module, BLOCKED, (), "run request budget exhausted", start_url)
    if not crawl.pages:
        state = BLOCKED if crawl.blocked else NOT_AVAILABLE
        reason = "; ".join(crawl.blocked) or "no HTML page returned"
        return ModuleResult(module, state, (), reason, start_url)

    home_page, home_resp = crawl.pages[0]
    proof = _identity(crawl.pages, orgnr, name)
    if proof["level"] == "orgnr_on_page" or (proof["level"] == "name_on_page" and not discovered):
        facts = _facts(crawl.pages, proof, orgnr, start_url, origin)
        return ModuleResult(module, AVAILABLE, facts, None, home_resp.final_url, home_resp.fetched_at,
                            {"identity": proof, "pages": [p.url for p, _ in crawl.pages]})
    if discovered:
        return ModuleResult(module, NOT_AVAILABLE, (), f"candidate {start_url} rejected: org number not on site",
                            start_url, home_resp.fetched_at)
    return ModuleResult(module, AMBIGUOUS, (), f"{home_resp.final_url} shows neither the org number nor the "
                        "registered name; website content not published", home_resp.final_url, home_resp.fetched_at)


def _crawl(client: HttpClient, start_url: str, extra_pages: int, orgnr: str) -> _Crawl:
    crawl = _Crawl()
    if not ROBOTS.allowed(client, start_url):
        crawl.blocked.append(f"robots.txt disallows {start_url}")
        return crawl
    home = _fetch_page(client, start_url)
    if home is None:
        return crawl
    crawl.pages.append(home)
    targets = _pick_pages(home[0], extra_pages, orgnr_found=bool(orgnr_span(home[0], orgnr)))
    for url in targets:
        if not ROBOTS.allowed(client, url):
            crawl.blocked.append(f"robots.txt disallows {url}")
            continue
        try:
            page = _fetch_page(client, url)
        except ConnectionError:
            continue
        if page is not None:
            crawl.pages.append(page)
    return crawl


def _fetch_page(client: HttpClient, url: str) -> tuple[Page, Response] | None:
    resp = client.get(url, accept="text/html,application/xhtml+xml", timeout=config.WEBSITE_TIMEOUT_SECONDS,
                      max_bytes=config.WEBSITE_MAX_BYTES, retries=0, untrusted=True)
    if resp.status >= 500 or resp.status == 429:
        raise ConnectionError(f"{url} returned HTTP {resp.status}")
    if resp.status != 200 or "html" not in resp.headers.get("content-type", "text/html"):
        return None
    return parse_page(resp.text(), resp.final_url), resp


def _pick_pages(home: Page, limit: int, *, orgnr_found: bool) -> list[str]:
    """Contact page first when identity is unproven, then about, careers, news."""
    by_kind: dict[str, str] = {}
    for url, text in home.links:
        if not url.startswith(("http://", "https://")) or not same_site(url, home.url):
            continue
        if url.split("#")[0].rstrip("/") == home.url.split("#")[0].rstrip("/"):
            continue
        kind = classify_link(url, text)
        if kind and kind not in by_kind:
            by_kind[kind] = url.split("#")[0]
    order = PAGE_PRIORITY if not orgnr_found else ("careers", "news", "contact", "about")
    return [by_kind[k] for k in order if k in by_kind][:limit]


def _identity(pages: list[tuple[Page, Response]], orgnr: str, name: str) -> dict[str, Any]:
    for page, resp in pages:
        span = orgnr_span(page, orgnr)
        if span:
            return {"level": "orgnr_on_page", "proof_url": resp.final_url, "claim_span": span,
                    "proof_sha256": resp.sha256}
    for page, resp in pages:
        span = name_span(page, name)
        if span:
            return {"level": "name_on_page", "proof_url": resp.final_url, "claim_span": span,
                    "proof_sha256": resp.sha256}
    return {"level": "unverified"}


def _facts(pages: list[tuple[Page, Response]], proof: dict[str, Any], orgnr: str, start_url: str,
           origin: str) -> tuple[Fact, ...]:
    confidence = "high" if proof["level"] == "orgnr_on_page" else "medium"
    guard = {"matched_orgnr": orgnr, "identity": proof["level"], "proof_url": proof["proof_url"],
             "website_origin": origin}
    home, home_resp = pages[0]

    def src(resp: Response) -> Source:
        return Source(config.SOURCE_WEBSITE, resp.final_url, None, COMPANY_OWNED, resp.sha256)

    def fact(name: str, value: Any, resp: Response, label: str, span: str | None, method: str = "html_parse",
             conf: str = confidence) -> Fact:
        return make_fact(name, value, src(resp), resp.fetched_at, label=label, confidence=conf,
                         extra_evidence=guard, claim_span=span, extraction_method=method)

    proof_resp = next((r for _, r in pages if r.final_url == proof["proof_url"]), home_resp)
    site = {"url": start_url, "final_url": home_resp.final_url,
            "domain": registrable_domain(urlparse(home_resp.final_url).netloc),
            "verification": proof["level"], "origin": origin}
    out = [fact("official_website", site, proof_resp, "Verified official website", proof.get("claim_span"),
                "identity_gate")]
    brand = home.site_name or next((str(o["name"]) for o in home.jsonld
                                    if o.get("@type") in ("Organization", "Corporation", "LocalBusiness")
                                    and o.get("name")), None)
    if brand:
        out.append(fact("brand_name", brand[:120], home_resp, "Public brand name on website", brand, "meta_og"))
    if home.title:
        out.append(fact("website_title", home.title[:300], home_resp, "Website title", home.title, "html_title"))
    if home.description:
        out.append(fact("website_description", home.description[:600], home_resp, "Self-description on website",
                        home.description[:300], "meta_description"))
    _collect(out, pages, fact, home_resp)
    return tuple(out)


def _collect(out: list[Fact], pages: list[tuple[Page, Response]], fact: Any, home_resp: Response) -> None:
    all_emails: list[str] = []
    all_phones: list[str] = []
    social: dict[str, str] = {}
    news: list[dict[str, str]] = []
    careers_url: str | None = None
    email_resp = phone_resp = social_resp = news_resp = home_resp
    for page, resp in pages:
        found = [e for e in emails(page, home_resp.final_url) if e not in all_emails]
        if found and not all_emails:
            email_resp = resp
        all_emails += found
        numbers = [p for p in phones(page) if p not in all_phones]
        if numbers and not all_phones:
            phone_resp = resp
        all_phones += numbers
        links = {k: v for k, v in social_links(page).items() if k not in social}
        if links and not social:
            social_resp = resp
        social.update(links)
        items = dated_items(page)
        if items and not news:
            news_resp = resp
        news += items
        for url, text in page.links:
            if careers_url is None and same_site(url, home_resp.final_url) and classify_link(url, text) == "careers":
                careers_url = url.split("#")[0]
    if all_emails:
        out.append(fact("website_emails", all_emails[:5], email_resp, "Contact emails on website",
                        ", ".join(all_emails[:5]), "mailto_links"))
    if all_phones:
        out.append(fact("website_phones", all_phones[:5], phone_resp, "Phone numbers on website",
                        ", ".join(all_phones[:5]), "tel_links"))
    if social:
        out.append(fact("social_profiles", dict(sorted(social.items())), social_resp,
                        "Company-owned social profiles linked from the verified website",
                        " ".join(sorted(social.values())), "outbound_links"))
    if careers_url:
        out.append(fact("careers_page", careers_url, home_resp, "Careers page on website", careers_url,
                        "link_classifier"))
    if news:
        unique = {(i["date"], i["title"]): i for i in news}
        items = sorted(unique.values(), key=lambda i: i["date"], reverse=True)[:10]
        out.append(fact("website_news", items, news_resp, "Dated news and updates on the company website",
                        f"{items[0]['date']} {items[0]['title']}", "time_tags"))
