"""HTML parsing for company websites: metadata, links, JSON-LD, dated news, identity proof."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

LEGAL_SUFFIXES = {"AS", "ASA", "ANS", "DA", "SA", "ENK", "NUF", "BA", "KS", "SE", "IKS", "STIFTELSE", "SF", "KF"}
SOCIAL_DOMAINS = {"linkedin.com": "linkedin", "facebook.com": "facebook", "instagram.com": "instagram",
                  "x.com": "x", "twitter.com": "x", "youtube.com": "youtube", "tiktok.com": "tiktok"}
_SHARE_HINTS = ("sharer", "/share", "intent/", "sharearticle", "shareArticle", "/dialog/")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}

PAGE_KEYWORDS = {
    "contact": ("kontakt", "contact"),
    "about": ("om-oss", "om oss", "omoss", "about", "hvem-er-vi", "selskapet"),
    "legal": ("personvern", "privacy", "vilkar", "vilkår", "terms", "salgsbetingelser", "cookie", "impressum"),
    "careers": ("karriere", "ledige-stillinger", "ledige stillinger", "jobb", "career", "jobs", "stillinger"),
    "news": ("nyheter", "news", "aktuelt", "presse", "press", "blogg", "blog"),
}


@dataclass
class _Parser(HTMLParser):
    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    links: list[tuple[str, str]] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    jsonld: list[str] = field(default_factory=list)
    times: list[tuple[str, str]] = field(default_factory=list)
    _in_title: bool = False
    _skip: int = 0
    _in_jsonld: bool = False
    _jsonld_buf: list[str] = field(default_factory=list)
    _anchor: list[str] | None = None
    _anchor_href: str = ""
    _pending_time: str | None = None

    def __post_init__(self) -> None:
        super().__init__(convert_charrefs=True)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_jsonld, self._jsonld_buf = True, []
        elif tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "a" and a.get("href"):
            self._anchor, self._anchor_href = [], a["href"].strip()
        elif tag == "time" and a.get("datetime"):
            self._pending_time = a["datetime"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._jsonld_buf))
        elif tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._anchor is not None:
            self.links.append((self._anchor_href, " ".join(" ".join(self._anchor).split())))
            self._anchor = None

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        elif self._in_title:
            self.title += data
        elif not self._skip:
            self.text_parts.append(data)
            if self._anchor is not None:
                self._anchor.append(data)
            if self._pending_time and data.strip():
                self.times.append((self._pending_time, data.strip()))
                self._pending_time = None


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    meta: dict[str, str]
    text: str
    links: tuple[tuple[str, str], ...]
    jsonld: tuple[dict[str, Any], ...]
    times: tuple[tuple[str, str], ...]

    @property
    def description(self) -> str | None:
        return self.meta.get("description") or self.meta.get("og:description")

    @property
    def site_name(self) -> str | None:
        return self.meta.get("og:site_name")


def parse_page(html: str, url: str) -> Page:
    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed markup: keep whatever was parsed so far
        pass
    if parser._anchor is not None:  # unterminated <a> at end of document
        parser.links.append((parser._anchor_href, " ".join(" ".join(parser._anchor).split())))
    absolute = tuple((urljoin(url, href), text) for href, text in parser.links)
    text = " ".join(" ".join(parser.text_parts).split())
    return Page(url, " ".join(parser.title.split()), dict(parser.meta), text, absolute,
                tuple(_jsonld_objects(parser.jsonld)), tuple(parser.times))


def _jsonld_objects(blocks: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in blocks:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                    continue
                out.append(item)
    return out


def registrable_domain(host: str) -> str:
    host = host.lower().split(":")[0].removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def same_site(url: str, base: str) -> bool:
    return registrable_domain(urlparse(url).netloc) == registrable_domain(urlparse(base).netloc)


def core_name(name: str) -> str:
    tokens = [t for t in re.split(r"\s+", name.strip()) if t]
    while tokens and tokens[-1].upper().strip(".") in LEGAL_SUFFIXES:
        tokens.pop()
    core = " ".join(tokens)
    return core if len(core) >= 3 else ""


def orgnr_span(page: Page, orgnr: str) -> str | None:
    """Return a text snippet proving the org number is on the page, or None."""
    spaced = r"[\s. -]?".join(orgnr[i:i + 3] for i in (0, 3, 6))
    pattern = re.compile(rf"(?<!\d){spaced}(?!\d)")
    for haystack in (page.text, " ".join(json.dumps(o, ensure_ascii=False) for o in page.jsonld)):
        match = pattern.search(haystack)
        if match:
            start = max(0, match.start() - 60)
            return haystack[start:match.end() + 40].strip()
    return None


GENERIC_NAME_WORDS = {"bygg", "eiendom", "holding", "invest", "service", "services", "consult", "consulting",
                      "norge", "norway", "group", "gruppen", "transport", "handel", "drift", "utvikling", "regnskap",
                      "elektro", "maskin", "design", "media", "data", "it", "tech", "solutions", "partner", "partners",
                      "og", "and", "i", "the", "sameie", "borettslag", "eiendomsselskap", "nordic", "norden"}


def distinctive_name(name: str) -> bool:
    """Names that are too short or generic cannot prove identity on their own."""
    tokens = [t for t in re.split(r"[^\wæøå]+", core_name(name).lower()) if t]
    specific = [t for t in tokens if t not in GENERIC_NAME_WORDS and not t.isdigit()]
    return bool(specific) and len("".join(specific)) >= 5


def prominent_text(page: Page) -> str:
    """Where a site names its owner: title, site name, structured-data names and copyright lines."""
    parts = [page.title, page.site_name or ""]
    parts += [str(o.get(k)) for o in page.jsonld for k in ("name", "legalName") if o.get(k)
              and str(o.get("@type", "")) in ("Organization", "Corporation", "LocalBusiness", "WebSite")]
    parts += [m.group(0) for m in re.finditer(r"(©|\(c\)|copyright)[^|.\n]{0,60}", page.text, flags=re.IGNORECASE)]
    return " | ".join(p for p in parts if p)


def ascii_tokens(text: str) -> list[str]:
    lowered = text.lower()
    for src, dst in (("æ", "ae"), ("ø", "o"), ("å", "a"), ("&", " og ")):
        lowered = lowered.replace(src, dst)
    return [t for t in re.split(r"[^a-z0-9]+", lowered) if t]


def name_in_title(page: Page, name: str) -> bool:
    core = core_name(name)
    head = f"{page.title} | {page.site_name or ''}"
    return bool(core) and re.search(r"(?<!\w)" + re.escape(core) + r"(?!\w)", head, flags=re.IGNORECASE) is not None


def domain_matches_name(url: str, name: str) -> bool:
    """True when the site's domain contains a distinctive word of the legal name (e.g. pellybutik.se for Pelly)."""
    label = registrable_domain(urlparse(url).netloc).split(".")[0].replace("-", "")
    return any(len(t) >= 4 and t not in GENERIC_NAME_WORDS and t in label for t in ascii_tokens(core_name(name)))


