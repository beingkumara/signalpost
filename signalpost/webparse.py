"""HTML parsing for company websites: metadata, links, JSON-LD, dated news, identity proof."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import ParseResult, urljoin, urlparse

from signalpost.orgnr import checksum_ok

LEGAL_SUFFIXES = {"AS", "ASA", "ANS", "DA", "SA", "ENK", "NUF", "BA", "KS", "SE", "IKS", "STIFTELSE", "SF", "KF"}
SOCIAL_DOMAINS = {"linkedin.com": "linkedin", "facebook.com": "facebook", "instagram.com": "instagram",
                  "x.com": "x", "twitter.com": "x", "youtube.com": "youtube", "tiktok.com": "tiktok"}
_SHARE_SEGMENTS = {"sharer", "sharer.php", "share", "share.php", "intent", "sharearticle", "dialog"}
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_HEADINGS = {"h1", "h2", "h3", "h4"}

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
    articles: list[tuple[str, str, str]] = field(default_factory=list)
    _in_title: bool = False
    _skip: int = 0
    _in_jsonld: bool = False
    _jsonld_buf: list[str] = field(default_factory=list)
    _anchor: list[str] | None = None
    _anchor_href: str = ""
    _pending_time: str | None = None
    _open_articles: list[dict[str, Any]] = field(default_factory=list)
    _heading: list[str] | None = None

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
            key = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
                if key == "datepublished":
                    self._article_date(a["content"])
        elif tag == "a" and a.get("href"):
            self._anchor, self._anchor_href = [], a["href"].strip()
        elif tag == "time" and a.get("datetime") and not _modified_stamp(a):
            self._pending_time = a["datetime"]
            self._article_date(a["datetime"])
        elif tag == "article":
            self._open_articles.append({"date": None, "title": None, "href": None, "fallback": None})
        elif tag in _HEADINGS and self._open_articles:
            self._heading = []

    def _article_date(self, stamp: str) -> None:
        if self._open_articles and not self._open_articles[-1]["date"]:
            self._open_articles[-1]["date"] = stamp

    def _close_anchor(self) -> None:
        text = " ".join(" ".join(self._anchor or []).split())
        self.links.append((self._anchor_href, text))
        self._anchor = None
        if not self._open_articles:
            return
        article = self._open_articles[-1]
        if self._heading is not None and not article["href"]:
            article["href"] = self._anchor_href
        elif article["fallback"] is None and len(text.split()) >= 3:
            article["fallback"] = (text, self._anchor_href)

    def _close_article(self) -> None:
        self._heading = None
        article = self._open_articles.pop()
        fallback = article["fallback"] or (None, None)
        title = article["title"] or fallback[0]
        href = article["href"] or fallback[1]
        if article["date"] and title and href:
            self.articles.append((article["date"], title, href))

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._jsonld_buf))
        elif tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._anchor is not None:
            self._close_anchor()
        elif tag in _HEADINGS and self._heading is not None:
            if self._open_articles and not self._open_articles[-1]["title"]:
                self._open_articles[-1]["title"] = " ".join(" ".join(self._heading).split()) or None
                if self._anchor is not None and not self._open_articles[-1]["href"]:  # <a><h2>..</h2></a>
                    self._open_articles[-1]["href"] = self._anchor_href
            self._heading = None
        elif tag == "article" and self._open_articles:
            self._close_article()

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        elif self._in_title:
            self.title += data
        elif not self._skip:
            self.text_parts.append(data)
            if self._anchor is not None:
                self._anchor.append(data)
            if self._heading is not None:
                self._heading.append(data)
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
    articles: tuple[tuple[str, str, str], ...] = ()

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
    while parser._open_articles:  # unterminated <article> blocks
        parser._close_article()
    absolute = tuple((joined, text) for href, text in parser.links if (joined := _join(url, href)))
    text = " ".join(" ".join(parser.text_parts).split())
    articles = tuple((stamp, title, joined) for stamp, title, href in parser.articles if (joined := _join(url, href)))
    return Page(url, " ".join(parser.title.split()), dict(parser.meta), text, absolute,
                tuple(_jsonld_objects(parser.jsonld)), tuple(parser.times), articles)


def _url(url: str) -> ParseResult:
    """urlparse that never raises: hostile pages carry links such as 'http://[bad'."""
    try:
        return urlparse(url)
    except ValueError:
        return urlparse("")


def _join(base: str, href: str) -> str | None:
    try:
        joined = urljoin(base, href)
        urlparse(joined)
        return joined
    except ValueError:
        return None


def jsonld_types(obj: dict[str, Any]) -> set[str]:
    kind = obj.get("@type")
    return {str(k) for k in kind} if isinstance(kind, list) else {str(kind)} if kind else set()


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
    return registrable_domain(_url(url).netloc) == registrable_domain(_url(base).netloc)


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
              and jsonld_types(o) & {"Organization", "Corporation", "LocalBusiness", "WebSite"}]
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
    label = registrable_domain(_url(url).netloc).split(".")[0].replace("-", "")
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


def careers_platforms(page: Page, owners: Sequence[str]) -> dict[str, str]:
    """Recruitment platforms where this company has its own account (e.g. acme.teamtailor.com).
    Search links (NAV, FINN) and single ads for other employers are skipped."""
    found: dict[str, str] = {}
    for href, _ in page.links:
        parsed = _url(href)
        host = parsed.netloc.lower().split(":")[0].removeprefix("www.")
        for domain, label in ATS_DOMAINS.items():
            if label in found or not (host == domain or host.endswith("." + domain)):
                continue
            account = host[:-len(domain)].rstrip(".")  # acme.teamtailor.com; a path on finn.no is only a search
            if account and not account.isdigit() and handle_matches(account, owners):
                found[label] = href.split("#")[0]
    return found


def job_postings(page: Page, owners: Sequence[str], today: str | None = None) -> list[dict[str, Any]]:
    """schema.org JobPosting ads for this company: a single ad with its own URL (or the only ad on its page),
    not expired, and not hiring for another organisation."""
    today = today or date.today().isoformat()
    ads = [o for o in page.jsonld if "JobPosting" in jsonld_types(o) and o.get("title")]
    jobs: list[dict[str, Any]] = []
    for obj in ads:
        hiring = obj.get("hiringOrganization")
        employer = hiring.get("name") if isinstance(hiring, dict) else hiring if isinstance(hiring, str) else None
        if employer and not handle_matches(str(employer), owners):
            continue
        expires = _iso_date(str(obj.get("validThrough") or ""))
        if expires and expires < today:
            continue
        if not obj.get("url") and len(ads) > 1:
            continue  # a listing page: no way to tell the single ads apart
        location = obj.get("jobLocation")
        location = location[0] if isinstance(location, list) and location else location
        address = (location or {}).get("address") if isinstance(location, dict) else None
        city = address.get("addressLocality") if isinstance(address, dict) else None
        jobs.append({"title": str(obj["title"])[:200], "published": _iso_date(str(obj.get("datePosted") or "")),
                     "expires": expires, "location": city, "url": str(obj.get("url") or page.url)})
    return jobs


_CAREERS_PHRASES = (("karriere",), ("ledige", "stillinger"), ("ledig", "stilling"), ("stillinger",), ("jobb",),
                    ("jobbe",), ("jobs",), ("career",), ("careers",), ("rekruttering",))
_NEWS_PREFIXES = ("nyhet", "news", "aktuelt", "presse", "press", "blogg", "blog", "artikkel", "artikler", "article")
MAX_LABEL_WORDS = 5


def classify_link(url: str, text: str) -> str | None:
    """The kind of page a link points to. Careers and news need whole words, so 'utstillinger' or
    'butiksjobb' are not careers pages and a news story mentioning a job is not the careers page."""
    path = _url(url).path.lower()
    path_tokens, text_tokens = ascii_tokens(path), ascii_tokens(text)
    in_news = any(t.startswith(_NEWS_PREFIXES) for t in path_tokens)
    for kind, words in PAGE_KEYWORDS.items():
        if kind == "careers":
            short_label = len(text_tokens) <= MAX_LABEL_WORDS
            if not in_news and (_has_phrase(path_tokens, _CAREERS_PHRASES)
                                or (short_label and _has_phrase(text_tokens, _CAREERS_PHRASES))):
                return kind
        elif kind == "news":
            if in_news or any(t.startswith(_NEWS_PREFIXES) for t in text_tokens):
                return kind
        elif any(w in f"{path} {text.lower()}" for w in words):
            return kind
    return None


def _has_phrase(tokens: list[str], phrases: tuple[tuple[str, ...], ...]) -> bool:
    return any(tuple(tokens[i:i + len(p)]) == p for p in phrases for i in range(len(tokens)))


def emails(page: Page, site_url: str) -> list[str]:
    domain = registrable_domain(_url(site_url).netloc)
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


_NOT_ACCOUNTS = {
    "facebook": {"profile.php", "people", "groups", "sharer", "sharer.php", "share", "share.php", "dialog", "events",
                 "watch", "photo", "photo.php", "photos", "story.php", "permalink.php", "hashtag", "plugins", "tr"},
    "instagram": {"p", "reel", "reels", "explore", "stories", "tv"},
    "x": {"intent", "share", "hashtag", "home", "search", "i"},
}


def social_account(url: str) -> tuple[str, str, str] | None:
    """(network, handle, canonical URL) for a link to an account page; None for posts, videos and share links.
    A YouTube channel ID has an empty handle: it names no one, so only the company's own structured data can vouch for it."""
    parsed = _url(url)
    host = parsed.netloc.lower().split(":")[0].removeprefix("www.").removeprefix("m.").removeprefix("no.")
    network = next((n for d, n in SOCIAL_DOMAINS.items() if host == d or host.endswith("." + d)), None)
    segs = [s for s in parsed.path.split("/") if s]
    if not network or not segs or any(seg.lower() in _SHARE_SEGMENTS for seg in segs):
        return None
    first = segs[0].lower()
    handle: str | None = None
    if network == "linkedin":
        handle = segs[1] if first in ("company", "school", "showcase") and len(segs) > 1 else None
    elif network == "youtube":
        handle = (segs[0][1:] if first.startswith("@") else segs[1] if first in ("c", "user") and len(segs) > 1
                  else "" if first == "channel" and len(segs) > 1 else None)
    elif network == "tiktok":
        handle = segs[0][1:] if first.startswith("@") and len(segs) == 1 else None
    elif network == "facebook" and first == "pages":
        handle = segs[1] if len(segs) > 1 else None
    elif first not in _NOT_ACCOUNTS.get(network, set()):
        handle = segs[0]
    if handle is None:
        return None
    keep = segs[:2] if network in ("linkedin", "youtube") and first not in ("pages",) and not first.startswith("@") \
        else segs[:1] if network != "facebook" or first != "pages" else segs[:3]
    return network, handle, f"{parsed.scheme}://{parsed.netloc}/{'/'.join(keep)}"


