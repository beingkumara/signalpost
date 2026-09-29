"""Find which stored profiles need a refresh, using Brønnøysund's change feeds."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from signalpost import config
from signalpost.http_client import HttpClient

FEED_PAGE_SIZE = 10_000
MAX_FEED_PAGES = 40
STATE_FILE = "_sync_state.json"


def changed_orgnrs(client: HttpClient, since_iso: str) -> tuple[set[str], dict[str, Any]]:
    """Org numbers with entity or role changes since the given UTC timestamp."""
    entity_changes, entity_pages = _entity_feed(client, since_iso)
    role_changes, role_pages = _role_feed(client, since_iso)
    stats = {"since": since_iso, "entity_changes": len(entity_changes), "role_changes": len(role_changes),
             "feed_requests": entity_pages + role_pages}
    return entity_changes | role_changes, stats


def _entity_feed(client: HttpClient, since_iso: str) -> tuple[set[str], int]:
    found: set[str] = set()
    url = f"{config.ENHET_UPDATES_URL}?dato={since_iso}&size={FEED_PAGE_SIZE}"
    for page in range(1, MAX_FEED_PAGES + 1):
        resp = client.get(url)
        if resp.status != 200:
            raise ConnectionError(f"entity update feed returned HTTP {resp.status}")
        items = (resp.json().get("_embedded") or {}).get("oppdaterteEnheter") or []
        found.update(i["organisasjonsnummer"] for i in items if i.get("organisasjonsnummer"))
        if len(items) < FEED_PAGE_SIZE:
            return found, page
        next_id = max(i["oppdateringsid"] for i in items) + 1
        url = f"{config.ENHET_UPDATES_URL}?oppdateringsid={next_id}&size={FEED_PAGE_SIZE}"
    return found, MAX_FEED_PAGES


def _role_feed(client: HttpClient, since_iso: str) -> tuple[set[str], int]:
    found: set[str] = set()
    url = f"{config.ROLLE_UPDATES_URL}?afterTime={since_iso}&size={FEED_PAGE_SIZE}"
    for page in range(1, MAX_FEED_PAGES + 1):
        resp = client.get(url)
        if resp.status != 200:
            raise ConnectionError(f"role update feed returned HTTP {resp.status}")
        events = resp.json() or []
        found.update((e.get("data") or {}).get("organisasjonsnummer") for e in events)
        if len(events) < FEED_PAGE_SIZE:
            found.discard(None)
            return found, page
        url = f"{config.ROLLE_UPDATES_URL}?afterId={max(int(e['id']) for e in events)}&size={FEED_PAGE_SIZE}"
    found.discard(None)
    return found, MAX_FEED_PAGES


def plan_refresh(stored: dict[str, str], changed: set[str], stale_days: int, now: datetime) -> list[str]:
    """Changed profiles first, then those not checked for `stale_days`, oldest first.

    `stored` maps orgnr to its last checked_at timestamp.
    """
    cutoff = now - timedelta(days=stale_days)
    changed_first = sorted(o for o in stored if o in changed)
    stale = sorted((ts, o) for o, ts in stored.items() if o not in changed and _parse(ts) < cutoff)
    return changed_first + [o for _, o in stale]


def _parse(ts: str) -> datetime:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def read_state(directory: Path) -> dict[str, Any]:
    path = directory / STATE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(directory: Path, state: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


def feed_timestamp(ts: str) -> str:
    """Convert '2026-09-28T10:00:00Z' to the feed's millisecond format."""
    return ts.replace("Z", ".000Z") if ts.endswith("Z") and "." not in ts else ts
