"""Research one company: run every module and record an explicit state for each."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from signalpost import __version__, orgnr as orgnr_mod
from signalpost.explain import explain
from signalpost.facts import Fact
from signalpost.http_client import BudgetExceeded, HttpClient, Response, utc_now_iso
from signalpost.modules import (AVAILABLE, BLOCKED, FAILED, NOT_APPLICABLE, NOT_AVAILABLE, ModuleResult,
                                skipped)
from signalpost.sources.activity import registry_events
from signalpost.sources.discovery import discover_website
from signalpost.sources.enhetsregisteret import EntityResult, fetch_entity, fetch_parent_name
from signalpost.sources.regnskap import fetch_accounts
from signalpost.sources.roller import fetch_roles
from signalpost.sources.underenheter import fetch_subunits
from signalpost.sources.website import crawl_site

SCHEMA_VERSION = "2.0"
CORE_COST = 2  # accounts + roles per company, reserved before optional work
NO_ACCOUNTS_FORMS = {"ENK", "ANS", "DA", "FLI", "ORGL", "STAT", "KOMM", "FYLK", "KIRK", "ESEK", "SAM"}


@dataclass
class RunContext:
    """Shared across all companies in a run."""

    client: HttpClient
    deadline: float
    companies_not_started: int
    website_pages: int = 2
    discovery: bool = True
    jobs: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start_company(self) -> None:
        with self._lock:
            self.companies_not_started = max(0, self.companies_not_started - 1)

    def time_left(self) -> bool:
        return time.monotonic() < self.deadline

    def can_afford(self, cost: int) -> bool:
        with self._lock:
            reserve = self.companies_not_started * CORE_COST
        return self.client.remaining - reserve >= cost


class CountingClient:
    """Per-company view of the shared client that counts this company's requests."""

    def __init__(self, inner: HttpClient) -> None:
        self._inner = inner
        self.count = 0

    @property
    def remaining(self) -> int:
        return self._inner.remaining

    def get(self, url: str, **kwargs: Any) -> Response:
        self.count += 1
        return self._inner.get(url, **kwargs)


def research(ctx: RunContext, raw_input: str, entity: EntityResult | None = None) -> dict[str, Any]:
    started = utc_now_iso()
    t0 = time.monotonic()
    ctx.start_company()
    client = CountingClient(ctx.client)
    try:
        orgnr = orgnr_mod.parse(raw_input)
    except orgnr_mod.InvalidOrgnr as err:
        return _empty(raw_input.strip(), "invalid_input", started, [str(err)], [], t0, client)
    if entity is None:
        try:
            entity = fetch_entity(client, orgnr)  # type: ignore[arg-type]
        except (ConnectionError, ValueError, BudgetExceeded) as err:
            return _empty(orgnr, "lookup_failed", started, [f"Enhetsregisteret lookup failed: {err}"],
                          [ModuleResult("registry", FAILED, (), str(err)[:300])], t0, client)
    if entity.kind == "not_found":
        return _empty(orgnr, "not_found", started, ["No main unit or sub-unit with this number in Enhetsregisteret."],
                      [ModuleResult("registry", NOT_AVAILABLE, (), "not in Enhetsregisteret", entity.source_url)],
                      t0, client)

    registry = ModuleResult("registry", AVAILABLE, entity.facts, None, entity.source_url, entity.retrieved_at)
    results = [registry, *_run_modules(ctx, client, entity)]
    facts: list[Fact] = [f for r in results for f in r.facts]
    role_facts = [f for f in facts if f.source.source_class == "official_roles"]
    entity_fact = next(f for f in entity.facts if f.field == "name") if any(
        f.field == "name" for f in entity.facts) else entity.facts[0]
    try:
        facts.extend(registry_events(entity.raw or {}, role_facts, entity_fact))
    except Exception as err:  # derived events are optional
        results.append(ModuleResult("registry_events", FAILED, (), f"{type(err).__name__}: {err}"[:300]))
    subunits = sorted({o for r in results for o in r.extra.get("subunit_orgnrs", [])})
    return {**_profile(orgnr, entity, facts, results, started, t0, client), "subunit_orgnrs": subunits,
            "entity_kind": entity.kind}


def _run_modules(ctx: RunContext, client: CountingClient, entity: EntityResult) -> list[ModuleResult]:
    raw = entity.raw or {}
    if entity.kind == "deleted":
        return [skipped(m, NOT_APPLICABLE, "entity deleted from the register")
                for m in ("annual_accounts", "roles", "workplaces", "website")]
    if entity.kind == "underenhet":
        parent = raw.get("overordnetEnhet")
        out = [skipped(m, NOT_APPLICABLE, "sub-unit: roles and accounts belong to the parent entity")
               for m in ("annual_accounts", "roles", "workplaces")]
        if parent:
            out.append(_guard(ctx, "parent", 1, lambda: _as_result(
                "parent", fetch_parent_name(client, parent, entity.orgnr), parent), core=True))
        out.append(skipped("website", NOT_APPLICABLE, "sub-unit"))
        return out

    form = (raw.get("organisasjonsform") or {}).get("kode")
    accounts = _guard(ctx, "annual_accounts", 1, lambda: fetch_accounts(client, entity.orgnr), core=True)
    if accounts.state == NOT_AVAILABLE and form in NO_ACCOUNTS_FORMS:
        accounts = ModuleResult("annual_accounts", NOT_APPLICABLE, (),
                                f"organisation form {form} is generally not obliged to file accounts",
                                accounts.source_url, accounts.checked_at)
    roles = _guard(ctx, "roles", 1, lambda: fetch_roles(client, entity.orgnr), core=True)
    workplaces = _guard(ctx, "workplaces", 1, lambda: fetch_subunits(client, entity.orgnr))
    subunits = set(workplaces.extra.get("subunit_orgnrs", []))
    name = raw.get("navn", "")
    website = _website(ctx, client, entity, name, [])
    return [accounts, roles, workplaces, website]


