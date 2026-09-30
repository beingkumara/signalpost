"""Website discovery when the registry lists no website.

Candidates come from the legal name and from NAV job ads whose employer org number matched.
A candidate is only a hypothesis: `crawl_site(discovered=True)` publishes it only if a crawled
page shows the exact org number.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable

from signalpost.http_client import HttpClient
from signalpost.modules import NOT_AVAILABLE, ModuleResult
from signalpost.safe_url import host_resolves
from signalpost.sources.search import candidate_sites
from signalpost.sources.website import crawl_site
from signalpost.webparse import core_name

_TRANSLIT = {"æ": "ae", "ø": "o", "å": "a", "ä": "a", "ö": "o", "ü": "u", "&": " og "}
_GENERIC_TAIL = {"norge", "norway", "holding", "eiendom", "invest", "group", "gruppen", "consult", "service",
                 "services", "drift", "handel", "nordic", "norden", "international", "og", "and", "markedsforing",
                 "arkitektfirma", "arkitekter", "regnskap", "regnskapsbyra", "bygg", "transport", "consulting",
                 "utvikling", "entreprenor", "elektro", "maskin", "rorlegger", "eiendomsselskap", "i"}
WEBMAIL = {"gmail.com", "hotmail.com", "hotmail.no", "outlook.com", "live.no", "live.com", "yahoo.com", "yahoo.no",
           "icloud.com", "me.com", "online.no", "broadpark.no", "altibox.no", "getmail.no", "frisurf.no", "start.no",
           "msn.com", "aol.com", "protonmail.com", "proton.me", "lyse.net", "c2i.net", "hotmail.co.uk",
           "gmx.com", "mail.com", "tele2.no", "chello.no", "ebnett.no", "enivest.no", "tussa.com", "haugnett.no"}
MIN_SLUG = 4


def name_tokens(text: str) -> list[str]:
    """Lowercase ASCII tokens with Norwegian letters transliterated."""
    lowered = text.lower()
    for src, dst in _TRANSLIT.items():
        lowered = lowered.replace(src, dst)
    lowered = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode()
    return [t for t in re.split(r"[^a-z0-9]+", lowered) if t]


def slug_tokens(name: str) -> list[str]:
    """Tokens of the legal name without its legal-form suffix (AS, ASA ...)."""
    return name_tokens(core_name(name))


def candidate_domains(name: str) -> list[str]:
    tokens = slug_tokens(name)
    if not tokens:
        return []
    options: list[str] = []
    joined = "".join(tokens)
    if len(joined) >= MIN_SLUG:
        options.append(f"{joined}.no")
    if len(tokens) > 1:
        options.append(f"{'-'.join(tokens)}.no")
        trimmed = [t for t in tokens if t not in _GENERIC_TAIL]
        if trimmed and trimmed != tokens and len("".join(trimmed)) >= MIN_SLUG:
            options.append(f"{''.join(trimmed)}.no")
    if len(joined) >= MIN_SLUG:
        options.append(f"{joined}.com")
    seen: list[str] = []
    for domain in options:
        if domain not in seen and len(domain) <= 63:
            seen.append(domain)
    return seen


def email_domain_candidate(email: str | None) -> str | None:
    """The domain of a registered company email is a strong website candidate (webmail excluded)."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    if domain in WEBMAIL or "." not in domain:
        return None
    return domain


def discover_website(client: HttpClient, orgnr: str, name: str, *, extra_candidates: list[str] | None = None,
                     email: str | None = None, max_fetch: int = 2, extra_pages: int = 2,
                     search: Callable[[], tuple[list[str], str | None]] | None = None,
                     postcode: str | None = None) -> ModuleResult:
    urls = [u for u in (extra_candidates or []) if u.startswith(("http://", "https://"))]
    mail_domain = email_domain_candidate(email)
    if mail_domain and host_resolves(mail_domain):
        urls.append(f"https://{mail_domain}/")
    urls += [f"https://{d}/" for d in candidate_domains(name) if host_resolves(d)]
    tried: list[str] = []
    for url in _dedupe(urls)[:max_fetch]:
        origin = ("job_ad_employer_homepage" if url in (extra_candidates or [])
                  else "registry_email_domain" if mail_domain and mail_domain in url else "domain_candidate")
        # The registry's own email domain is an official link, so the registered name suffices (medium
        # confidence); every other candidate needs the org number on a crawled page.
        official_link = origin == "registry_email_domain"
        result = crawl_site(client, url, orgnr, name, discovered=not official_link, extra_pages=extra_pages,
                            origin=origin)
        tried.append(url)
        if result.state == "available":
            return result
    if search is not None:  # search runs last: it costs money and only proposes candidates
        urls, provider = search()
        for url in [u for u in candidate_sites(urls) if _key(u) not in {_key(t) for t in tried}][:2]:
            result = crawl_site(client, url, orgnr, name, discovered=True, extra_pages=extra_pages,
                                origin=f"search_candidate:{provider}", postcode=postcode)
            tried.append(url)
            if result.state == "available":
                return result
    reason = ("no registry website; candidates checked without org-number proof: " + ", ".join(tried)) if tried \
        else "no registry website and no resolvable domain candidate"
    return ModuleResult("website", NOT_AVAILABLE, (), reason)


def _key(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/").lower()


def _dedupe(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = re.sub(r"^https?://(www\.)?", "", url).rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out
