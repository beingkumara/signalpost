"""Profile persistence and refresh diffs.

A refresh is idempotent: the same source snapshot produces no changes and no duplicate records.
A source that fails or is skipped keeps its last supported values, marked stale.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from signalpost.explain import change_notes

MAX_CHANGE_HISTORY = 200
_FAILED_STATUSES = {"lookup_failed"}
_MODULE_CLASSES = {
    "roles": "official_roles", "annual_accounts": "official_annual_accounts",
    "filing_history": "official_annual_account_copies", "website": "company_owned", "jobs": "public_job_board",
}
_MODULE_FIELDS = {"workplaces": {"subunit_count", "subunits"}}
MATERIAL_FIELDS = {
    "status", "name", "organization_form", "business_address", "ceo", "board_chair", "board_members", "auditor",
    "accounts_period", "revenue", "net_profit", "equity", "official_website", "bankrupt", "under_liquidation",
    "under_forced_dissolution", "deleted_date", "share_capital", "job_postings", "hiring", "subunit_count",
}
_CHANGE_TYPES = {
    "roles": "role_change", "ceo": "role_change", "board_chair": "role_change", "board_members": "role_change",
    "auditor": "role_change", "accountant": "role_change", "owner": "role_change",
    "accounts_period": "new_filing", "filing_history": "new_filing", "job_postings": "jobs_change",
    "hiring": "jobs_change", "subunits": "location_change", "subunit_count": "location_change",
    "business_address": "address_change", "website_description": "changed_description",
    "official_website": "website_change", "name": "name_change", "status": "status_change",
    "registry_events": "new_event", "website_news": "new_event", "social_profiles": "profile_change",
}


def profile_path(directory: Path, orgnr: str) -> Path:
    return directory / f"{orgnr}.json"


def load(directory: Path, orgnr: str) -> dict[str, Any] | None:
    path = profile_path(directory, orgnr)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save(directory: Path, profile: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = profile_path(directory, profile["orgnr"])
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def stored_orgnrs(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if p.stem.isdigit() and len(p.stem) == 9)


def from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a minimal previous profile from an envelope (for `--previous envelopes.jsonl`)."""
    evidence = {e["id"]: e for e in envelope.get("evidence", [])}
    facts = []
    for claim in envelope.get("claims", []):
        if claim.get("availability") != "available":
            continue
        ev = evidence.get((claim.get("evidence_ids") or [None])[0], {})
        facts.append({"field": claim["field"], "value": claim["value"], "label": claim.get("label"),
                      "retrieved_at": ev.get("retrieved_at"), "first_seen": claim.get("first_seen"),
                      "as_of": claim.get("as_of"),
                      "source": {"name": ev.get("source_name"), "url": ev.get("source_url"),
                                 "source_class": ev.get("source_class"), "content_sha256": ev.get("content_sha256")}})
    refresh = envelope.get("refresh", {})
    return {"orgnr": envelope["organisation_number"], "status": envelope.get("company_status"),
            "checked_at": refresh.get("checked_at"), "first_checked_at": refresh.get("first_checked_at"),
            "revision": refresh.get("revision", 1), "facts": facts, "change_history": []}


