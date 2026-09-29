import copy

from signalpost import config, store


def fact(field, value, source=config.SOURCE_ENHETSREGISTERET):
    return {"field": field, "value": value, "label": field, "source": {"name": source, "url": "https://x"},
            "retrieved_at": "2026-09-01T00:00:00Z", "as_of": "2026-09-01"}


def fact_cls(field, value, source_class):
    return {**fact(field, value), "source": {"name": "x", "url": "https://x", "source_class": source_class}}


def profile(facts, checked_at="2026-09-01T00:00:00Z", status="active", modules=None):
    return {"orgnr": "923609016", "status": status, "checked_at": checked_at, "facts": facts,
            "fields": {f["field"]: f["value"] for f in facts}, "warnings": [],
            "modules": modules or [], "explanation": {"summary": "s", "notes": []}}


def test_first_save_starts_history():
    merged = store.merge(None, profile([fact("name", "A AS")]))
    assert merged["revision"] == 1
    assert merged["facts"][0]["first_seen"] == "2026-09-01T00:00:00Z"
    assert merged["change_history"] == []


def test_unchanged_refresh_keeps_first_seen_and_revision():
    first = store.merge(None, profile([fact("name", "A AS")]))
    second = store.merge(first, profile([fact("name", "A AS")], checked_at="2026-09-10T00:00:00Z"))
    assert second["changes_this_run"] == []
    assert second["revision"] == 1
    assert second["facts"][0]["first_seen"] == "2026-09-01T00:00:00Z"
    assert second["previous_checked_at"] == "2026-09-01T00:00:00Z"


def test_changed_added_and_removed_values_are_logged():
    first = store.merge(None, profile([fact("name", "A AS"), fact("phone", "1")]))
    second = store.merge(first, profile([fact("name", "B AS"), fact("email", "e@x.no")],
                                        checked_at="2026-09-10T00:00:00Z"))
    kinds = {(c["field"], c["change"]) for c in second["changes_this_run"]}
    assert kinds == {("name", "changed"), ("email", "added"), ("phone", "removed")}
    assert second["revision"] == 2
    assert second["fields"]["name"] == "B AS"
    assert "changes_since_last_check" in second["explanation"]
    name_fact = next(f for f in second["facts"] if f["field"] == "name")
    assert name_fact["first_seen"] == "2026-09-10T00:00:00Z"


def test_status_change_is_logged():
    first = store.merge(None, profile([fact("name", "A AS")]))
    second = store.merge(first, profile([fact("name", "A AS")], checked_at="2026-09-10T00:00:00Z", status="bankrupt"))
    assert second["changes_this_run"][0] == {**second["changes_this_run"][0], "field": "status", "new": "bankrupt"}


def test_failed_source_keeps_previous_facts_marked_stale():
    first = store.merge(None, profile([fact("name", "A AS"), fact_cls("revenue", 5, "official_annual_accounts")]))
    failed = [{"module": "annual_accounts", "state": "failed"}]
    second = store.merge(first, profile([fact("name", "A AS")], checked_at="2026-09-10T00:00:00Z", modules=failed))
    revenue = next(f for f in second["facts"] if f["field"] == "revenue")
    assert revenue["stale"] is True
    assert second["changes_this_run"] == []


def test_lookup_failure_does_not_wipe_profile():
    first = store.merge(None, profile([fact("name", "A AS")]))
    broken = profile([], checked_at="2026-09-10T00:00:00Z", status="lookup_failed")
    second = store.merge(first, broken)
    assert second["fields"]["name"] == "A AS"
    assert second["last_attempt_status"] == "lookup_failed"


def test_save_and_load_roundtrip(tmp_path):
    merged = store.merge(None, profile([fact("name", "Ø AS")]))
    store.save(tmp_path, merged)
    assert store.load(tmp_path, "923609016")["fields"]["name"] == "Ø AS"
    assert store.stored_orgnrs(tmp_path) == ["923609016"]
    assert store.load(tmp_path, "100000016") is None


def test_merge_does_not_mutate_inputs():
    first = store.merge(None, profile([fact("name", "A AS")]))
    snapshot = copy.deepcopy(first)
    store.merge(first, profile([fact("name", "B AS")], checked_at="2026-09-10T00:00:00Z"))
    assert first == snapshot


def test_blocked_workplaces_keep_subunits():
    first = store.merge(None, profile([fact("name", "A AS"), fact("subunit_count", 3)]))
    second = store.merge(first, profile([fact("name", "A AS")], checked_at="2026-09-10T00:00:00Z",
                                        modules=[{"module": "workplaces", "state": "blocked"}]))
    assert any(f["field"] == "subunit_count" and f["stale"] for f in second["facts"])


def test_changes_are_typed_and_keep_both_evidence_sides():
    first = store.merge(None, profile([fact("ceo", {"name": "Old"})]))
    second = store.merge(first, profile([fact("ceo", {"name": "New"})], checked_at="2026-09-10T00:00:00Z"))
    change = second["changes_this_run"][0]
    assert change["change_type"] == "role_change" and change["material"] is True
    assert change["previous_evidence"]["source_url"] == "https://x"
    assert change["current_evidence"]["source_url"] == "https://x"


def test_from_envelope_roundtrip_is_idempotent():
    from signalpost.envelope import build_envelope

    first = store.merge(None, {**profile([fact("name", "A AS")]), "explanation": {"summary": "", "notes": []}})
    env = build_envelope(first, {"run_id": "r"})
    previous = store.from_envelope(env)
    again = store.merge(previous, profile([fact("name", "A AS")], checked_at="2026-09-10T00:00:00Z"))
    assert again["changes_this_run"] == []
