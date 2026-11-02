"""Company-owned website crawl with an exact-entity publication gate.

- Registry-listed site: publish when the org number (high) or registered name (medium) is on a crawled page.
- Discovered site (guessed domain or job-ad homepage): publish only with the org number on a crawled page.
Anything else is `ambiguous` (registry site) or rejected (discovered site). No content is published otherwise.

Ownership checks run first: a directory, registry or social-network URL is never a company website, a
redirect to another domain must land on a site's home page (not a listing on a shared platform), and a
name-only match fails when the site states a different org number (a parent group or brand site).
Social profiles, recruitment platforms and job ads are kept only when their handle or employer names
this company.
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
from signalpost.item_checks import assess_news, assess_site_job, login_url, lowest, parent_group_accounts
from signalpost.modules import (
    AMBIGUOUS,
    AVAILABLE,
    BLOCKED,
    FAILED,
    NOT_AVAILABLE,
    ModuleResult,
)
from signalpost.webparse import (
    Page,
    ascii_tokens,
    careers_platforms,
    classify_link,
    dated_items,
    domain_matches_name,
    emails,
    handle_matches,
    hosted_listing,
    job_postings,
    listed_openings,
    name_in_title,
    name_span,
    not_company_site,
    orgnr_span,
    orgnrs_on_page,
    parse_page,
    phones,
    registrable_domain,
    same_site,
    social_account,
    social_links,
)

PAGE_PRIORITY = ("contact", "about", "legal", "careers", "news")
GUESSED_PATHS = ("/kontakt", "/om-oss")


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
               extra_pages: int, origin: str = "registry", postcode: str | None = None) -> ModuleResult:
    module = "website"
    if not_company_site(start_url):
        return ModuleResult(module, NOT_AVAILABLE, (), f"{start_url} is a directory, registry or social-network "
                            "page, not a company website", start_url)
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
    elsewhere = _foreign_landing(start_url, home_resp.final_url)
    if elsewhere:
        state = NOT_AVAILABLE if discovered else AMBIGUOUS
        return ModuleResult(module, state, (), f"{start_url} redirects to {home_resp.final_url}, {elsewhere}; "
                            "website content not published", home_resp.final_url, home_resp.fetched_at)
    proof = _identity(crawl.pages, orgnr, name)
    if origin.startswith("search_candidate") and proof["level"] == "orgnr_on_page":
        # Anyone can print a public org number (spam, directories). A search result must also be tied to
        # the company by its title, domain or registered postcode, and is capped at medium confidence.
        tied = (name_in_title(home_page, name) or domain_matches_name(home_resp.final_url, name)
                or bool(postcode and any(postcode in p.text for p, _ in crawl.pages)))
        if not tied:
            return ModuleResult(module, NOT_AVAILABLE, (), f"search candidate {start_url} shows the org number "
                                "but is not tied to the company by title, domain or postcode", start_url)
        proof = {**proof, "level": "orgnr_on_page", "search_tie": True}
    if proof["level"] == "orgnr_on_page" or (proof["level"] == "name_on_page" and not discovered):
        if not proof.get("other_orgnrs"):
            _add_careers_page(client, crawl.pages)
        facts, withheld = _facts(crawl.pages, proof, orgnr, name, start_url, origin)
        return ModuleResult(module, AVAILABLE, facts, None, home_resp.final_url, home_resp.fetched_at,
                            {"identity": proof, "pages": [p.url for p, _ in crawl.pages], "withheld": withheld})
    if proof.get("other_orgnrs"):
        numbers = ", ".join(proof["other_orgnrs"])
        why = (f"lists {orgnr} among other org numbers ({numbers}) and its title and domain do not name this company"
               if proof.get("listed_among_others") else f"states org number(s) {numbers} but not {orgnr}")
        return ModuleResult(module, NOT_AVAILABLE if discovered else AMBIGUOUS, (),
                            f"{home_resp.final_url} {why}: likely a parent group or brand site; website content "
                            "not published", home_resp.final_url, home_resp.fetched_at)
    if discovered:
        return ModuleResult(module, NOT_AVAILABLE, (), f"candidate {start_url} rejected: org number not on site",
                            start_url, home_resp.fetched_at)
    return ModuleResult(module, AMBIGUOUS, (), f"{home_resp.final_url} shows neither the org number nor the "
                        "registered name; website content not published", home_resp.final_url, home_resp.fetched_at)


def _foreign_landing(start_url: str, final_url: str) -> str | None:
    """Why a cross-domain redirect does not land on a company's own site, or None when it does."""
    if registrable_domain(urlparse(final_url).netloc) == registrable_domain(urlparse(start_url).netloc):
        return None
    if not_company_site(final_url):
        return "a directory, registry or social-network page"
    if hosted_listing(final_url):
        return "a page on a shared platform rather than a site's home page"
    return None