def _website(ctx: RunContext, client: CountingClient, entity: EntityResult, name: str,
             job_homepages: list[str]) -> ModuleResult:
    registered = next((f.value for f in entity.facts if f.field == "website"), None)
    pages = ctx.website_pages
    if registered:
        return _guard(ctx, "website", 2 + pages, lambda: crawl_site(
            client, registered, entity.orgnr, name, discovered=False, extra_pages=pages))
    if not ctx.discovery:
        return skipped("website", BLOCKED, "no registry website; discovery disabled for this run")
    email = next((f.value for f in entity.facts if f.field == "email"), None)
    return _guard(ctx, "website", 6, lambda: discover_website(
        client, entity.orgnr, name, extra_candidates=job_homepages, email=email, max_fetch=2, extra_pages=2))


def _guard(ctx: RunContext, module: str, cost: int, fn: Callable[[], ModuleResult], core: bool = False) -> ModuleResult:
    if not ctx.time_left():
        return skipped(module, BLOCKED, "run time budget reached before this source was checked")
    if not core and not ctx.can_afford(cost):
        return skipped(module, BLOCKED, "run request budget reserved for core registry lookups")
    try:
        return fn()
    except BudgetExceeded:
        return skipped(module, BLOCKED, "run request budget exhausted")
    except Exception as err:  # one broken source must never cost the company its other claims
        return ModuleResult(module, FAILED, (), f"{type(err).__name__}: {err}"[:300])


def _as_result(module: str, facts: tuple[Fact, ...], url: str) -> ModuleResult:
    state = AVAILABLE if facts else NOT_AVAILABLE
    return ModuleResult(module, state, facts, None if facts else "not found", url)


def derive_status(kind: str, facts: list[Fact]) -> str:
    values = {f.field: f.value for f in facts}
    if kind == "deleted":
        return "deleted"
    if kind == "underenhet":
        return "subunit"
    for flag, status in (("bankrupt", "bankrupt"), ("under_forced_dissolution", "under_forced_dissolution"),
                         ("under_liquidation", "under_liquidation")):
        if values.get(flag):
            return status
    return "active"


def _profile(orgnr: str, entity: EntityResult, facts: list[Fact], results: list[ModuleResult], started: str,
             t0: float, client: CountingClient) -> dict[str, Any]:
    warnings = [f"{r.module}: {r.reason}" for r in results if r.state in (FAILED, BLOCKED) and r.reason]
    profile = {
        "schema_version": SCHEMA_VERSION,
        "agent_version": __version__,
        "orgnr": orgnr,
        "orgnr_formatted": orgnr_mod.pretty(orgnr),
        "name": next((f.value for f in facts if f.field == "name"), None),
        "status": derive_status(entity.kind, facts),
        "checked_at": started,
        "fields": {f.field: f.value for f in facts},
        "facts": [f.to_dict() for f in facts],
        "modules": [r.to_dict() for r in results],
        "sources_checked": [{"source": r.module, "url": r.source_url, "state": r.state,
                             **({"error": r.reason} if r.state == FAILED else {})} for r in results],
        "warnings": warnings,
        "operations": {"requests": client.count, "runtime_ms": int((time.monotonic() - t0) * 1000),
                       "third_party_cost_usd": 0.0},
    }
    return {**profile, "explanation": explain(profile)}


def _empty(orgnr: str, status: str, started: str, warnings: list[str], results: list[ModuleResult], t0: float,
           client: CountingClient) -> dict[str, Any]:
    profile = {
        "schema_version": SCHEMA_VERSION,
        "agent_version": __version__,
        "orgnr": orgnr,
        "name": None,
        "status": status,
        "checked_at": started,
        "fields": {},
        "facts": [],
        "modules": [r.to_dict() for r in results],
        "sources_checked": [],
        "warnings": warnings,
        "operations": {"requests": client.count, "runtime_ms": int((time.monotonic() - t0) * 1000),
                       "third_party_cost_usd": 0.0},
    }
    return {**profile, "explanation": explain(profile)}


def attach_module(profile: dict[str, Any], result: ModuleResult) -> dict[str, Any]:
    """Return a new profile with a late module result (e.g. the rate-limited filing-history lane)."""
    facts = [*profile["facts"], *(f.to_dict() for f in result.facts)]
    modules = [*profile.get("modules", []), result.to_dict()]
    updated = {**profile, "facts": facts, "modules": modules,
               "fields": {**profile["fields"], **{f.field: f.value for f in result.facts}}}
    return {**updated, "explanation": explain(updated)}