def social_links(page: Page, owners: Sequence[str]) -> dict[str, str]:
    """One account per network whose handle matches this company. Footer links to a parent group, the web
    agency or the website theme (facebook.com/Wix) do not match and are skipped."""
    candidates = [(href, False) for href, _ in page.links]
    for obj in page.jsonld:
        same_as = obj.get("sameAs")
        own = _is_owner_org(obj, owners)
        urls = same_as if isinstance(same_as, list) else [same_as] if isinstance(same_as, str) else []
        candidates += [(str(u), own) for u in urls]
    found: dict[str, str] = {}
    for url, own in candidates:
        account = social_account(url)
        if account is None or account[0] in found:
            continue
        network, handle, canonical = account
        if (handle and handle_matches(handle, owners)) or (not handle and own):
            found[network] = canonical
    return found


def _is_owner_org(obj: dict[str, Any], owners: Sequence[str]) -> bool:
    if not jsonld_types(obj) & {"Organization", "Corporation", "LocalBusiness"}:
        return False
    return any(handle_matches(str(obj.get(k)), owners) for k in ("legalName", "name") if obj.get(k))


def handle_matches(handle: str, owners: Sequence[str]) -> bool:
    """True when an account handle or employer name belongs to this company. Same rule as the organiser's
    starter kit: the whole legal name, a single-word name, or at least 75% of the name's words (two or
    more) appear in it; or it contains the verified site's domain label ('acmebyggas', 'uppigardnatadal')."""
    flat = "".join(ascii_tokens(handle))
    return len(flat) >= 3 and any(_owner_matches(flat, owner) for owner in owners)