def url_variants(url: str) -> list[str]:
    """The given URL, then the same site with/without www and over plain http, for flaky hosts."""
    parsed = urlparse(url)
    host = parsed.netloc
    other = host[4:] if host.startswith("www.") else f"www.{host}"
    path = parsed.path or "/"
    variants = [url, f"{parsed.scheme}://{other}{path}", f"http://{host}{path}"]
    return list(dict.fromkeys(variants))


def _fetch_home(client: HttpClient, start_url: str) -> tuple[Page, Response] | None:
    last: ConnectionError | None = None
    for url in url_variants(start_url):
        try:
            return _fetch_page(client, url)
        except ConnectionError as err:
            last = err
    raise last or ConnectionError(f"{start_url} unreachable")


def _crawl(client: HttpClient, start_url: str, extra_pages: int, orgnr: str) -> _Crawl:
    crawl = _Crawl()
    if not ROBOTS.allowed(client, start_url):
        crawl.blocked.append(f"robots.txt disallows {start_url}")
        return crawl
    home = _fetch_home(client, start_url)
    if home is None:
        return crawl
    crawl.pages.append(home)
    proven = bool(orgnr_span(home[0], orgnr))
    targets = _pick_pages(home[0], extra_pages, orgnr_found=proven)
    if not proven and not targets and extra_pages:
        base = home[1].final_url.rstrip("/")
        targets = [base + p for p in GUESSED_PATHS][:extra_pages]
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


def _careers_link(pages: list[tuple[Page, Response]], site_url: str) -> str | None:
    for page, _ in pages:
        for url, text in page.links:
            if same_site(url, site_url) and classify_link(url, text) == "careers" and not login_url(url):
                return url.split("#")[0]
    return None


def _add_careers_page(client: HttpClient, pages: list[tuple[Page, Response]]) -> None:
    """Fetch the verified site's careers page when the crawl has not, so an opening listed there can be seen.
    One extra request, only for sites that link a careers page."""
    url = _careers_link(pages, pages[0][1].final_url)
    seen = {u.rstrip("/") for p, r in pages for u in (p.url, r.final_url)}
    if url is None or url.rstrip("/") in seen or not ROBOTS.allowed(client, url):
        return
    try:
        page = _fetch_page(client, url)
    except (ConnectionError, BudgetExceeded):
        return  # the careers field then stays "can't tell": no opening was seen
    if page is not None:
        pages.append(page)


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
    order = PAGE_PRIORITY if not orgnr_found else ("careers", "news", "contact", "about", "legal")
    return [by_kind[k] for k in order if k in by_kind][:limit]


def _identity(pages: list[tuple[Page, Response]], orgnr: str, name: str) -> dict[str, Any]:
    others = sorted(set().union(*(orgnrs_on_page(p) for p, _ in pages)) - {orgnr})
    home, home_resp = pages[0]
    tied = name_in_title(home, name) or domain_matches_name(home_resp.final_url, name)
    for page, resp in pages:
        span = orgnr_span(page, orgnr)
        if span and others and not tied:  # our number listed among others: a group site ("Våre selskaper")
            return {"level": "unverified", "other_orgnrs": others, "listed_among_others": True}
        if span:
            return {"level": "orgnr_on_page", "proof_url": resp.final_url, "claim_span": span,
                    "proof_sha256": resp.sha256, **({"other_orgnrs": others} if others else {})}
    if others:  # the site states whose it is, and it is not this company
        return {"level": "unverified", "other_orgnrs": others}
    # A name-only match must also tie the site to this company rather than a parent or partner:
    # the name is in the site's title/site name, or the domain contains a distinctive word of the name.
    for page, resp in pages:
        span = name_span(page, name)
        if span and (name_in_title(page, name) or domain_matches_name(resp.final_url, name)):
            return {"level": "name_on_page", "proof_url": resp.final_url, "claim_span": span,
                    "proof_sha256": resp.sha256}
    return {"level": "unverified"}