def name_span(page: Page, name: str) -> str | None:
    """The registered name in a prominent place on the page, for distinctive names only."""
    core = core_name(name)
    if not core or not distinctive_name(name):
        return None
    haystack = prominent_text(page)
    match = re.search(r"(?<!\w)" + re.escape(core) + r"(?!\w)", haystack, flags=re.IGNORECASE)
    if not match:
        return None
    start = max(0, match.start() - 40)
    return haystack[start:match.end() + 40].strip()


ATS_DOMAINS = {"teamtailor.com": "Teamtailor", "webcruiter.no": "Webcruiter", "webcruiter.com": "Webcruiter",
               "jobbnorge.no": "Jobbnorge", "recman.no": "Recman", "recman.page": "Recman", "hrmanager.no": "HR Manager",
               "easycruit.com": "EasyCruit", "reachmee.com": "ReachMee", "varbi.com": "Varbi",
               "myworkdayjobs.com": "Workday", "smartrecruiters.com": "SmartRecruiters", "lever.co": "Lever",
               "greenhouse.io": "Greenhouse", "finn.no": "FINN", "arbeidsplassen.nav.no": "NAV"}


def careers_platforms(page: Page) -> dict[str, str]:
    found: dict[str, str] = {}
    for href, _ in page.links:
        host = urlparse(href).netloc.lower().removeprefix("www.")
        for domain, label in ATS_DOMAINS.items():
            if (host == domain or host.endswith("." + domain)) and label not in found:
                found[label] = href.split("#")[0]
    return found


