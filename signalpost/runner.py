"""Batch execution: exactly one terminal envelope per input, within time and request budgets."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from signalpost import __version__, config, orgnr as orgnr_mod, store
from signalpost.agent import RunContext, attach_module, research
from signalpost.envelope import build_envelope
from signalpost.http_client import BudgetExceeded, HttpClient, utc_now_iso
from signalpost.modules import BLOCKED, FAILED, NOT_APPLICABLE, STATES, ModuleResult, skipped
from signalpost.sources.enhetsregisteret import EntityResult, fetch_entities_batch
from signalpost.sources.jobs import FeedIndex, build_index, jobs_for_company, public_token
from signalpost.sources.regnskap import fetch_filing_years


MAX_JOB_DETAILS = 6


@dataclass(frozen=True)
class RunOptions:
    out_dir: Path
    profiles_dir: Path | None
    workers: int = config.DEFAULT_WORKERS
    max_minutes: float = config.DEFAULT_MAX_MINUTES
    run_id: str = ""
    website_pages: int = 2
    discovery: bool = True
    jobs: bool = True
    history: bool = True
    jobs_days: int = config.JOBS_FEED_DAYS
    previous: dict[str, dict[str, Any]] | None = None
    expected_count: int | None = None
    progress: Callable[[str], None] | None = None


def run_batch(client: HttpClient, inputs: list[str], opts: RunOptions) -> dict[str, Any]:
    started_at = utc_now_iso()
    t0 = time.monotonic()
    deadline = t0 + opts.max_minutes * 60
    run_id = opts.run_id or f"signalpost-{started_at.replace(':', '').replace('-', '')}"
    valid = [o for o in (_parse(i) for i in inputs) if o]
    entities = _batch_entities(client, valid)
    ctx = RunContext(client, deadline, len(inputs), opts.website_pages, opts.discovery, opts.jobs)
    history_ids = [o for o in dict.fromkeys(valid) if entities.get(o) is None or entities[o].kind == "enhet"]
    lane = _HistoryLane(ctx, history_ids) if opts.history else None
    if lane:
        lane.start()
    feed = _FeedLane(ctx, opts.jobs_days) if opts.jobs else None
    if feed:
        feed.start()

    profiles: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, opts.workers)) as pool:
        futures = {pool.submit(_safe_research, ctx, raw, entities.get(_parse(raw) or "")): raw for raw in inputs}
        for done, future in enumerate(as_completed(futures), start=1):
            raw = futures[future]
            profiles[raw] = future.result()
            if opts.progress:
                p = profiles[raw]
                opts.progress(f"[{done}/{len(inputs)}] {p['orgnr']} {p['status']} {p.get('name') or '-'} "
                              f"(requests {client.used})")
    jobs = _run_jobs(ctx, feed, profiles, opts)
    if lane:
        lane.join(max(0.0, deadline - time.monotonic()) + 5)

    completed_at = utc_now_iso()
    run = {"run_id": run_id, "started_at": started_at, "completed_at": completed_at}
    envelopes = []
    for raw in inputs:
        profile = _with_history(profiles[raw], lane)
        profile = _with_jobs(profile, jobs, opts)
        profile = _refresh(profile, opts)
        envelopes.append(build_envelope(profile, run))
    report = _report(envelopes, inputs, client, t0, run, opts)
    _write(opts.out_dir, envelopes, report)
    return report


def _parse(raw: str) -> str | None:
    try:
        return orgnr_mod.parse(raw)
    except orgnr_mod.InvalidOrgnr:
        return None


def _batch_entities(client: HttpClient, orgnrs: list[str]) -> dict[str, EntityResult]:
    try:
        return fetch_entities_batch(client, list(dict.fromkeys(orgnrs)))
    except (ConnectionError, ValueError, BudgetExceeded):
        return {}  # every company falls back to a single lookup


def _safe_research(ctx: RunContext, raw: str, entity: EntityResult | None) -> dict[str, Any]:
    try:
        return research(ctx, raw, entity)
    except Exception as err:  # never drop a company: record the failure in its envelope
        return {"schema_version": "2.0", "agent_version": __version__, "orgnr": raw.strip(), "name": None,
                "status": "lookup_failed", "checked_at": utc_now_iso(), "fields": {}, "facts": [],
                "modules": [ModuleResult("registry", FAILED, (), f"unexpected error: {err!r}"[:300]).to_dict()],
                "warnings": [f"unexpected error: {err!r}"[:300]],
                "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0.0},
                "explanation": {"summary": "Processing failed; no facts were published.", "notes": []}}


class _HistoryLane(threading.Thread):
    """Filing-year lookups are rate limited upstream (~30/min), so they run paced in one thread."""

    def __init__(self, ctx: RunContext, orgnrs: list[str]) -> None:
        super().__init__(daemon=True)
        self.ctx = ctx
        self.orgnrs = orgnrs
        self.results: dict[str, ModuleResult] = {}

    def run(self) -> None:
        for orgnr in self.orgnrs:
            if not self.ctx.time_left() or not self.ctx.can_afford(1):
                return
            started = time.monotonic()
            try:
                self.results[orgnr] = fetch_filing_years(self.ctx.client, orgnr)
            except BudgetExceeded:
                return
            except (ConnectionError, ValueError) as err:
                self.results[orgnr] = ModuleResult("filing_history", FAILED, (), str(err)[:300])
            wait = config.FILING_HISTORY_INTERVAL_SECONDS - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)


class _FeedLane(threading.Thread):
    """Reads NAV's job feed once while the main pass runs."""

    def __init__(self, ctx: RunContext, days: int) -> None:
        super().__init__(daemon=True)
        self.ctx = ctx
        self.days = days
        self.token: str | None = None
        self.index: FeedIndex | None = None
        self.error: str | None = None

    def run(self) -> None:
        try:
            self.token = public_token(self.ctx.client)
            self.index = build_index(self.ctx.client, self.token, self.days, self.ctx.deadline)
        except (ConnectionError, BudgetExceeded, ValueError) as err:
            self.error = str(err)[:200]