def _facts(pages: list[tuple[Page, Response]], proof: dict[str, Any], orgnr: str, name: str, start_url: str,
           origin: str) -> tuple[tuple[Fact, ...], dict[str, str]]:
    """Facts from a verified site, plus fields withheld as "can't tell" with the reason."""
    confidence = "high" if proof["level"] == "orgnr_on_page" and not proof.get("search_tie") else "medium"
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
    owners: tuple[str, ...] = (name, home_resp.final_url)
    if brand and handle_matches(brand, (name, home_resp.final_url)):  # not the web agency's or group's brand
        out.append(fact("brand_name", brand[:120], home_resp, "Public brand name on website", brand, "meta_og"))
        owners = (name, brand, home_resp.final_url)
    if home.title:
        out.append(fact("website_title", home.title[:300], home_resp, "Website title", home.title, "html_title"))
    if home.description:
        out.append(fact("website_description", home.description[:600], home_resp, "Self-description on website",
                        home.description[:300], "meta_description"))
    group_site = sorted(proof.get("other_orgnrs") or [])
    withheld = _collect(out, pages, fact, home_resp, owners, confidence, group_site)
    return tuple(out), withheld


def _collect(out: list[Fact], pages: list[tuple[Page, Response]], fact: Any, home_resp: Response,
             owners: tuple[str, ...], site_confidence: str, group_site: list[str] | None = None) -> dict[str, str]:
    all_emails: list[str] = []
    all_phones: list[str] = []
    social: dict[str, str] = {}
    news: list[dict[str, str]] = []
    careers_url: str | None = None
    login_links: list[str] = []
    site_jobs: list[dict[str, Any]] = []
    platforms: dict[str, str] = {}
    jobs_resp = platform_resp = home_resp
    email_resp = phone_resp = social_resp = news_resp = home_resp
    handle_owners = (owners[0], owners[-1])  # legal name and verified site: the starter kit's handle rule
    for page, resp in pages:
        found = [e for e in emails(page, home_resp.final_url) if e not in all_emails]
        if found and not all_emails:
            email_resp = resp
        all_emails += found
        numbers = [p for p in phones(page) if p not in all_phones]
        if numbers and not all_phones:
            phone_resp = resp
        all_phones += numbers
        links = {k: v for k, v in social_links(page, handle_owners).items() if k not in social}
        if links and not social:
            social_resp = resp
        social.update(links)
        found_jobs = job_postings(page, handle_owners)
        if found_jobs and not site_jobs:
            jobs_resp = resp
        site_jobs += found_jobs
        found_platforms = {k: v for k, v in careers_platforms(page, handle_owners).items() if k not in platforms}
        if found_platforms and not platforms:
            platform_resp = resp
        platforms.update(found_platforms)
        items = dated_items(page)
        if items and not news:
            news_resp = resp
        news += items
        for url, text in page.links:
            if careers_url is None and same_site(url, home_resp.final_url) and classify_link(url, text) == "careers":
                if login_url(url):
                    login_links.append(url)
                else:
                    careers_url = url.split("#")[0]
    if all_emails:
        out.append(fact("website_emails", all_emails[:5], email_resp, "Contact emails on website",
                        ", ".join(all_emails[:5]), "mailto_links"))
    if all_phones:
        out.append(fact("website_phones", all_phones[:5], phone_resp, "Phone numbers on website",
                        ", ".join(all_phones[:5]), "tel_links"))
    withheld: dict[str, str] = {}
    social = _own_accounts(social, owners[0], group_site or [], withheld)
    if social:
        out.append(fact("social_profiles", dict(sorted(social.items())), social_resp,
                        "Company-owned social profiles linked from the verified website",
                        " ".join(sorted(social.values())), "outbound_links"))
    _publish_jobs(out, fact, site_jobs, jobs_resp, owners, site_confidence, withheld)
    openings = next((f for f in out if f.field == "website_job_postings"), None)
    listed = None if openings else _listed_opening(pages, careers_url, owners[0], group_site or [])
    if careers_url and (openings or isinstance(listed, tuple)):
        span, resp = listed if isinstance(listed, tuple) else (careers_url, home_resp)
        out.append(fact("careers_page", careers_url, resp, "Careers page on website", span,
                        "listed_opening" if isinstance(listed, tuple) else "link_classifier", conf="medium"))
    elif careers_url or login_links:
        withheld["careers_page"] = (listed if isinstance(listed, str) else
                                    "careers link found but no opening for this company was seen on the site"
                                    if careers_url else "careers link is a sign-in page, which shows no public opening")
    platforms = {k: v for k, v in platforms.items() if not login_url(v)}
    if platforms and openings:
        out.append(fact("careers_platform", dict(sorted(platforms.items())), platform_resp,
                        "Recruitment platform linked from the website", " ".join(sorted(platforms.values())),
                        "outbound_links", conf="medium"))
    elif platforms:
        withheld["careers_platform"] = ("the company's recruitment account is linked, but no opening for this "
                                        "company was seen")
    _publish_news(out, fact, news, news_resp, owners, site_confidence, withheld)
    return withheld