def job_postings(page: Page) -> list[dict[str, Any]]:
    """schema.org JobPosting objects embedded by the company."""
    jobs: list[dict[str, Any]] = []
    for obj in page.jsonld:
        if str(obj.get("@type", "")) != "JobPosting" or not obj.get("title"):
            continue
        location = obj.get("jobLocation")
        location = location[0] if isinstance(location, list) and location else location
        address = (location or {}).get("address") if isinstance(location, dict) else None
        city = address.get("addressLocality") if isinstance(address, dict) else None
        jobs.append({"title": str(obj["title"])[:200], "published": _iso_date(str(obj.get("datePosted") or "")),
                     "expires": _iso_date(str(obj.get("validThrough") or "")), "location": city,
                     "url": str(obj.get("url") or page.url)})
    return jobs


def classify_link(url: str, text: str) -> str | None:
    target = f"{urlparse(url).path.lower()} {text.lower()}"
    for kind, words in PAGE_KEYWORDS.items():
        if any(w in target for w in words):
            return kind
    return None


def emails(page: Page, site_url: str) -> list[str]:
    domain = registrable_domain(urlparse(site_url).netloc)
    found: list[str] = []
    for href, _ in page.links:
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip().lower()
            if _EMAIL.match(addr) and addr.split("@")[1].endswith(domain) and addr not in found:
                found.append(addr)
    for obj in page.jsonld:
        addr = str(obj.get("email") or "").removeprefix("mailto:").strip().lower()
        if _EMAIL.match(addr) and addr.split("@")[1].endswith(domain) and addr not in found:
            found.append(addr)
    return found


def phones(page: Page) -> list[str]:
    raw = [href[4:] for href, _ in page.links if href.lower().startswith("tel:")]
    raw += [str(o.get("telephone")) for o in page.jsonld if o.get("telephone")]
    found: list[str] = []
    for number in raw:
        normalised = normalise_phone(number)
        if normalised and normalised not in found:
            found.append(normalised)
    return found


def normalise_phone(number: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", number)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+47"):
        digits = digits[3:]
    elif digits.startswith("+"):
        return digits if 8 <= len(digits) <= 16 else None
    return f"+47{digits}" if len(digits) == 8 and digits[0] in "23456789" else None


def social_links(page: Page) -> dict[str, str]:
    urls = [href for href, _ in page.links]
    for obj in page.jsonld:
        same_as = obj.get("sameAs")
        urls.extend(same_as if isinstance(same_as, list) else [same_as] if isinstance(same_as, str) else [])
    found: dict[str, str] = {}
    for url in urls:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.").removeprefix("no.")
        if any(h in url for h in _SHARE_HINTS) or parsed.path in ("", "/"):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if (host == domain or host.endswith("." + domain)) and network not in found:
                found[network] = url.split("?")[0].rstrip("/")
    return found


def dated_items(page: Page, limit: int = 10) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for stamp, text in page.times:
        date = _iso_date(stamp)
        if date and len(text) >= 3:
            items.append({"date": date, "title": text[:200], "url": page.url})
    for obj in page.jsonld:
        kind = str(obj.get("@type", ""))
        if kind in ("NewsArticle", "BlogPosting", "Article") and obj.get("datePublished"):
            date = _iso_date(str(obj["datePublished"]))
            if date:
                items.append({"date": date, "title": str(obj.get("headline") or "")[:200],
                              "url": str(obj.get("url") or page.url)})
    unique = {(i["date"], i["title"]): i for i in items}
    return sorted(unique.values(), key=lambda i: i["date"], reverse=True)[:limit]


def _iso_date(value: str) -> str | None:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value.strip())
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else None