def _run_jobs(ctx: RunContext, feed: _FeedLane | None, profiles: dict[str, dict[str, Any]],
              opts: RunOptions) -> dict[str, ModuleResult]:
    if feed is None:
        return {}
    feed.join(max(0.0, ctx.deadline - time.monotonic()) + 5)
    if feed.index is None or feed.token is None:
        reason = f"NAV job feed unavailable: {feed.error or 'not read within the time budget'}"
        return {p["orgnr"]: skipped("jobs", BLOCKED, reason) for p in profiles.values()}
    index, token = feed.index, feed.token
    targets = [p for p in profiles.values() if p.get("entity_kind") == "enhet"]

    def one(profile: dict[str, Any]) -> tuple[str, ModuleResult]:
        if not ctx.time_left():
            return profile["orgnr"], skipped("jobs", BLOCKED, "run time budget reached before job ads were checked")
        if not ctx.can_afford(MAX_JOB_DETAILS):
            return profile["orgnr"], skipped("jobs", BLOCKED, "run request budget exhausted")
        try:
            return profile["orgnr"], jobs_for_company(ctx.client, token, index, profile["orgnr"],
                                                      profile.get("name") or "", set(profile.get("subunit_orgnrs", [])))
        except Exception as err:  # isolate the connector
            return profile["orgnr"], ModuleResult("jobs", FAILED, (), f"{type(err).__name__}: {err}"[:300])

    with ThreadPoolExecutor(max_workers=max(1, min(8, opts.workers))) as pool:
        return dict(pool.map(one, targets))


def _with_jobs(profile: dict[str, Any], jobs: dict[str, ModuleResult], opts: RunOptions) -> dict[str, Any]:
    if profile["status"] in ("invalid_input", "not_found", "lookup_failed"):
        return profile
    if profile.get("entity_kind") != "enhet":
        return attach_module(profile, skipped("jobs", NOT_APPLICABLE, f"entity is {profile['status']}"))
    if not opts.jobs:
        return attach_module(profile, skipped("jobs", BLOCKED, "jobs connector disabled for this run"))
    return attach_module(profile, jobs.get(profile["orgnr"]) or skipped("jobs", BLOCKED, "job ads not checked"))