def _listed_opening(pages: list[tuple[Page, Response]], careers_url: str | None, name: str,
                    group_site: list[str]) -> tuple[str, Response] | str | None:
    """(evidence span, response) when the careers page lists an opening with a future application deadline,
    a reason string when one is listed but can't be tied to this company, or None when none is listed."""
    if not careers_url:
        return None
    target = careers_url.rstrip("/")
    page_resp = next(((p, r) for p, r in pages if target in (p.url.rstrip("/"), r.final_url.rstrip("/"))), None)
    if page_resp is None:
        return None
    found = listed_openings(page_resp[0])
    if not found:
        return None
    if group_site:
        return (f"the careers page lists openings, but the site also states org number(s) "
                f"{', '.join(group_site[:3])}, so they may be another group company's")
    if "holding" in ascii_tokens(name):
        return "the careers page of a holding company lists its group's openings, not necessarily this company's"
    return f"opening with application deadline {found[0]['deadline']}: {found[0]['context']}", page_resp[1]


def _own_accounts(social: dict[str, str], name: str, group_site: list[str],
                  withheld: dict[str, str]) -> dict[str, str]:
    """Drop accounts that belong to a parent group, a sibling or a region rather than this company."""
    if not social:
        return social
    if group_site:  # the site states several companies' org numbers: its accounts can't be tied to this one
        withheld["social_profiles"] = (f"the website also states org number(s) {', '.join(group_site[:3])}, so its "
                                       "social accounts may belong to a parent or sibling company")
        return {}
    handles = {net: (social_account(url) or ("", "", ""))[1] for net, url in social.items()}
    dropped = parent_group_accounts(social, handles, name)
    if dropped:
        withheld["social_profiles"] = "; ".join(dropped[n] for n in sorted(dropped))
    return {net: url for net, url in social.items() if net not in dropped}


def _publish_jobs(out: list[Fact], fact: Any, site_jobs: list[dict[str, Any]], resp: Response,
                  owners: tuple[str, ...], site_confidence: str, withheld: dict[str, str]) -> None:
    unique = sorted({(j["title"], j["url"]): j for j in site_jobs}.values(),
                    key=lambda j: (j["published"] or "", j["title"]), reverse=True)
    assessed = [assess_site_job(j, owners, site_confidence) for j in unique]
    kept = [a for a in assessed if a][:10]
    dropped = sum(1 for a in assessed if a is None)
    if not kept:
        if unique:
            withheld["website_job_postings"] = (f"{len(unique)} job ad(s) on the site do not name this company as "
                                                "employer (could be another group company's opening)")
        return
    out.append(fact("website_job_postings", kept, resp, "Job postings published on the company website",
                    kept[0]["title"], "jsonld_jobposting", conf=lowest([j["confidence"] for j in kept])))
    if dropped:
        withheld["website_job_postings"] = f"{dropped} other job ad(s) left out: they do not name this company"


def _publish_news(out: list[Fact], fact: Any, news: list[dict[str, Any]], resp: Response,
                  owners: tuple[str, ...], site_confidence: str, withheld: dict[str, str]) -> None:
    unique = sorted({(i["date"], i["title"]): i for i in news}.values(), key=lambda i: i["date"], reverse=True)
    assessed = [assess_news(i, owners, site_confidence) for i in unique]
    kept = [a for a in assessed if a][:10]
    dropped = sum(1 for a in assessed if a is None)
    if not kept:
        if unique:
            withheld["website_news"] = f"{len(unique)} dated post(s) found, but none names the company"
        return
    out.append(fact("website_news", kept, resp, "Dated news and updates on the company website",
                    f"{kept[0]['date']} {kept[0]['title']}", "time_tags", conf=lowest([i["confidence"] for i in kept])))
    if dropped:
        withheld["website_news"] = f"{dropped} other dated post(s) left out: they do not name the company"