def merge(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Return a new profile combining this refresh with history from the previous one."""
    now = current["checked_at"]
    if previous is None:
        facts = [{**f, "first_seen": f.get("first_seen") or now} for f in current["facts"]]
        return {**current, "facts": facts, "first_checked_at": now, "last_changed_at": now,
                "revision": 1, "changes_this_run": [], "change_history": []}
    if current["status"] in _FAILED_STATUSES:
        return {**previous, "last_attempt_at": now, "last_attempt_status": current["status"],
                "warnings": current["warnings"]}

    facts = [*current["facts"], *_carry_forward(previous, current)]
    facts = _stamp_first_seen(previous["facts"], facts, now)
    changes = diff_facts(previous["facts"], facts, now)
    if previous.get("status") and previous.get("status") != current["status"]:
        changes = [_status_change(previous["status"], current["status"], now), *changes]
    explanation = current["explanation"]
    if changes:
        explanation = {**explanation, "changes_since_last_check": change_notes(changes)}
    return {
        **current,
        "fields": {f["field"]: f["value"] for f in facts},
        "facts": facts,
        "explanation": explanation,
        "first_checked_at": previous.get("first_checked_at", previous.get("checked_at")),
        "previous_checked_at": previous.get("checked_at"),
        "last_changed_at": now if changes else previous.get("last_changed_at", previous.get("checked_at")),
        "revision": previous.get("revision", 1) + (1 if changes else 0),
        "changes_this_run": changes,
        "change_history": [*changes, *previous.get("change_history", [])][:MAX_CHANGE_HISTORY],
    }


def _carry_forward(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep last supported values from sources that failed or were skipped this run."""
    missing = [m["module"] for m in current.get("modules", []) if m.get("state") in ("failed", "blocked")]
    classes = {_MODULE_CLASSES[m] for m in missing if m in _MODULE_CLASSES}
    fields = set().union(*(_MODULE_FIELDS.get(m, set()) for m in missing)) if missing else set()
    if not classes and not fields:
        return []
    present = {f["field"] for f in current["facts"]}
    return [{**f, "stale": True, "stale_reason": "source unavailable on last refresh; value from earlier check"}
            for f in previous["facts"]
            if f["field"] not in present
            and ((f.get("source") or {}).get("source_class") in classes or f["field"] in fields)]


def _stamp_first_seen(old_facts: list[dict[str, Any]], new_facts: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    old = {f["field"]: f for f in old_facts}
    stamped = []
    for fact in new_facts:
        before = old.get(fact["field"])
        same = before is not None and before.get("value") == fact.get("value")
        first_seen = (before.get("first_seen") or before.get("retrieved_at")) if same else now
        stamped.append({**fact, "first_seen": first_seen or now})
    return stamped


def diff_facts(old_facts: list[dict[str, Any]], new_facts: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    old = {f["field"]: f for f in old_facts}
    new = {f["field"]: f for f in new_facts}
    changes: list[dict[str, Any]] = []
    for name, fact in new.items():
        before = old.get(name)
        if before is None:
            changes.append(_change(fact, "added", None, fact, now))
        elif before.get("value") != fact.get("value"):
            changes.append(_change(fact, "changed", before, fact, now))
    for name, fact in old.items():
        if name not in new:
            changes.append(_change(fact, "removed", fact, None, now))
    return changes


def _evidence_ref(fact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not fact:
        return None
    src = fact.get("source") or {}
    return {"source_url": src.get("url"), "content_sha256": src.get("content_sha256"),
            "retrieved_at": fact.get("retrieved_at"), "as_of": fact.get("as_of")}


def _change(fact: dict[str, Any], kind: str, before: dict[str, Any] | None, after: dict[str, Any] | None,
            now: str) -> dict[str, Any]:
    name = fact["field"]
    change_type = _CHANGE_TYPES.get(name, "field_changed")
    if name in ("roles", "board_members") and kind == "added":
        change_type = "new_role"
    return {
        "field": name, "label": fact.get("label"), "change": kind, "change_type": change_type,
        "material": name in MATERIAL_FIELDS,
        "old": before.get("value") if before else None, "new": after.get("value") if after else None,
        "detected_at": now, "previous_evidence": _evidence_ref(before), "current_evidence": _evidence_ref(after),
        "source_url": (fact.get("source") or {}).get("url"),
    }


def _status_change(old: str, new: str, now: str) -> dict[str, Any]:
    return {"field": "status", "label": "Status", "change": "changed", "change_type": "status_change",
            "material": True, "old": old, "new": new, "detected_at": now,
            "previous_evidence": None, "current_evidence": None}
