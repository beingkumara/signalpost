from datetime import datetime, timezone

from conftest import FakeClient
from signalpost import config, updater


def test_plan_refresh_puts_changed_first_then_oldest_stale():
    stored = {"111111111": "2026-09-28T00:00:00Z", "222222222": "2026-01-01T00:00:00Z",
              "333333333": "2026-02-01T00:00:00Z", "444444444": "2026-09-28T00:00:00Z"}
    plan = updater.plan_refresh(stored, {"444444444", "999999999"}, 30, datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert plan == ["444444444", "222222222", "333333333"]


def test_changed_orgnrs_reads_both_feeds_and_pages():
    since = "2026-09-28T00:00:00.000Z"
    first = f"{config.ENHET_UPDATES_URL}?dato={since}&size={updater.FEED_PAGE_SIZE}"
    roles = f"{config.ROLLE_UPDATES_URL}?afterTime={since}&size={updater.FEED_PAGE_SIZE}"
    entity_items = {"_embedded": {"oppdaterteEnheter": [{"organisasjonsnummer": "111111111", "oppdateringsid": 5}]}}
    role_items = [{"id": "7", "data": {"organisasjonsnummer": "222222222"}}]
    client = FakeClient({first: (200, entity_items), roles: (200, role_items)})
    changed, stats = updater.changed_orgnrs(client, since)
    assert changed == {"111111111", "222222222"}
    assert stats["feed_requests"] == 2


def test_state_roundtrip(tmp_path):
    assert updater.read_state(tmp_path) == {}
    updater.write_state(tmp_path, {"last_sync": "2026-09-29T00:00:00Z"})
    assert updater.read_state(tmp_path)["last_sync"] == "2026-09-29T00:00:00Z"


def test_feed_timestamp_format():
    assert updater.feed_timestamp("2026-09-28T10:00:00Z") == "2026-09-28T10:00:00.000Z"