_NAME_CONNECTORS = {"og", "and", "the", "of", "i"}
MIN_WORD_SHARE = 0.75


def _owner_matches(flat: str, owner: str) -> bool:
    if "." in owner and " " not in owner.strip():  # a verified domain such as acmebygg.no
        label = "".join(ascii_tokens(registrable_domain(_url(owner).netloc or owner).split(".")[0]))
        return len(label) >= 4 and label in flat
    tokens = [t for t in ascii_tokens(core_name(owner) or owner) if len(t) > 1 and t not in _NAME_CONNECTORS]
    joined = "".join(tokens)
    if len(joined) >= 4 and joined in flat:
        return True
    if len(tokens) == 1:
        return False  # the single word is the whole name, already checked
    matched = {t for t in tokens if len(t) >= 3 and t in flat}
    return len(matched) >= 2 and len(matched) / len(set(tokens)) >= MIN_WORD_SHARE


_ORGNR_LABELLED = re.compile(r"(?:(?i:org(?:anisasjons)?\.?\s*-?\s*(?:nr|nummer|no)\b\.?|organisasjonsnummer|"
                             r"foretaksregisteret)|\bNO)\s*[:.]?\s*(\d{3})[\s.]?(\d{3})[\s.]?(\d{3})(?!\d)")


