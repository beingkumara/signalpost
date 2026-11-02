"""Hand-check workflow: sample published entries per source type, then score the checked sheet.

1. `signalpost audit-sample --envelopes out/envelopes.jsonl --out audit/sheet.csv` writes up to N entries per
   source type (website, social, news, NAV jobs, website jobs, careers page) with their source link,
   confidence and reason. A person opens each link and fills `verdict` with right, wrong or unsure.
2. `signalpost audit-score --sheet audit/sheet.csv --out ACCURACY.md` publishes how often each source type and
   confidence level is right. Unsure rows are reported but left out of the precision.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

COLUMNS: tuple[str, ...] = ("source_type", "organisation_number", "company", "value", "check_url", "confidence", "reason",
           "verdict", "checker_note")
VERDICTS = ("right", "wrong", "unsure")
WHAT_TO_CHECK = {
    "official_website": "the site belongs to this company (not a parent group, a sibling or a directory)",
    "social_profile": "the account belongs to this company, not a parent group, franchise chain or web agency",
    "website_news": "a dated article (not a home or service page) that is about this company",
    "nav_job": "an open job ad on NAV whose employer is this company or one of its workplaces",
    "website_job": "an open job ad for this company (not a group-wide or login-only page)",
    "careers_page": "a careers page that shows at least one opening for this company",
}


def _rows(envelope: dict[str, Any]) -> Iterator[dict[str, str]]:
    base = {"organisation_number": envelope["organisation_number"], "company": envelope.get("name") or ""}
    evidence = {e["id"]: e for e in envelope.get("evidence", [])}
    for claim in envelope.get("claims", []):
        if claim.get("availability") != "available":
            continue
        field, value = claim["field"], claim["value"]
        source = evidence.get((claim.get("evidence_ids") or [""])[0], {}).get("source_url") or ""
        level = "high" if claim.get("confidence", 0) >= 0.9 else "medium"
        if field == "official_website":
            yield {**base, "source_type": "official_website", "value": value.get("final_url", ""),
                   "check_url": value.get("final_url", ""), "confidence": level,
                   "reason": f"{value.get('verification')} via {value.get('origin')}; proof on {source}"}
        elif field == "social_profiles":
            for network, url in sorted(value.items()):
                yield {**base, "source_type": "social_profile", "value": f"{network}: {url}", "check_url": url,
                       "confidence": level, "reason": f"linked from the verified website {source}"}
        elif field in ("website_news", "job_postings", "website_job_postings"):
            kind = {"website_news": "website_news", "job_postings": "nav_job",
                    "website_job_postings": "website_job"}[field]
            for item in value:
                yield {**base, "source_type": kind, "value": item.get("title", ""), "check_url": item.get("url", ""),
                       "confidence": item.get("confidence", level), "reason": item.get("reason", "")}
        elif field == "careers_page":
            yield {**base, "source_type": "careers_page", "value": value, "check_url": value, "confidence": level,
                   "reason": "careers link on the verified site, with an opening for this company"}


def sample(envelopes_path: Path, per_type: int, seed: int, per_company: int = 1) -> list[dict[str, str]]:
    """Up to `per_type` entries per source type, at most `per_company` from any one company, so a company with
    many accounts or posts cannot dominate a source type's precision."""
    by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    with envelopes_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                for row in _rows(json.loads(line)):
                    by_type[row["source_type"]].append(row)
    rng = random.Random(seed)
    picked: list[dict[str, str]] = []
    for kind in WHAT_TO_CHECK:
        rows = list(by_type.get(kind, []))
        rng.shuffle(rows)
        taken: dict[str, int] = defaultdict(int)
        chosen: list[dict[str, str]] = []
        for row in rows:
            org = row["organisation_number"]
            if len(chosen) < per_type and taken[org] < per_company:
                taken[org] += 1
                chosen.append(row)
        picked += chosen
    return [{**{c: "" for c in COLUMNS}, **row} for row in picked]


def write_sheet(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    bad = [r for r in rows if (r.get("verdict") or "").strip().lower() not in (*VERDICTS, "")]
    if bad:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)} (row for {bad[0].get('organisation_number')})")
    return rows


def score(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Precision per source type and per confidence level, over rows with a right/wrong verdict."""
    out: dict[str, Any] = {}
    for key in [*WHAT_TO_CHECK, *(f"{k}:{lv}" for k in WHAT_TO_CHECK for lv in ("high", "medium"))]:
        kind, _, level = key.partition(":")
        subset = [r for r in rows if r["source_type"] == kind and (not level or r["confidence"] == level)]
        verdicts = [(r.get("verdict") or "").strip().lower() for r in subset]
        right, wrong, unsure = verdicts.count("right"), verdicts.count("wrong"), verdicts.count("unsure")
        if right + wrong + unsure:
            out[key] = {"checked": right + wrong, "right": right, "wrong": wrong, "unsure": unsure,
                        "unchecked": verdicts.count(""),
                        "precision": round(right / (right + wrong), 3) if right + wrong else None}
    return out


def markdown(scores: dict[str, Any], rows: list[dict[str, str]], run_label: str) -> str:
    intro = (f"Hand-checked sample from {run_label}. Each entry was opened at its source link and marked right, "
             "wrong or unsure. Precision is right / (right + wrong).\n")
    lines = ["# How often each source type is right\n", intro,
             "| Source type | What counts as right | Checked | Right | Wrong | Unsure | Precision |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for kind, rule in WHAT_TO_CHECK.items():
        s = scores.get(kind)
        if s:
            pct = f"{s['precision']:.0%}" if s["precision"] is not None else "n/a"
            lines.append(f"| {kind.replace('_', ' ')} | {rule} | {s['checked']} | {s['right']} | {s['wrong']} | "
                         f"{s['unsure']} | {pct} |")
    levels = [(k, v) for k, v in scores.items() if ":" in k]
    if levels:
        lines += ["\n## By confidence level\n", "| Source type | Confidence | Checked | Precision |", "|---|---|---:|---:|"]
        for key, s in levels:
            kind, level = key.split(":")
            pct = f"{s['precision']:.0%}" if s["precision"] is not None else "n/a"
            lines.append(f"| {kind.replace('_', ' ')} | {level} | {s['checked']} | {pct} |")
    wrong = [r for r in rows if (r.get("verdict") or "").strip().lower() == "wrong"]
    if wrong:
        lines += ["\n## Wrong entries and what we changed\n"]
        lines += [f"- {r['source_type']}: {r['company']} ({r['organisation_number']}): {r['value']}. "
                  f"{r.get('checker_note') or ''}".rstrip() for r in wrong]
    return "\n".join(lines) + "\n"
