"""Batch execution with concurrency, a wall-clock deadline and the request budget."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signalpost import __version__, store
from signalpost.http_client import HttpClient, utc_now_iso
from signalpost.profile_builder import build_profile

# Worst case per company: entity + sub-unit fallback, roles, accounts, sub-units, robots.txt, homepage.
MAX_REQUESTS_PER_COMPANY = 7


@dataclass(frozen=True)
class RunOptions:
    profiles_dir: Path
    out_dir: Path | None
    workers: int
    max_minutes: float
    with_website: bool
    quiet: bool = False


def run_batch(client: HttpClient, inputs: list[str], opts: RunOptions) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + opts.max_minutes * 60
    results: dict[int, dict[str, Any]] = {}
    skipped: list[str] = []

    with ThreadPoolExecutor(max_workers=opts.workers) as pool:
        futures = {}
        for index, raw in enumerate(inputs):
            futures[pool.submit(_process_one, client, raw, opts, deadline)] = index
        for done, future in enumerate(as_completed(futures), start=1):
            index = futures[future]
            profile = future.result()
            if profile is None:
                skipped.append(inputs[index])
                continue
            results[index] = profile
            if not opts.quiet:
                _progress(done, len(inputs), profile, client)

    ordered = [results[i] for i in sorted(results)]
    report = _report(ordered, skipped, client, time.monotonic() - started)
    if opts.out_dir:
        _write_outputs(opts.out_dir, ordered, report)
    return report


def _process_one(client: HttpClient, raw: str, opts: RunOptions, deadline: float) -> dict[str, Any] | None:
    if time.monotonic() > deadline or client.remaining < MAX_REQUESTS_PER_COMPANY:
        return None
    try:
        current = build_profile(client, raw, with_website=opts.with_website)
    except Exception as err:  # one bad company must not sink the batch; record it instead
        return {"orgnr": raw.strip(), "status": "lookup_failed", "warnings": [f"unexpected error: {err!r}"],
                "facts": [], "fields": {}, "checked_at": utc_now_iso(),
                "explanation": {"summary": "Processing failed; no facts published.", "notes": []}}
    if current["status"] == "invalid_input":
        return current
    merged = store.merge(store.load(opts.profiles_dir, current["orgnr"]), current)
    store.save(opts.profiles_dir, merged)
    return merged


def _progress(done: int, total: int, profile: dict[str, Any], client: HttpClient) -> None:
    name = profile.get("name") or "-"
    print(f"[{done}/{total}] {profile.get('orgnr')} {profile.get('status')} {name} "
          f"(requests used {client.used})", file=sys.stderr)


def _report(profiles: list[dict[str, Any]], skipped: list[str], client: HttpClient, seconds: float) -> dict[str, Any]:
    statuses = Counter(p.get("status") for p in profiles)
    fact_counts = [len(p.get("facts", [])) for p in profiles]
    return {
        "agent_version": __version__,
        "finished_at": utc_now_iso(),
        "companies_requested": len(profiles) + len(skipped),
        "companies_completed": len(profiles),
        "skipped_for_limits": skipped,
        "status_counts": dict(statuses),
        "profiles_changed": sum(1 for p in profiles if p.get("changes_this_run")),
        "avg_facts_per_profile": round(sum(fact_counts) / len(fact_counts), 1) if fact_counts else 0,
        "outbound_requests": client.used,
        "duration_seconds": round(seconds, 1),
        "external_api_cost_usd": 0.0,
    }


def _write_outputs(out_dir: Path, profiles: list[dict[str, Any]], report: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        for profile in profiles:
            handle.write(json.dumps(profile, ensure_ascii=False) + "\n")
    (out_dir / "run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