def orgnrs_on_page(page: Page) -> set[str]:
    """Organisation numbers the page states as such (labelled 'Org.nr', 'NO ... MVA' or in JSON-LD tax IDs)."""
    found = {"".join(m.groups()) for m in _ORGNR_LABELLED.finditer(page.text)}
    for obj in page.jsonld:
        for key in ("taxID", "vatID", "identifier"):
            value = obj.get(key)
            value = value.get("value") if isinstance(value, dict) else value
            digits = re.sub(r"\D", "", str(value or ""))
            if len(digits) == 9:
                found.add(digits)
    return {n for n in found if checksum_ok(n)}


# Directories, registries, media and platforms: never a company's own website.
NOT_COMPANY_SITES = {
    "proff.no", "proff.dk", "proff.se", "purehelp.no", "1881.no", "gulesider.no", "brreg.no", "forvalt.no",
    "regnskapstall.no", "firmasok.no", "bizzdo.no", "finn.no", "enirogule.no", "allabolag.se", "northdata.com",
    "northdata.de", "opencorporates.com", "dnb.com", "kompass.com", "infobel.com", "cylex.no", "bedriftsregisteret.no",
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
    "wikipedia.org", "google.com", "bing.com", "yelp.com", "tripadvisor.com", "tripadvisor.no", "trustpilot.com",
    "nav.no", "arbeidsplassen.nav.no", "e24.no", "dn.no", "nrk.no", "vg.no", "aftenposten.no", "tu.no",
    "finansavisen.no", "kapital.no", "mittanbud.no", "anbudstorget.no", "bygg.no", "legelisten.no", "hjemla.no",
    "eiendomspriser.no", "krak.dk", "hitta.se", "eniro.se", "ratsit.se", "roaring.io", "bisnode.no", "creditsafe.com",
    "infotorg.no", "regnskapstall.com", "byndle.no", "riksadvokaten.no", "lovdata.no", "kunngjoringer.no", "doffin.no",
    "glassdoor.com", "indeed.com", "jobbnorge.no", "karrierestart.no", "webcruiter.no", "teamtailor.com",
    "amazon.com", "ebay.com",
}
_HOME_SEGMENTS = {"hjem", "home", "forside", "start", "index.html", "index.php", "index.htm", "default.aspx"}


def not_company_site(url: str) -> bool:
    host = _url(url).netloc.lower().split(":")[0].removeprefix("www.")
    return registrable_domain(host) in NOT_COMPANY_SITES or host in NOT_COMPANY_SITES


def hosted_listing(url: str) -> bool:
    """A page deep inside another site (sjekkpunkt.no/bilverksted/x, minskole.no/skole), not a site's home.
    Language and home segments such as /no-no or /Hjem still count as a home page."""
    segs = [s.lower() for s in _url(url).path.split("/") if s]
    return any(not re.fullmatch(r"[a-z]{2}([-_][a-z]{2})?", s) and s not in _HOME_SEGMENTS for s in segs)


