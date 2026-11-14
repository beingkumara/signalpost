"""Decision-useful statements: what the company does, what changed, what is unknown, each citing claims."""

from signalpost.explain import change_notes
from signalpost.synthesis import synthesis


def claim(i, field, value, label=None):
    return {"id": f"c{i}", "field": field, "label": label or field, "value": value, "availability": "available"}


def missing(i, field, label):
    return {"id": f"c{i}", "field": field, "label": label, "value": None, "availability": "not_available"}


def money(amount):
    return {"amount": amount, "currency": "NOK"}


def topics(claims, changes=()):
    return {s["topic"]: s for s in synthesis({"changes_this_run": list(changes)}, claims)}


INDUSTRY = [{"code": "71.110", "description_en": "Architectural activities"}]


def test_zero_net_result_is_break_even_not_profit():
    t = topics([claim(1, "accounts_period", {"year": 2025}), claim(2, "revenue", money(4_180_000)),
                claim(3, "operating_profit", money(-127_160)), claim(4, "net_profit", money(0))])
    text = t["financials"]["text"]
    assert "broke even" in text and "profit." not in text and "operating result NOK -127.16 k" in text


def test_junk_website_description_is_not_what_the_company_does():
    t = topics([claim(1, "industry_codes", INDUSTRY), claim(2, "website_description", "Powered by SmugMug"),
                claim(3, "official_website", {"final_url": "https://arkjv.no/"})])
    text = t["what_it_does"]["text"]
    assert "Architectural activities" in text and "SmugMug" not in text


def test_real_website_description_adds_to_the_registry_industry():
    desc = "Vi tegner boliger, hytter og næringsbygg i Sogn og Fjordane siden 2003."
    t = topics([claim(1, "industry_codes", INDUSTRY), claim(2, "website_description", desc)])
    assert "Architectural activities" in t["what_it_does"]["text"] and desc in t["what_it_does"]["text"]
    assert t["what_it_does"]["claim_ids"] == ["c1", "c2"]


def test_holding_company_only_when_the_purpose_says_so():
    purpose = "Holdingsselskap og drive med kjøp og salg av aksjer."
    t = topics([claim(1, "industry_codes", INDUSTRY), claim(2, "purpose", purpose)])
    assert "holding company" in t["what_it_does"]["text"]
    t = topics([claim(1, "industry_codes", INDUSTRY), claim(2, "purpose", "Arkitekttjenester.")])
    assert "holding company" not in t["what_it_does"]["text"]


def test_official_changes_name_what_changed():
    events = [{"date": "2026-10-31", "type": "roles_updated", "fields": ["roles"], "material": True, "details": []},
              {"date": "2026-09-15", "type": "updated", "fields": ["main industry"], "material": True,
               "details": [{"field": "main industry", "op": "replace", "value": "96.220"}]},
              {"date": "2026-11-12", "type": "updated", "fields": ["employees"], "material": False, "details": []}]
    reg = [{"date": "2026-10-31", "type": "roles_changed", "description": "Regnskapsfører last changed"}]
    t = topics([claim(1, "official_changes", events), claim(2, "registry_events", reg)])
    text = t["official_changes"]["text"]
    assert "2026-10-31: accountant changed" in text and "2026-09-15: main industry → 96.220" in text
    assert "employees" not in text.split(";")[0]  # non-material refreshes are counted, not listed first


def test_unknowns_explain_the_missing_website_first():
    t = topics([claim(1, "name", "X AS"), missing(2, "official_website", "Verified official website"),
                missing(3, "website_emails", "Contact emails on website"), missing(4, "auditor", "Auditor")])
    text = t["unknowns"]["text"]
    assert text.startswith("No company website could be verified") and "Auditor" in text
    assert "Contact emails on website" not in text


def test_change_notes_are_readable_for_lists():
    subunits_old = [{"name": "REMA 1000 SEM", "employees": 18}]
    subunits_new = [{"name": "REMA 1000 SEM", "employees": 16}, {"name": "REMA 1000 BYGDØY", "employees": 3}]
    notes = change_notes([{"field": "subunits", "label": "Registered workplaces", "change": "changed",
                           "old": subunits_old, "new": subunits_new},
                          {"field": "official_changes", "label": "Changes recorded by Brønnøysund",
                           "change": "changed", "old": None,
                           "new": [{"date": "2026-11-13", "fields": ["business address"]}]}])
    assert "{" not in " ".join(notes) and "'" not in " ".join(notes)
    assert "REMA 1000 BYGDØY" in notes[0] and "2026-11-13 business address" in notes[1]
