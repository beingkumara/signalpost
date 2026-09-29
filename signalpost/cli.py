"""Command line entry point: `python -m signalpost <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from signalpost import config, seed, store, updater
from signalpost.http_client import HttpClient, utc_now_iso
from signalpost.profile_builder import build_profile
from signalpost.runner import MAX_REQUESTS_PER_COMPANY, RunOptions, run_batch


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signalpost", description="Evidence-backed Norwegian company profiles.")
    parser.add_argument("--profiles-dir", type=Path, default=Path("profiles"), help="where profiles are stored")
    parser.add_argument("--budget", type=int, default=config.DEFAULT_REQUEST_BUDGET, help="max outbound requests")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="profile many companies (the daily-test command)")
    run.add_argument("orgnrs", nargs="*", help="organisation numbers")
    run.add_argument("--input", type=Path, help="file with one org number per line (or a JSON list)")
    run.add_argument("--out", type=Path, default=Path("out"), help="writes results.jsonl and run_report.json")
    run.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
    run.add_argument("--max-minutes", type=float, default=40.0)
    run.add_argument("--no-website", action="store_true", help="skip company websites")
    run.set_defaults(handler=_cmd_run)

    one = sub.add_parser("profile", help="profile one company and print it")
    one.add_argument("orgnr")
    one.add_argument("--text", action="store_true", help="print the explanation instead of JSON")
    one.add_argument("--no-website", action="store_true")
    one.set_defaults(handler=_cmd_profile)

    show = sub.add_parser("show", help="print a stored profile's explanation")
    show.add_argument("orgnr")
    show.set_defaults(handler=_cmd_show)

    upd = sub.add_parser("update", help="refresh stored profiles that changed upstream or went stale")
    upd.add_argument("--since", help="UTC timestamp, e.g. 2026-09-01T00:00:00Z (default: last sync)")
    upd.add_argument("--stale-days", type=int, default=config.DEFAULT_STALE_DAYS)
    upd.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
    upd.add_argument("--max-minutes", type=float, default=40.0)
    upd.add_argument("--out", type=Path, default=Path("out/update"))
    upd.set_defaults(handler=_cmd_update)

    sd = sub.add_parser("seed", help="sample org numbers from the registry")
    sd.add_argument("--count", type=int, default=1000)
    sd.add_argument("--seed", type=int, default=42)
    sd.add_argument("--out", type=Path, default=Path("data/seed_orgnrs.txt"))
    sd.set_defaults(handler=_cmd_seed)

    exp = sub.add_parser("export", help="write all stored profiles to one JSONL file")
    exp.add_argument("--out", type=Path, default=Path("out/all_profiles.jsonl"))
    exp.set_defaults(handler=_cmd_export)
    return parser


def _read_inputs(args: argparse.Namespace) -> list[str]:
    items = list(args.orgnrs)
    if args.input:
        text = args.input.read_text(encoding="utf-8").strip()
        if text.startswith("["):
            items.extend(str(x) for x in json.loads(text))
        else:
            items.extend(line.split(",")[0].strip() for line in text.splitlines()
                         if line.strip() and not line.startswith("#"))
    return items


def _cmd_run(args: argparse.Namespace) -> int:
    inputs = _read_inputs(args)
    if not inputs:
        print("No org numbers given. Pass them as arguments or with --input FILE.", file=sys.stderr)
        return 2
    opts = RunOptions(args.profiles_dir, args.out, args.workers, args.max_minutes, not args.no_website)
    report = run_batch(HttpClient(args.budget), inputs, opts)
    print(json.dumps(report, indent=2))
    return 0


def _cmd_profile(args: argparse.Namespace) -> int:
    profile = build_profile(HttpClient(args.budget), args.orgnr, with_website=not args.no_website)
    if profile["status"] != "invalid_input":
        profile = store.merge(store.load(args.profiles_dir, profile["orgnr"]), profile)
        store.save(args.profiles_dir, profile)
    if args.text:
        _print_explanation(profile)
    else:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0 if profile["status"] not in ("invalid_input", "lookup_failed") else 1


def _cmd_show(args: argparse.Namespace) -> int:
    profile = store.load(args.profiles_dir, args.orgnr.replace(" ", ""))
    if profile is None:
        print(f"No stored profile for {args.orgnr}. Run: python -m signalpost profile {args.orgnr}", file=sys.stderr)
        return 1
    _print_explanation(profile)
    return 0


def _print_explanation(profile: dict) -> None:
    explanation = profile.get("explanation", {})
    print(explanation.get("summary", ""))
    for note in explanation.get("notes", []) + explanation.get("changes_since_last_check", []):
        print(f"  - {note}")
    print(f"Checked {profile.get('checked_at')}. Sources:")
    for row in profile.get("sources_checked", []):
        print(f"  - {row.get('source')}: {row.get('url', '')}")


def _cmd_update(args: argparse.Namespace) -> int:
    client = HttpClient(args.budget)
    stored = {o: (store.load(args.profiles_dir, o) or {}).get("checked_at", "")
              for o in store.stored_orgnrs(args.profiles_dir)}
    if not stored:
        print("No stored profiles to update.", file=sys.stderr)
        return 1
    state = updater.read_state(args.profiles_dir)
    since = args.since or state.get("last_sync") or min(stored.values())
    sync_started = utc_now_iso()
    changed, stats = updater.changed_orgnrs(client, updater.feed_timestamp(since))
    plan = updater.plan_refresh(stored, changed, args.stale_days, datetime.now(timezone.utc))
    capacity = max(0, client.remaining // MAX_REQUESTS_PER_COMPANY)
    todo = plan[:capacity]
    print(f"{len(changed & stored.keys())} stored profiles changed upstream since {since}; "
          f"refreshing {len(todo)} of {len(plan)} planned.", file=sys.stderr)
    opts = RunOptions(args.profiles_dir, args.out, args.workers, args.max_minutes, True)
    report = run_batch(client, todo, opts) if todo else {"companies_completed": 0}
    if len(todo) == len(plan):
        updater.write_state(args.profiles_dir, {"last_sync": sync_started, **stats})
    print(json.dumps({**report, "feed": stats, "deferred": len(plan) - len(todo)}, indent=2))
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    orgnrs = seed.sample_orgnrs(HttpClient(args.budget), args.count, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(orgnrs) + "\n", encoding="utf-8")
    print(f"Wrote {len(orgnrs)} org numbers to {args.out}", file=sys.stderr)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    orgnrs = store.stored_orgnrs(args.profiles_dir)
    with args.out.open("w", encoding="utf-8") as handle:
        for orgnr in orgnrs:
            profile = store.load(args.profiles_dir, orgnr)
            if profile:
                handle.write(json.dumps(profile, ensure_ascii=False) + "\n")
    print(f"Exported {len(orgnrs)} profiles to {args.out}", file=sys.stderr)
    return 0
