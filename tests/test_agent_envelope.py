import json
import time

from conftest import EQUINOR, FakeClient, load_fixture
from signalpost import config
from signalpost.agent import RunContext, research
from signalpost.envelope import build_envelope
from signalpost.modules import STATES
from signalpost.store import merge


def ctx(client, *, minutes=5, jobs=False, discovery=False, companies=1):
    return RunContext(client, time.monotonic() + minutes * 60, companies, 1, discovery, jobs)


def envelope_for(profile):
    return build_envelope(merge(None, profile), {"run_id": "t", "started_at": "a", "completed_at": "b"})


def test_full_profile_envelope_contract(equinor_routes):
    profile = research(ctx(FakeClient(equinor_routes)), EQUINOR)
    env = envelope_for(profile)
    assert env["organisation_number"] == EQUINOR and env["state"] == "available"
    assert set(env["sections"]) == {"legal_identity_and_brand", "annual_accounts", "leadership_and_workplaces",
                                    "website_and_profiles", "hiring_and_activity"}
    evidence_ids = {e["id"] for e in env["evidence"]}
    for claim in env["claims"]:
        assert claim["availability"] in STATES
        if claim["availability"] == "available":
            assert claim["evidence_ids"] and set(claim["evidence_ids"]) <= evidence_ids
        else:
            assert claim["value"] is None and claim["reason"]
    revenue = next(c for c in env["claims"] if c["field"] == "revenue")
    assert revenue["reporting_period"] == {"from": "2025-01-01", "to": "2025-12-31"}
    assert all(e["content_sha256"] and e["retrieved_at"] for e in env["evidence"])
    assert env["operations"]["third_party_cost_usd"] == 0.0
    json.dumps(env)


def test_missing_fields_get_explicit_states(equinor_routes):
    env = envelope_for(research(ctx(FakeClient(equinor_routes)), EQUINOR))
    website = next(c for c in env["claims"] if c["field"] == "official_website")
    assert website["availability"] in ("failed", "not_available", "blocked", "ambiguous")
    jobs = next(c for c in env["claims"] if c["field"] == "job_postings")
    assert jobs["availability"] == "not_available"  # jobs are attached by the runner lane


def test_synthesis_cites_claims(equinor_routes):
    env = envelope_for(research(ctx(FakeClient(equinor_routes)), EQUINOR))
    topics = {s["topic"]: s for s in env["summary"]["statements"]}
    assert "leadership" in topics and "financials" in topics and "unknowns" in topics
    ids = {c["id"] for c in env["claims"]}
    assert set(topics["financials"]["claim_ids"]) <= ids


def test_time_budget_skips_optional_sources_but_keeps_registry(equinor_routes):
    profile = research(ctx(FakeClient(equinor_routes), minutes=-1), EQUINOR)
    states = {m["module"]: m["state"] for m in profile["modules"]}
    assert states["registry"] == "available"
    assert states["annual_accounts"] == "blocked" and states["roles"] == "blocked"


def test_request_reserve_protects_core_lookups(equinor_routes):
    client = FakeClient(equinor_routes, budget=3)
    profile = research(ctx(client, companies=1), EQUINOR)
    states = {m["module"]: m["state"] for m in profile["modules"]}
    assert states["annual_accounts"] == "available" and states["roles"] == "available"
    assert states["workplaces"] == "blocked" and states["website"] == "blocked"


def test_invalid_and_unknown_numbers():
    invalid = envelope_for(research(ctx(FakeClient({})), "123456789"))
    assert invalid["state"] == "failed" and invalid["company_status"] == "invalid_input"
    unknown = envelope_for(research(ctx(FakeClient({})), "100000016"))
    assert unknown["state"] == "not_available"
    assert all(c["availability"] == "not_available" for c in unknown["claims"])


def test_subunit_and_deleted_are_not_applicable():
    routes = {config.UNDERENHET_URL.format(orgnr="973861883"): (200, load_fixture("underenhet_973861883.json")),
              config.ENHET_URL.format(orgnr=EQUINOR): (200, load_fixture("enhet_923609016.json"))}
    sub = research(ctx(FakeClient(routes)), "973861883")
    assert sub["status"] == "subunit" and sub["fields"]["parent_name"] == "EQUINOR ASA"
    assert {m["module"]: m["state"] for m in sub["modules"]}["roles"] == "not_applicable"
    deleted = research(ctx(FakeClient({config.ENHET_URL.format(orgnr="929173856"):
                                      (200, load_fixture("enhet_deleted_929173856.json"))})), "929173856")
    assert deleted["status"] == "deleted"


def test_bankrupt_status_and_registry_events():
    routes = {config.ENHET_URL.format(orgnr="935567645"): (200, load_fixture("enhet_bankrupt_935567645.json"))}
    profile = research(ctx(FakeClient(routes)), "935567645")
    assert profile["status"] == "bankrupt"
    events = profile["fields"]["registry_events"]
    assert events[0]["type"] == "bankruptcy" and events[0]["date"] == "2026-07-08"


