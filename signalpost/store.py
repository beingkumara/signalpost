"""Profile persistence and change tracking between refreshes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from signalpost import config
from signalpost.explain import change_notes

MAX_CHANGE_HISTORY = 200

# Which facts each secondary source produces, so a failed source keeps its old facts.
_SOURCE_BY_LABEL = {
    "Enhetsregisteret roller": lambda fact: fact["source"]["name"] == config.SOURCE_ROLLER,
    "Regnskapsregisteret": lambda fact: fact["source"]["name"] == config.SOURCE_REGNSKAP,
    "Company website": lambda fact: fact["source"]["name"] == config.SOURCE_WEBSITE,
    "Enhetsregisteret underenheter": lambda fact: fact["field"] in ("subunit_count", "subunits"),
}
_FAILED_STATUSES = {"lookup_failed"}


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
        json.dump(profile, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def merge(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Return a new profile combining this refresh with history from the previous one."""
    now = current["checked_at"]
    if previous is None:
        facts = [{**f, "first_seen": now} for f in current["facts"]]
        return {**current, "facts": facts, "first_checked_at": now, "last_changed_at": now,
                "revision": 1, "changes_this_run": [], "change_history": []}

    if current["status"] in _FAILED_STATUSES:
        # Keep the last good profile rather than overwrite it with nothing.
        return {**previous, "last_attempt_at": now, "last_attempt_status": current["status"],
                "warnings": current["warnings"]}

    carried = _carry_forward_failed(previous, current)
    facts = [*current["facts"], *carried]
    facts = _stamp_first_seen(previous["facts"], facts, now)
    changes = diff_facts(previous["facts"], facts, now)
    if previous.get("status") != current["status"]:
        changes = [{"field": "status", "label": "Status", "change": "changed", "old": previous.get("status"),
                    "new": current["status"], "detected_at": now}, *changes]

    fields = {f["field"]: f["value"] for f in facts}
    history = [*changes, *previous.get("change_history", [])][:MAX_CHANGE_HISTORY]
    explanation = current["explanation"]
    if changes:
        explanation = {**explanation, "changes_since_last_check": change_notes(changes)}
    return {
        **current,
        "fields": fields,
        "facts": facts,
        "explanation": explanation,
        "first_checked_at": previous.get("first_checked_at", previous.get("checked_at")),
        "previous_checked_at": previous.get("checked_at"),
        "last_changed_at": now if changes else previous.get("last_changed_at", previous.get("checked_at")),
        "revision": previous.get("revision", 1) + (1 if changes else 0),
        "changes_this_run": changes,
        "change_history": history,
    }


def _carry_forward_failed(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    failed = [row["source"] for row in current.get("sources_checked", []) if row.get("error")]
    predicates = [_SOURCE_BY_LABEL[label] for label in failed if label in _SOURCE_BY_LABEL]
    if not predicates:
        return []
    present = {f["field"] for f in current["facts"]}
    return [{**f, "stale": True, "stale_reason": "source unavailable on last refresh; value from earlier check"}
            for f in previous["facts"]
            if f["field"] not in present and any(p(f) for p in predicates)]


def _stamp_first_seen(old_facts: list[dict[str, Any]], new_facts: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    old = {f["field"]: f for f in old_facts}
    stamped = []
    for fact in new_facts:
        before = old.get(fact["field"])
        same = before is not None and before.get("value") == fact.get("value")
        first_seen = (before.get("first_seen") or before.get("retrieved_at")) if same else now
        stamped.append({**fact, "first_seen": first_seen})
    return stamped


def diff_facts(old_facts: list[dict[str, Any]], new_facts: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    old = {f["field"]: f for f in old_facts}
    new = {f["field"]: f for f in new_facts}
    changes: list[dict[str, Any]] = []
    for name, fact in new.items():
        before = old.get(name)
        if before is None:
            changes.append(_change(fact, "added", None, fact["value"], now))
        elif before.get("value") != fact.get("value"):
            changes.append(_change(fact, "changed", before["value"], fact["value"], now))
    for name, fact in old.items():
        if name not in new:
            changes.append(_change(fact, "removed", fact["value"], None, now))
    return changes


def _change(fact: dict[str, Any], kind: str, old: Any, new: Any, now: str) -> dict[str, Any]:
    return {"field": fact["field"], "label": fact.get("label"), "change": kind, "old": old, "new": new,
            "detected_at": now, "source_url": fact["source"]["url"], "source_as_of": fact.get("as_of")}


def stored_orgnrs(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if p.stem.isdigit() and len(p.stem) == 9)
