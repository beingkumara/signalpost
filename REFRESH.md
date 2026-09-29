# Refresh behaviour

- **Previous snapshot.** A refresh reads the stored profile in `--profiles-dir`, or an earlier `envelopes.jsonl` passed with `--previous`.
- **Stable claim keys.** Claims are keyed by field. List values such as job postings and news are sorted deterministically, so re-running on the same source snapshot yields zero changes and no duplicate records. This is tested in `tests/test_cli.py::test_rerun_is_idempotent`.
- **Typed changes.** Every change records:
  - `change` (`added`, `changed` or `removed`) and `change_type`, one of `role_change`, `new_role`, `new_filing`, `jobs_change`, `location_change`, `address_change`, `changed_description`, `website_change`, `name_change`, `status_change`, `new_event`, `profile_change` or `field_changed`;
  - a `material` flag;
  - the old and new values;
  - `previous_evidence` and `current_evidence`, which give the source URL, content hash, retrieval time and as-of date on both sides.
- **History.** The last 200 changes are kept in the stored profile. `revision` increases only when something changed. `first_seen` shows how long a value has held.
- **Failed refreshes keep the last supported value.** A source that fails or is skipped keeps its previous claims, marked `stale`, and the failure is recorded. If the core lookup fails, the previous profile is kept whole.
- **Scheduled refresh.** `python3 -m signalpost update` reads Brønnøysund's entity and role change feeds since the last sync. It refreshes the stored companies that changed, then those older than `--stale-days`.
