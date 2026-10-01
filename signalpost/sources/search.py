"""Web search providers used only to *generate website candidates*.

Search results are never evidence. A candidate domain is published only after the agent
fetches the site itself and finds the exact org number (see sources/website.py).

Providers are enabled by environment variables and tried in order until one answers:
TAVILY_API_KEY, EXA_API_KEY, LINKUP_API_KEY, FIRECRAWL_API_KEY. The order can be set with
SIGNALPOST_SEARCH_PROVIDERS (comma separated). Tavily's keyless Search mode is the free last resort
and needs no account; disable it with SIGNALPOST_SEARCH_KEYLESS=0.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from signalpost.http_client import BudgetExceeded, HttpClient
from signalpost.webparse import registrable_domain

ENV_PROVIDERS = "SIGNALPOST_SEARCH_PROVIDERS"
ENV_MAX = "SIGNALPOST_SEARCH_MAX"
DEFAULT_MAX_SEARCHES = 150
RESULTS = 6

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
    "infotorg.no", "regnskapstall.com", "lovdata.no", "kunngjoringer.no", "doffin.no", "glassdoor.com",
    "indeed.com", "jobbnorge.no", "karrierestart.no", "webcruiter.no", "teamtailor.com", "amazon.com", "ebay.com",
}


@dataclass(frozen=True)
class Provider:
    name: str
    env_key: str | None  # None: works without a key
    url: str
    usd_per_search: float  # list price after free credits, declared as external cost
    build: Callable[[str, str], tuple[dict[str, str], dict[str, Any]]]
    parse: Callable[[dict[str, Any]], list[str]]


def _tavily(key: str, query: str) -> tuple[dict[str, str], dict[str, Any]]:
    return {"Authorization": f"Bearer {key}"}, {"query": query, "max_results": RESULTS, "search_depth": "basic",
                                               "country": "norway"}


def _tavily_keyless(_key: str, query: str) -> tuple[dict[str, str], dict[str, Any]]:
    # Tavily's documented keyless access mode for Search (rate limited, no account, no cost).
    return {"X-Tavily-Access-Mode": "keyless"}, {"query": query, "max_results": RESULTS, "search_depth": "basic",
                                                  "country": "norway"}


def _exa(key: str, query: str) -> tuple[dict[str, str], dict[str, Any]]:
    return {"x-api-key": key}, {"query": query, "numResults": RESULTS, "type": "auto", "userLocation": "NO"}


def _linkup(key: str, query: str) -> tuple[dict[str, str], dict[str, Any]]:
    return {"Authorization": f"Bearer {key}"}, {"q": query, "depth": "standard", "outputType": "searchResults",
                                               "maxResults": RESULTS}


def _firecrawl(key: str, query: str) -> tuple[dict[str, str], dict[str, Any]]:
    return {"Authorization": f"Bearer {key}"}, {"query": query, "limit": RESULTS, "country": "NO"}


def _urls(items: Any) -> list[str]:
    return [str(i["url"]) for i in items or [] if isinstance(i, dict) and i.get("url")]


PROVIDERS: dict[str, Provider] = {
    "tavily": Provider("tavily", "TAVILY_API_KEY", "https://api.tavily.com/search", 0.008, _tavily,
                       lambda d: _urls(d.get("results"))),
    "exa": Provider("exa", "EXA_API_KEY", "https://api.exa.ai/search", 0.005, _exa, lambda d: _urls(d.get("results"))),
    "linkup": Provider("linkup", "LINKUP_API_KEY", "https://api.linkup.so/v1/search", 0.006, _linkup,
                       lambda d: _urls(d.get("results"))),
    "firecrawl": Provider("firecrawl", "FIRECRAWL_API_KEY", "https://api.firecrawl.dev/v2/search", 0.005, _firecrawl,
                          lambda d: _urls((d.get("data") or {}).get("web") if isinstance(d.get("data"), dict)
                                          else d.get("data"))),
    "tavily_keyless": Provider("tavily_keyless", None, "https://api.tavily.com/search", 0.0, _tavily_keyless,
                               lambda d: _urls(d.get("results"))),
}
ENV_KEYLESS = "SIGNALPOST_SEARCH_KEYLESS"


@dataclass
class SearchService:
    """Thread-safe search with provider failover, a per-run cap and a cost ledger."""

    providers: list[tuple[Provider, str]] = field(repr=False)
    max_searches: int = DEFAULT_MAX_SEARCHES
    searches: int = 0
    cost_usd: float = 0.0
    by_provider: dict[str, int] = field(default_factory=dict)
    disabled: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    def summary(self) -> dict[str, Any]:
        return {"providers": [p.name for p, _ in self.providers], "searches": self.searches,
                "by_provider": dict(self.by_provider), "estimated_cost_usd": round(self.cost_usd, 4),
                "disabled": dict(self.disabled), "max_searches": self.max_searches}

    def _reserve(self) -> bool:
        with self._lock:
            active = any(p.name not in self.disabled for p, _ in self.providers)
            if not active or self.searches >= self.max_searches:
                return False
            self.searches += 1
            return True

    def search(self, client: HttpClient, query: str) -> tuple[list[str], str | None]:
        """Return (result URLs, provider name). Empty list when no provider could answer."""
        if not self.providers or not self._reserve():
            return [], None
        for provider, key in self.providers:
            if provider.name in self.disabled:
                continue
            headers, payload = provider.build(key, query)
            try:
                resp = client.post_json(provider.url, payload, headers=headers, timeout=20)
            except BudgetExceeded:
                return [], None
            except ConnectionError as err:
                self._disable(provider, f"unreachable: {err}"[:120])
                continue
            if resp.status != 200:  # bad request, bad key, no credits, rate limit or outage: fail over
                self._disable(provider, f"HTTP {resp.status}")
                continue
            with self._lock:
                self.cost_usd += provider.usd_per_search
                self.by_provider[provider.name] = self.by_provider.get(provider.name, 0) + 1
            try:
                return provider.parse(resp.json()), provider.name
            except Exception:  # unexpected response shape from a third party
                return [], provider.name
        return [], None

    def _disable(self, provider: Provider, reason: str) -> None:
        with self._lock:
            self.disabled[provider.name] = reason


def from_env(max_searches: int | None = None, environ: dict[str, str] | None = None) -> SearchService:
    env = os.environ if environ is None else environ
    order = [n.strip().lower() for n in (env.get(ENV_PROVIDERS) or ",".join(PROVIDERS)).split(",") if n.strip()]
    keyless_on = (env.get(ENV_KEYLESS) or "1").strip().lower() not in ("0", "false", "no", "off")
    chosen: list[tuple[Provider, str]] = []
    for name in order:
        provider = PROVIDERS.get(name)
        if provider is None:
            continue
        if provider.env_key is None:
            if keyless_on:
                chosen.append((provider, ""))
        elif env.get(provider.env_key, "").strip():
            chosen.append((provider, env[provider.env_key].strip()))
    try:
        cap = max_searches if max_searches is not None else int(env.get(ENV_MAX) or DEFAULT_MAX_SEARCHES)
    except ValueError:
        cap = DEFAULT_MAX_SEARCHES
    return SearchService(chosen, max(0, cap))


def candidate_sites(urls: list[str], limit: int = 3) -> list[str]:
    """Distinct company-site roots from search results, skipping directories and platforms."""
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        except ValueError:
            continue
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            continue
        domain = registrable_domain(host)
        if domain in seen or domain in NOT_COMPANY_SITES or any(domain.endswith("." + d) for d in NOT_COMPANY_SITES):
            continue
        seen.add(domain)
        out.append(f"{parsed.scheme}://{host}/")
        if len(out) >= limit:
            break
    return out
