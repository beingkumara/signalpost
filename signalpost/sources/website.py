"""The company's own website, but only the URL registered in Enhetsregisteret.

Content from the page is published only if the page shows the org number or the
registered name, so a parked or reused domain cannot leak facts about another company.
"""

from __future__ import annotations

import re
import urllib.robotparser
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from signalpost import config
from signalpost.facts import Fact, Source, make_fact
from signalpost.http_client import HttpClient

_LEGAL_SUFFIXES = {"AS", "ASA", "ANS", "DA", "SA", "ENK", "NUF", "BA", "KS", "SE", "IKS", "STIFTELSE"}
_SOCIAL = {"linkedin.com": "linkedin", "facebook.com": "facebook", "instagram.com": "instagram",
           "x.com": "x", "twitter.com": "x", "youtube.com": "youtube"}
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass
class _PageParser(HTMLParser):
    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    _in_title: bool = False
    _skip_depth: int = 0

    def __post_init__(self) -> None:
        super().__init__(convert_charrefs=True)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"].strip()
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"].strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.text_parts.append(data)


@dataclass(frozen=True)
class PageInfo:
    title: str
    description: str | None
    site_name: str | None
    emails: tuple[str, ...]
    social: dict[str, str]
    text: str


def parse_page(html: str, base_url: str) -> PageInfo:
    parser = _PageParser()
    try:
        parser.feed(html)
    except Exception:  # malformed markup: keep whatever was parsed so far
        pass
    description = parser.meta.get("description") or parser.meta.get("og:description")
    emails: list[str] = []
    social: dict[str, str] = {}
    for href in parser.links:
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if _EMAIL.match(addr) and addr.lower() not in emails:
                emails.append(addr.lower())
            continue
        absolute = urljoin(base_url, href)
        host = urlparse(absolute).netloc.lower().removeprefix("www.")
        for domain, network in _SOCIAL.items():
            if (host == domain or host.endswith("." + domain)) and network not in social:
                social[network] = absolute
    text = " ".join(" ".join(parser.text_parts).split())
    return PageInfo(" ".join(parser.title.split()), description, parser.meta.get("og:site_name"),
                    tuple(emails), social, text)


def identity_check(page: PageInfo, orgnr: str, name: str) -> dict[str, Any]:
    """How strongly does this page belong to the company?"""
    haystack = f"{page.title} {page.site_name or ''} {page.description or ''} {page.text}"
    squashed = re.sub(r"[\s. \-]", "", haystack)
    orgnr_found = orgnr in squashed
    core = core_name(name)
    name_found = bool(core) and core.lower() in " ".join(haystack.lower().split())
    level = "orgnr_on_page" if orgnr_found else "name_on_page" if name_found else "unverified"
    return {"level": level, "orgnr_found": orgnr_found, "name_found": name_found, "name_tested": core}


def core_name(name: str) -> str:
    tokens = [t for t in re.split(r"\s+", name.strip()) if t]
    while tokens and tokens[-1].upper().strip(".") in _LEGAL_SUFFIXES:
        tokens.pop()
    core = " ".join(tokens)
    return core if len(core) >= 3 else ""


def fetch_website_facts(client: HttpClient, orgnr: str, name: str, website: str) -> tuple[tuple[Fact, ...], list[str]]:
    """Return (facts, warnings). Network failures on a company site are warnings, not errors."""
    warnings: list[str] = []
    if not _robots_allows(client, website):
        return (), [f"robots.txt disallows fetching {website}; website content skipped"]
    # Unreachable sites raise ConnectionError so the store keeps the previous facts, marked stale.
    resp = client.get(website, accept="text/html,application/xhtml+xml", timeout=config.WEBSITE_TIMEOUT_SECONDS,
                      max_bytes=config.WEBSITE_MAX_BYTES, retries=0)
    if resp.status >= 500 or resp.status == 429:
        raise ConnectionError(f"website {website} returned HTTP {resp.status}")
    if resp.status != 200 or "html" not in resp.headers.get("content-type", "html"):
        return (), [f"website {website} returned HTTP {resp.status}; content not used"]
    page = parse_page(resp.text(), resp.final_url)
    identity = identity_check(page, orgnr, name)
    if identity["level"] == "unverified":
        return (), [f"website {resp.final_url} did not show the org number or name; content not used"]
    return _page_facts(page, identity, resp.final_url, website, resp.fetched_at, orgnr), warnings


def _page_facts(page: PageInfo, identity: dict[str, Any], final_url: str, website: str,
                retrieved_at: str, orgnr: str) -> tuple[Fact, ...]:
    source = Source(config.SOURCE_WEBSITE, final_url)
    confidence = "high" if identity["level"] == "orgnr_on_page" else "medium"
    evidence = {"matched_orgnr": orgnr, "identity": identity, "registered_url": website}
    facts = [make_fact("website_verification", identity["level"], source, retrieved_at,
                       label="Website identity check", extra_evidence=evidence, confidence=confidence)]
    if page.title:
        facts.append(make_fact("website_title", page.title[:300], source, retrieved_at, json_path="html.title",
                               label="Website title", extra_evidence=evidence, confidence=confidence))
    if page.description:
        facts.append(make_fact("website_description", page.description[:600], source, retrieved_at,
                               json_path="html.meta[description]", label="Self-description on website",
                               extra_evidence=evidence, confidence=confidence))
    site_domain = urlparse(final_url).netloc.lower().removeprefix("www.")
    emails = [e for e in page.emails if e.split("@")[1].endswith(_registrable(site_domain))]
    if emails:
        facts.append(make_fact("website_emails", emails[:5], source, retrieved_at, json_path="html.a[mailto]",
                               label="Contact emails on website", extra_evidence=evidence, confidence=confidence))
    if page.social:
        facts.append(make_fact("social_profiles", page.social, source, retrieved_at, json_path="html.a[href]",
                               label="Social media links on website", extra_evidence=evidence, confidence="medium"))
    return tuple(facts)


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _robots_allows(client: HttpClient, website: str) -> bool:
    parsed = urlparse(website)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = client.get(robots_url, accept="text/plain", timeout=config.WEBSITE_TIMEOUT_SECONDS,
                          max_bytes=200_000, retries=0)
    except ConnectionError:
        return True  # no robots.txt reachable: the homepage fetch decides
    if resp.status != 200:
        return True
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(resp.text().splitlines())
    return parser.can_fetch(config.USER_AGENT, website)