def _with_history(profile: dict[str, Any], lane: _HistoryLane | None) -> dict[str, Any]:
    if profile["status"] in ("invalid_input", "not_found", "lookup_failed"):
        return profile
    if profile["status"] in ("deleted", "subunit"):
        return attach_module(profile, skipped("filing_history", NOT_APPLICABLE, f"entity is {profile['status']}"))
    if lane is None:
        return attach_module(profile, skipped("filing_history", BLOCKED, "filing history disabled for this run"))
    result = lane.results.get(profile["orgnr"])
    if result is None:
        result = skipped("filing_history", BLOCKED,
                         "rate-limited source (about 30 requests/minute) not reached within the run budget")
    return attach_module(profile, result)


def _refresh(profile: dict[str, Any], opts: RunOptions) -> dict[str, Any]:
    if profile["status"] == "invalid_input":
        return store.merge(None, profile)
    previous = (opts.previous or {}).get(profile["orgnr"])
    if previous is None and opts.profiles_dir:
        previous = store.load(opts.profiles_dir, profile["orgnr"])
    merged = store.merge(previous, profile)
    if opts.profiles_dir:
        store.save(opts.profiles_dir, merged)
    return merged


def _report(envelopes: list[dict[str, Any]], inputs: list[str], client: HttpClient, t0: float,
            run: dict[str, Any], opts: RunOptions) -> dict[str, Any]:
    orgs = [e["organisation_number"] for e in envelopes]
    claim_states = [c["availability"] for e in envelopes for c in e["claims"]]
    checks = {
        "one_envelope_per_input": len(envelopes) == len(inputs),
        "expected_count_matches": opts.expected_count is None or len(envelopes) == opts.expected_count,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
        "all_claim_states_valid": all(s in STATES for s in claim_states),
        "all_runs_terminal": all(e["run"]["terminal_status"] in ("completed", "failed") for e in envelopes),
    }
    per_field: dict[str, Counter] = {}
    for e in envelopes:
        for c in e["claims"]:
            per_field.setdefault(c["field"], Counter())[c["availability"]] += 1
    runtimes = sorted(e["operations"].get("runtime_ms", 0) for e in envelopes)
    return {
        **run,
        "agent_version": __version__,
        "input_count": len(inputs),
        "emitted_envelopes": len(envelopes),
        "validation": {"passed": all(checks.values()), "checks": checks},
        "company_status_counts": dict(Counter(e["company_status"] for e in envelopes)),
        "section_coverage": {s: sum(1 for e in envelopes if e["sections"][s]["state"] == "available")
                             for s in (envelopes[0]["sections"] if envelopes else {})},
        "field_availability": {f: dict(c) for f, c in sorted(per_field.items())},
        "profiles_with_changes": sum(1 for e in envelopes if e["changes"]),
        "operations": {**client.metrics(), "request_budget": client.budget,
                       "runtime_seconds": round(time.monotonic() - t0, 1),
                       "company_runtime_p50_ms": runtimes[len(runtimes) // 2] if runtimes else None,
                       "company_runtime_p95_ms": runtimes[min(len(runtimes) - 1, int(len(runtimes) * 0.95))]
                       if runtimes else None,
                       "third_party_cost_usd": 0.0},
    }


def _write(out_dir: Path, envelopes: list[dict[str, Any]], report: dict[str, Any]) -> None:
    from signalpost.report_html import write_report  # local import keeps the runner light

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "envelopes.jsonl.tmp"
    with tmp.open("w", encoding="utf-8") as handle:
        for envelope in envelopes:
            handle.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(out_dir / "envelopes.jsonl")
    (out_dir / "run_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(out_dir / "report.html", envelopes, report)
