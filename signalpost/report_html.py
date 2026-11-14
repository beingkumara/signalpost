"""HTML report (page template in report_template.html): find, compare and verify company facts on desktop and mobile.

All values are rendered with textContent (no innerHTML of data), so page content cannot inject markup.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from signalpost.explain import change_notes

SECTION_TITLES = {
    "legal_identity_and_brand": "Identity & brand",
    "annual_accounts": "Annual accounts",
    "leadership_and_workplaces": "Leadership & workplaces",
    "website_and_profiles": "Website & profiles",
    "hiring_and_activity": "Hiring & activity",
}


def _slim(envelope: dict[str, Any]) -> dict[str, Any]:
    summary = envelope.get("summary") or {}
    return {
        "org": envelope["organisation_number"], "name": envelope.get("name"), "status": envelope.get("company_status"),
        "sections": {k: v["state"] for k, v in envelope.get("sections", {}).items()},
        "summary": summary.get("summary"),
        "statements": summary.get("statements") or [],
        "narrative": (summary.get("narrative") or {}).get("sentences") or [],
        "claims": [{k: c.get(k) for k in ("id", "field", "section", "label", "value", "availability", "reason",
                                          "evidence_ids", "as_of", "stale", "note")} for c in envelope.get("claims", [])],
        "evidence": {e["id"]: {k: e.get(k) for k in ("source_url", "human_url", "source_class", "retrieved_at",
                                                      "content_sha256", "claim_span", "json_path")}
                     for e in envelope.get("evidence", [])},
        "changes": [{**{k: c.get(k) for k in ("label", "field", "change", "change_type", "old", "new", "detected_at")},
                     "note": change_notes([c])[0]} for c in envelope.get("changes", [])],
        "checked": envelope.get("refresh", {}).get("checked_at"),
    }


DETAIL_DIR = "report_files"
_COMPARE_FIELDS = ("organization_form", "business_address", "industry_codes", "employees", "revenue", "net_profit",
                   "equity", "ceo", "board_chair", "official_website", "social_profiles", "website_news",
                   "job_postings")


def _index_row(envelope: dict[str, Any]) -> dict[str, Any]:
    """The few fields the list, search and filters need. Everything else loads per company."""
    # A field repeats once per accounting year; sorting by as_of makes the newest year win.
    claims = sorted(envelope.get("claims", []), key=lambda c: str(c.get("as_of") or ""))
    by = {c["field"]: c.get("value") for c in claims if c.get("availability") == "available"}
    address = by.get("business_address") if isinstance(by.get("business_address"), dict) else {}
    sections = {k: v["state"] for k, v in envelope.get("sections", {}).items()}
    codes = by.get("industry_codes")
    industry = codes[0] if isinstance(codes, list) and codes and isinstance(codes[0], dict) else {}
    return {"org": envelope["organisation_number"], "name": envelope.get("name"),
            "status": envelope.get("company_status"),
            "place": address.get("municipality") or address.get("city"),
            "industry": (industry or {}).get("description_en") or (industry or {}).get("description"),
            "sections": sections, "cov": sum(1 for v in sections.values() if v == "available"),
            "rev": _amount(by.get("revenue")), "emp": by.get("employees") if isinstance(by.get("employees"), int) else None,
            "web": 1 if "official_website" in by else 0, "news": 1 if by.get("website_news") else 0,
            "jobs": 1 if by.get("job_postings") or by.get("website_job_postings") else 0,
            "ext": sum(1 for f in ("official_website", "social_profiles", "website_news", "job_postings") if by.get(f))}


def _amount(value: Any) -> float | None:
    return value.get("amount") if isinstance(value, dict) and isinstance(value.get("amount"), (int, float)) else None


def _js(value: Any) -> str:
    """JSON that is safe inside a <script> element and as a standalone script file."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def write_report(path: Path, envelopes: list[dict[str, Any]], report: dict[str, Any]) -> None:
    """report.html with a small embedded index, plus one script file per company in report_files/.
    The detail files are written to a temporary directory first, then swapped in, so a crash never leaves
    a half-written report and a re-run removes files for companies no longer in the batch."""
    detail_dir = path.parent / DETAIL_DIR
    tmp_dir = path.parent / (DETAIL_DIR + ".tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    for envelope in envelopes:
        org = str(envelope["organisation_number"])
        if org.isdigit() and len(org) == 9:
            (tmp_dir / f"{org}.js").write_text(f"window.SP_LOAD({_js(_slim(envelope))});\n", encoding="utf-8")
    meta = {"run_id": report.get("run_id"), "completed_at": report.get("completed_at"),
            "companies": report.get("emitted_envelopes", len(envelopes)), "sections": SECTION_TITLES,
            "requests": (report.get("operations") or {}).get("requests"), "compare": _COMPARE_FIELDS,
            "dir": DETAIL_DIR}
    fills = {"__INDEX__": _js([_index_row(e) for e in envelopes]), "__META__": _js(meta)}
    html = re.sub(r"__INDEX__|__META__", lambda m: fills[m.group(0)], _TEMPLATE, count=2)
    tmp_html = path.with_suffix(".html.tmp")
    tmp_html.write_text(html, encoding="utf-8")
    shutil.rmtree(detail_dir, ignore_errors=True)
    tmp_dir.replace(detail_dir)
    tmp_html.replace(path)


_TEMPLATE = (Path(__file__).with_name("report_template.html")).read_text(encoding="utf-8")
