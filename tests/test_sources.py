import copy

import pytest

from conftest import EQUINOR, FakeClient, load_fixture
from signalpost import config
from signalpost.facts import Source
from signalpost.sources.enhetsregisteret import extract_facts, fetch_entity, fetch_parent_name
from signalpost.sources.regnskap import extract_account_facts
from signalpost.sources.roller import extract_role_facts
from signalpost.sources.underenheter import extract_subunit_facts

SRC = Source("test", "https://example.test")
AT = "2026-09-29T10:00:00Z"


def by_field(facts):
    return {f.field: f for f in facts}


def test_entity_facts_carry_source_evidence_and_dates():
    facts = by_field(extract_facts(load_fixture("enhet_923609016.json"), SRC, AT, "enhet"))
    assert facts["name"].value == "EQUINOR ASA"
    assert facts["organization_form"].value["code"] == "ASA"
    assert facts["organization_form"].value["description_en"] == "public limited company"
    assert facts["founded_date"].as_of == "1972-09-18"
    assert facts["share_capital"].value["currency"] == "NOK"
    assert facts["website"].value == "https://www.equinor.com"
    assert facts["employees"].value > 0
    assert all(f.evidence["matched_orgnr"] == EQUINOR for f in facts.values())
    assert facts["bankrupt"].value is False


def test_entity_status_facts_for_bankrupt_company():
    raw = load_fixture("enhet_bankrupt_935567645.json")
    facts = by_field(extract_facts(raw, SRC, AT, "enhet"))
    assert facts["bankrupt"].value is True
    assert facts["bankrupt"].as_of == raw["konkursdato"]


def test_deleted_entity_is_detected():
    client = FakeClient({config.ENHET_URL.format(orgnr="929173856"): (200, load_fixture("enhet_deleted_929173856.json"))})
    result = fetch_entity(client, "929173856")
    assert result.kind == "deleted"
    assert by_field(result.facts)["deleted_date"].value == "2026-09-20"


def test_subunit_fallback_when_main_unit_missing():
    client = FakeClient({config.UNDERENHET_URL.format(orgnr="973861883"): (200, load_fixture("underenhet_973861883.json"))})
    result = fetch_entity(client, "973861883")
    assert result.kind == "underenhet"
    assert by_field(result.facts)["parent_orgnr"].value == EQUINOR
    assert len(client.calls) == 2


def test_not_found_never_falls_back_to_name_search():
    client = FakeClient({})
    result = fetch_entity(client, "100000016")
    assert result.kind == "not_found"
    assert all("navn=" not in url for url in client.calls)


def test_identity_guard_rejects_record_for_other_number():
    wrong = copy.deepcopy(load_fixture("enhet_923609016.json"))
    client = FakeClient({config.ENHET_URL.format(orgnr="100000016"): (200, wrong)})
    with pytest.raises(ValueError, match="expected 100000016"):
        fetch_entity(client, "100000016")


def test_server_error_is_raised_not_hidden():
    client = FakeClient({config.ENHET_URL.format(orgnr=EQUINOR): (503, b"")})
    with pytest.raises(ConnectionError):
        fetch_entity(client, EQUINOR)


def test_parent_name_lookup():
    client = FakeClient({config.ENHET_URL.format(orgnr=EQUINOR): (200, load_fixture("enhet_923609016.json"))})
    facts = fetch_parent_name(client, EQUINOR, "973861883")
    assert facts[0].value == "EQUINOR ASA"
    assert fetch_parent_name(FakeClient({}), EQUINOR, "973861883") == ()


def test_roles_skip_deregistered_and_omit_birth_dates():
    data = load_fixture("roller_923609016.json")
    facts = by_field(extract_role_facts(data, SRC, AT, EQUINOR))
    assert facts["ceo"].value["name"] == "Anders Opedal"
    assert facts["auditor"].value["type"] == "organization"
    assert facts["auditor"].value["orgnr"] == "976389387"
    deregistered = {" ".join(filter(None, (r["person"]["navn"].get("fornavn"), r["person"]["navn"].get("etternavn"))))
                    for g in data["rollegrupper"] for r in g["roller"] if r.get("avregistrert") and r.get("person")}
    active_names = {r["holder"]["name"] for r in facts["roles"].value}
    assert not deregistered & active_names
    assert "fodselsdato" not in str(facts["roles"].value)
    assert facts["board_members"].value


def test_roles_empty_payload_gives_no_facts():
    assert extract_role_facts({"rollegrupper": []}, SRC, AT, EQUINOR) == ()


def test_accounts_are_copied_verbatim_with_currency_and_period():
    facts = by_field(extract_account_facts(load_fixture("regnskap_923609016.json"), SRC, AT, EQUINOR))
    assert facts["revenue"].value == {"amount": 67956000000.0, "currency": "USD", "year": "2025"}
    assert facts["revenue"].as_of == "2025-12-31"
    assert facts["accounts_period"].value["audited"] is True
    assert facts["net_profit"].evidence["journal_number"] == "2026635024"


def test_accounts_for_another_company_are_dropped():
    assert extract_account_facts(load_fixture("regnskap_923609016.json"), SRC, AT, "100000016") == ()


def test_group_accounts_get_prefixed_fields():
    data = load_fixture("regnskap_923609016.json")
    group = copy.deepcopy(data[0])
    group["regnskapstype"] = "KONSERN"
    facts = by_field(extract_account_facts([data[0], group], SRC, AT, EQUINOR))
    assert "group_revenue" in facts and "revenue" in facts


def test_subunits_count_and_listing():
    facts = by_field(extract_subunit_facts(load_fixture("underenheter_923609016.json"), SRC, AT, EQUINOR))
    assert facts["subunit_count"].value >= len(facts["subunits"].value)
    assert all(u["orgnr"] for u in facts["subunits"].value)


def test_subunits_of_another_parent_are_ignored():
    assert extract_subunit_facts(load_fixture("underenheter_923609016.json"), SRC, AT, "100000016") == ()
