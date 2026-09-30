"""Command line entry point: `python -m signalpost <command>`."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from signalpost import config, store, updater
from signalpost.envelope import build_envelope
from signalpost.http_client import HttpClient, utc_now_iso
from signalpost.inputs import read_batch, unique_in_order
from signalpost.runner import RunOptions, run_batch

ENV_BUDGET = "SIGNALPOST_REQUEST_BUDGET"
ENV_MINUTES = "SIGNALPOST_MAX_MINUTES"


def load_env_file(path: Path = Path(".env")) -> list[str]:
    """Load KEY=VALUE lines from a local, git-ignored .env file. Existing environment values win."""
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    load_env_file()
    return args.handler(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signalpost", description="Evidence-backed Norwegian company profiles.")
    parser.add_argument("--profiles-dir", type=Path, default=Path("state/profiles"),
                        help="stored profiles used as the previous snapshot for refresh diffs")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="research a batch and write one envelope per input (evaluator command)")
    run.add_argument("orgnrs", nargs="*", help="organisation numbers")
    run.add_argument("--input", type=Path, help="JSONL rows with organisation_number, JSON list, CSV or text")
    run.add_argument("--out", type=Path, default=Path("out"), help="writes envelopes.jsonl, run_report.json, report.html")
    run.add_argument("--previous", type=Path, help="previous envelopes.jsonl to diff against")
    run.add_argument("--run-id", default="")
    run.add_argument("--expected-count", type=int)
    _budget_args(run)
    run.set_defaults(handler=_cmd_run)

    one = sub.add_parser("profile", help="research one company and print its envelope")
    one.add_argument("orgnr")
    one.add_argument("--text", action="store_true", help="print the explanation instead of JSON")
    _budget_args(one)
    one.set_defaults(handler=_cmd_profile)

    show = sub.add_parser("show", help="print a stored profile's explanation")
    show.add_argument("orgnr")
    show.set_defaults(handler=_cmd_show)

    upd = sub.add_parser("update", help="refresh stored profiles that changed upstream or went stale")
    upd.add_argument("--since", help="UTC timestamp, e.g. 2026-09-01T00:00:00Z (default: last sync)")
    upd.add_argument("--stale-days", type=int, default=config.DEFAULT_STALE_DAYS)
    upd.add_argument("--out", type=Path, default=Path("out/update"))
    _budget_args(upd)
    upd.set_defaults(handler=_cmd_update)

    smp = sub.add_parser("sample", help="write a reproducible test batch from the public company universe")
    smp.add_argument("--universe", type=Path, default=Path("data/signalpost-universe.jsonl.gz"))
    smp.add_argument("--count", type=int, default=100)
    smp.add_argument("--seed", type=int, default=20260823)
    smp.add_argument("--out", type=Path, default=Path("data/smoke-companies.jsonl"))
    smp.set_defaults(handler=_cmd_sample)
    return parser


def _budget_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--budget", type=int, default=_env_int(ENV_BUDGET),
                   help=f"max outbound requests (default ${ENV_BUDGET}, else {config.REQUESTS_PER_COMPANY} per company "
                        f"+ {config.FIXED_RUN_REQUESTS})")
    p.add_argument("--max-minutes", type=float, default=_env_float(ENV_MINUTES, config.DEFAULT_MAX_MINUTES))
    p.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
    p.add_argument("--website-pages", type=int, default=4, help="extra same-site pages per website")
    p.add_argument("--no-website-discovery", action="store_true")
    p.add_argument("--no-jobs", action="store_true")
    p.add_argument("--no-history", action="store_true", help="skip the rate-limited filing-history lane")
    p.add_argument("--no-search", action="store_true", help="never call a paid search API even if a key is set")
    p.add_argument("--search-max", type=int, default=None,
                   help="max paid searches per run (default $SIGNALPOST_SEARCH_MAX or 150)")
    p.add_argument("--no-llm", action="store_true", help="skip AI-written summaries even if ANTHROPIC_API_KEY is set")
    p.add_argument("--quiet", action="store_true")


def _env_int(name: str) -> int | None:
    try:
        return int(os.environ[name]) if os.environ.get(name) else None
    except ValueError:
        return None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name]) if os.environ.get(name) else default
    except ValueError:
        return default


def _options(args: argparse.Namespace, out: Path, **extra) -> RunOptions:
    progress = None if args.quiet else (lambda line: print(line, file=sys.stderr))
    return RunOptions(out, args.profiles_dir, args.workers, args.max_minutes, website_pages=args.website_pages,
                      discovery=not args.no_website_discovery, jobs=not args.no_jobs, history=not args.no_history,
                      search=not args.no_search, search_max=args.search_max, narratives=not args.no_llm,
                      progress=progress, **extra)


def _budget(args: argparse.Namespace, companies: int) -> int:
    return args.budget or config.REQUESTS_PER_COMPANY * companies + config.FIXED_RUN_REQUESTS


def _cmd_run(args: argparse.Namespace) -> int:
    inputs = list(args.orgnrs) + (read_batch(args.input) if args.input else [])
    inputs, duplicates = unique_in_order([i for i in inputs if i])
    if not inputs:
        print("No organisation numbers given. Use --input FILE or pass numbers as arguments.", file=sys.stderr)
        return 2
    if duplicates:
        print(f"Ignored {len(duplicates)} duplicate input(s).", file=sys.stderr)
    previous = _load_previous(args.previous) if args.previous else None
    opts = _options(args, args.out, run_id=args.run_id, previous=previous, expected_count=args.expected_count)
    report = run_batch(HttpClient(_budget(args, len(inputs))), inputs, opts)
    print(json.dumps({k: report[k] for k in ("run_id", "input_count", "emitted_envelopes", "validation",
                                             "section_coverage", "operations")}, indent=2))
    return 0 if report["validation"]["passed"] else 1


def _load_previous(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            env = json.loads(line)
            out[env["organisation_number"]] = store.from_envelope(env)
    return out


def _cmd_profile(args: argparse.Namespace) -> int:
    out = Path("out/single")
    opts = _options(args, out, run_id=f"single-{int(time.time())}")
    opts = RunOptions(**{**opts.__dict__, "progress": None})
    report = run_batch(HttpClient(_budget(args, 1)), [args.orgnr], opts)
    envelope = json.loads((out / "envelopes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    if args.text:
        _print_text(envelope)
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0 if envelope["state"] != "failed" and report["validation"]["passed"] else 1


def _print_text(envelope: dict) -> None:
    summary = envelope.get("summary", {})
    print(summary.get("summary", ""))
    for statement in summary.get("statements", []):
        print(f"  * {statement['text']}")
    for note in summary.get("notes", []) + summary.get("changes_since_last_check", []):
        print(f"  - {note}")
    for name, section in envelope.get("sections", {}).items():
        print(f"  [{section['state']}] {name}: {section['available_claims']} claims")


def _cmd_show(args: argparse.Namespace) -> int:
    profile = store.load(args.profiles_dir, args.orgnr.replace(" ", ""))
    if profile is None:
        print(f"No stored profile for {args.orgnr}. Run: python -m signalpost profile {args.orgnr}", file=sys.stderr)
        return 1
    _print_text(build_envelope(profile, {"run_id": "stored"}))
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    stored = {o: (store.load(args.profiles_dir, o) or {}).get("checked_at", "")
              for o in store.stored_orgnrs(args.profiles_dir)}
    if not stored:
        print("No stored profiles to update.", file=sys.stderr)
        return 1
    client = HttpClient(_budget(args, len(stored)))
    state = updater.read_state(args.profiles_dir)
    since = args.since or state.get("last_sync") or min(stored.values())
    sync_started = utc_now_iso()
    changed, stats = updater.changed_orgnrs(client, updater.feed_timestamp(since))
    plan = updater.plan_refresh(stored, changed, args.stale_days, datetime.now(timezone.utc))
    print(f"{len(changed & stored.keys())} stored profiles changed upstream since {since}; "
          f"refreshing {len(plan)}.", file=sys.stderr)
    report = run_batch(client, plan, _options(args, args.out, run_id=f"update-{int(time.time())}")) if plan else {}
    updater.write_state(args.profiles_dir, {"last_sync": sync_started, **stats})
    print(json.dumps({"refreshed": len(plan), "feed": stats,
                      "profiles_with_changes": report.get("profiles_with_changes", 0)}, indent=2))
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    with gzip.open(args.universe, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    chosen = random.Random(args.seed).sample(rows, args.count)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in chosen), encoding="utf-8")
    print(f"Wrote {len(chosen)} companies to {args.out}", file=sys.stderr)
    return 0
