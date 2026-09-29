import json

from conftest import EQUINOR, FakeClient, load_fixture
from signalpost import config
from signalpost.explain import explain, money
from signalpost.profile_builder import build_profile


def test_full_profile_has_sources_for_every_fact(equinor_routes):
    profile = build_profile(FakeClient(equinor_routes), EQUINOR, with_website=False)
    assert profile["status"] == "active"
    assert profile["name"] == "EQUINOR ASA"
    assert profile["fields"]["revenue"]["currency"] == "USD"
    assert all(f["source"]["url"].startswith("https://") for f in profile["facts"])
    assert all(f["evidence"]["matched_orgnr"] == EQUINOR for f in profile["facts"])
    assert "Anders Opedal" in profile["explanation"]["summary"]
    assert "public limited company" in profile["explanation"]["summary"]
    json.dumps(profile)  # serialisable


def test_request_count_stays_small(equinor_routes):
    client = FakeClient(equinor_routes)
    build_profile(client, EQUINOR, with_website=False)
    assert client.used <= 5


def test_failed_secondary_source_becomes_warning(equinor_routes):
    client = FakeClient(equinor_routes, fail={config.REGNSKAP_URL.format(orgnr=EQUINOR)})
    profile = build_profile(client, EQUINOR, with_website=False)
    assert profile["status"] == "active"
    assert "revenue" not in profile["fields"]
    assert any("Regnskapsregisteret unavailable" in w for w in profile["warnings"])
    assert any(row.get("error") for row in profile["sources_checked"])


def test_website_is_fetched_when_enabled(equinor_routes):
    routes = {**equinor_routes, "https://www.equinor.com": (200, "<title>Equinor</title><p>Org nr 923609016</p>")}
    client = FakeClient(routes, content_types={"https://www.equinor.com": "text/html"})
    profile = build_profile(client, EQUINOR)
    assert profile["fields"]["website_verification"] == "orgnr_on_page"
    assert "verified" in profile["explanation"]["summary"]


def test_invalid_input_makes_no_requests():
    client = FakeClient({})
    profile = build_profile(client, "123456789")
    assert profile["status"] == "invalid_input" and client.used == 0
    assert profile["facts"] == []


def test_unknown_number_publishes_nothing():
    profile = build_profile(FakeClient({}), "100000016")
    assert profile["status"] == "not_found"
    assert profile["facts"] == []
    assert "avoid attributing" in profile["explanation"]["summary"]


def test_budget_exhaustion_is_reported_not_crashing(equinor_routes):
    profile = build_profile(FakeClient(equinor_routes, budget=0), EQUINOR)
    assert profile["status"] == "lookup_failed"


def test_subunit_profile_names_parent():
    routes = {
        config.UNDERENHET_URL.format(orgnr="973861883"): (200, load_fixture("underenhet_973861883.json")),
        config.ENHET_URL.format(orgnr=EQUINOR): (200, load_fixture("enhet_923609016.json")),
    }
    profile = build_profile(FakeClient(routes), "973861883")
    assert profile["status"] == "subunit"
    assert profile["fields"]["parent_name"] == "EQUINOR ASA"
    assert any("EQUINOR ASA (923609016)" in n for n in profile["explanation"]["notes"])


def test_deleted_and_bankrupt_statuses():
    deleted = build_profile(FakeClient({config.ENHET_URL.format(orgnr="929173856"):
                                        (200, load_fixture("enhet_deleted_929173856.json"))}), "929173856")
    assert deleted["status"] == "deleted"
    bankrupt = build_profile(FakeClient({config.ENHET_URL.format(orgnr="935567645"):
                                         (200, load_fixture("enhet_bankrupt_935567645.json"))}), "935567645")
    assert bankrupt["status"] == "bankrupt"
    assert any("Bankruptcy" in n for n in bankrupt["explanation"]["notes"])


def test_enk_without_accounts_gets_explanatory_note():
    profile = {"status": "active", "orgnr": "100000016", "name": "X",
               "fields": {"organization_form": {"code": "ENK", "description": "Enkeltpersonforetak"}}, "warnings": []}
    notes = explain(profile)["notes"]
    assert any("ENK" in n for n in notes)
    assert "a sole proprietorship" in explain(profile)["summary"]


def test_money_formatting():
    assert money(67956000000, "USD") == "USD 67.96 bn"
    assert money(-191680, "NOK") == "NOK -191.68 k"
    assert money(500, "NOK") == "NOK 500"