_MONTHS = ("januar", "februar", "mars", "april", "mai", "juni", "juli", "august", "september", "oktober", "november",
           "desember", "january", "february", "march", "may", "june", "july", "october", "december", "jan", "feb",
           "mar", "apr", "jun", "jul", "aug", "sep", "sept", "okt", "oct", "nov", "des", "dec")
_MENU_PREFIXES = ("les mer", "read more", "se mer", "see more", "vis mer", "show more", "se flere", "se alle",
                  "vis alle", "vis flere", "flere ", "alle ", "more ", "view all", "see all")
_POST_ONLY_KINDS = {"contact", "about", "legal", "careers"}
MIN_HEADLINE_CHARS = 12
_MENU_WORDS = {"kontakt", "contact", "nyheter", "nyhet", "news", "aktuelt", "blogg", "blog", "presse", "press",
               "karriere", "career", "careers", "jobb", "jobs", "ledige", "stillinger", "personvern", "privacy",
               "cookies", "vilkar", "terms", "om", "about", "hjem", "home", "meny", "menu", "oss", "us", "var", "vare",
               "alle", "flere", "se", "mer", "more", "hos", "med", "og", "i", "the", "our", "ta", "skriv", "til"}


def dated_items(page: Page, limit: int = 10, today: str | None = None) -> list[dict[str, str]]:
    """Real, dated posts: an <article> with a headline linking to the post, a post page with
    article:published_time, or NewsArticle/BlogPosting markup. Only publish dates count, never 'modified'."""
    today = today or date.today().isoformat()
    candidates = [(stamp, title, href) for stamp, title, href in page.articles]
    if page.meta.get("og:type", "").lower() == "article" and page.meta.get("article:published_time"):
        candidates.append((page.meta["article:published_time"], page.meta.get("og:title") or page.title, page.url))
    for obj in page.jsonld:
        kinds = jsonld_types(obj)
        url = str(obj.get("url") or page.url)
        is_post = kinds & {"NewsArticle", "BlogPosting"} or ("Article" in kinds and _news_path(url))
        if is_post and obj.get("datePublished"):
            candidates.append((str(obj["datePublished"]), str(obj.get("headline") or ""), url))
    items: list[dict[str, str]] = []
    for stamp, title, url in candidates:
        day = _iso_date(stamp)
        headline = " ".join(title.split())[:200]
        if day and day <= today and is_headline(headline) and _post_url(url, page.url):
            items.append({"date": day, "title": headline, "url": url})
    unique = {(i["date"], i["title"]): i for i in items}
    return sorted(unique.values(), key=lambda i: i["date"], reverse=True)[:limit]


def is_headline(text: str) -> bool:
    """A post title, not a date ('25. oktober 2015') or a menu label ('Se flere nyheter', 'Kontakt oss')."""
    lowered = text.lower().strip()
    words = ascii_tokens(lowered)
    if len(lowered) < MIN_HEADLINE_CHARS or len(words) < 2 or lowered.startswith(_MENU_PREFIXES):
        return False
    if all(w.isdigit() or w in _MONTHS for w in words):
        return False
    return not (len(words) <= 4 and all(w in _MENU_WORDS for w in words))


def _post_url(url: str, page_url: str) -> bool:
    if not same_site(url, page_url):
        return False
    segs = [s for s in _url(url).path.lower().split("/") if s]
    if not segs or (not _news_path(url) and classify_link("/" + segs[-1], "") in _POST_ONLY_KINDS):
        return False
    return not (len(segs) == 1 and any(ascii_tokens(segs[0]) == [p] for p in _NEWS_PREFIXES + ("nyheter",)))


def _news_path(url: str) -> bool:
    path = _url(url).path.lower()
    return bool(re.search(r"/20\d\d/", path)) or any(t.startswith(_NEWS_PREFIXES) for t in ascii_tokens(path))


def _modified_stamp(attrs: dict[str, str]) -> bool:
    marker = f"{attrs.get('class', '')} {attrs.get('itemprop', '')}".lower()
    return "updated" in marker or "modified" in marker


def _iso_date(value: str) -> str | None:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value.strip())
    if not match:
        return None
    try:
        return date(*(int(g) for g in match.groups())).isoformat()
    except ValueError:  # 2026-02-31
        return None
