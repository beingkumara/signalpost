import json
import time

import conftest
from conftest import EQUINOR, FakeClient, load_fixture
from signalpost import narrative
from signalpost.modules import ModuleResult
from signalpost.sources import discovery, search
from signalpost.agent import worth_searching

TAVILY = search.PROVIDERS["tavily"].url
EXA = search.PROVIDERS["exa"].url


def test_from_env_selects_keyed_providers_in_order():
    svc = search.from_env(environ={"EXA_API_KEY": "e", "TAVILY_API_KEY": "t", "SIGNALPOST_SEARCH_PROVIDERS": "exa,tavily"})
    assert [p.name for p, _ in svc.providers] == ["exa", "tavily"]
    assert not search.from_env(environ={}).enabled
    assert search.from_env(environ={"LINKUP_API_KEY": " "}).providers == []


def test_failover_cap_and_cost():
    client = FakeClient({TAVILY: (402, b""), EXA: (200, {"results": [{"url": "https://acme.no/about"}]})})
    svc = search.from_env(max_searches=1, environ={"TAVILY_API_KEY": "t", "EXA_API_KEY": "e"})
    urls, provider = svc.search(client, "Acme Bergen")
    assert urls == ["https://acme.no/about"] and provider == "exa"
    assert "tavily" in svc.disabled and svc.by_provider == {"exa": 1}
    assert svc.cost_usd == search.PROVIDERS["exa"].usd_per_search
    assert svc.search(client, "again") == ([], None)  # per-run cap reached
    assert client.posts[1][2] == {"x-api-key": "e"}


def test_provider_payload_shapes():
    for name, key_field in (("tavily", "query"), ("exa", "query"), ("linkup", "q"), ("firecrawl", "query")):
        headers, payload = search.PROVIDERS[name].build("k", "Acme")
        assert payload[key_field] == "Acme" and headers
    assert search.PROVIDERS["firecrawl"].parse({"data": {"web": [{"url": "https://a.no"}]}}) == ["https://a.no"]


def test_candidate_sites_skip_directories_and_duplicates():
    urls = ["https://www.proff.no/selskap/acme", "https://acme.no/kontakt", "https://www.acme.no/", "ftp://x.no",
            "https://no.linkedin.com/company/acme", "https://acme-bygg.no/"]
    assert search.candidate_sites(urls) == ["https://acme.no/", "https://acme-bygg.no/"]


def test_discovery_uses_search_last_and_requires_orgnr():
    site = "https://fjordlys.no/"
    client = FakeClient({site: (200, "<title>Fjordlys</title><footer>Org.nr 923 609 016</footer>")},
                        content_types={site: "text/html"})
    calls = []

    def fake_search():
        calls.append(1)
        return ["https://www.proff.no/x", site], "exa"

    result = discovery.discover_website(client, "923609016", "FJORDLYS BYGG AS", search=fake_search)
    assert result.state == "available" and calls == [1]
    assert result.facts[0].value["origin"] == "search_candidate:exa"


def test_worth_searching():
    staff = {"organisasjonsform": {"kode": "AS"}, "harRegistrertAntallAnsatte": True, "antallAnsatte": 3}
    assert worth_searching(staff, ModuleResult("annual_accounts", "not_available"))
    coop = {**staff, "organisasjonsform": {"kode": "BRL"}}
    assert not worth_searching(coop, ModuleResult("annual_accounts", "not_available"))
    assert not worth_searching({"organisasjonsform": {"kode": "AS"}}, ModuleResult("annual_accounts", "not_available"))


CLAIMS = [
    {"id": "c1", "field": "name", "label": "Registered name", "value": "ACME AS", "availability": "available"},
    {"id": "c2", "field": "revenue", "label": "Revenue", "value": {"amount": 4180000.0, "currency": "NOK", "year": "2025"},
     "availability": "available"},
    {"id": "c3", "field": "ceo", "label": "CEO", "value": {"name": "Kari Nordmann"}, "availability": "available"},
]


def test_validate_keeps_supported_and_drops_invented_numbers():
    raw = json.dumps({"sentences": [
        {"text": "ACME AS had revenue of NOK 4.18 m in 2025.", "claim_ids": ["c1", "c2"]},
        {"text": "Revenue grew 12% year on year.", "claim_ids": ["c2"]},
        {"text": "Kari Nordmann is the CEO.", "claim_ids": ["c3", "c99"]},
        {"text": "It is a leading firm.", "claim_ids": []},
    ]})
    kept, dropped = narrative.validate("```json\n" + raw + "\n```", CLAIMS)
    assert [k["text"] for k in kept] == ["ACME AS had revenue of NOK 4.18 m in 2025.", "Kari Nordmann is the CEO."]
    assert kept[1]["claim_ids"] == ["c3"] and dropped == 2
    assert narrative.validate("not json", CLAIMS) == ([], 1)


def test_narrator_adds_validated_narrative_and_tracks_cost():
    reply = {"content": [{"type": "text", "text": json.dumps(
        {"sentences": [{"text": "ACME AS is led by Kari Nordmann.", "claim_ids": ["c1", "c3"]}]})}],
        "usage": {"input_tokens": 1000, "output_tokens": 100}}
    client = FakeClient({narrative.API_URL: (200, reply)})
    narr = narrative.Narrator.from_env(client, {"ANTHROPIC_API_KEY": "k"})
    env = {"name": "ACME AS", "claims": CLAIMS, "summary": {"summary": "s"}}
    out = narr.add_narrative(env, time.monotonic() + 60)
    assert out["summary"]["narrative"]["sentences"][0]["claim_ids"] == ["c1", "c3"]
    assert abs(narr.cost_usd - 0.0015) < 1e-9
    assert client.posts[0][2]["x-api-key"] == "k"
    assert narrative.Narrator.from_env(client, {}) is None


def test_narrator_respects_budget_and_errors():
    client = FakeClient({narrative.API_URL: (500, b"")})
    narr = narrative.Narrator(client, "k", max_usd=0.0)
    env = {"claims": CLAIMS, "summary": {}}
    assert narr.add_narrative(env, time.monotonic() + 60) is env  # budget already spent
    narr = narrative.Narrator(client, "k")
    assert narr.add_narrative(env, time.monotonic() + 60) is env and narr.errors == 1


def test_cli_run_with_search_key_finds_site(tmp_path, monkeypatch, equinor_routes):
    from signalpost import cli
    raw = load_fixture("enhet_923609016.json")
    raw = {k: v for k, v in raw.items() if k not in ("hjemmeside", "epostadresse")}
    site = "https://equinor-energy.no/"
    routes = {**equinor_routes, f"https://data.brreg.no/enhetsregisteret/api/enheter/{EQUINOR}": (200, raw),
              TAVILY: (200, {"results": [{"url": site}]}),
              site: (200, "<title>Equinor</title><p>Equinor ASA org.nr. 923 609 016</p>")}
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.setattr(cli, "HttpClient", lambda budget: FakeClient(routes, budget=budget,
                                                                     content_types={site: "text/html"}))
    cli.main(["--profiles-dir", str(tmp_path / "p"), "run", EQUINOR, "--out", str(tmp_path / "o"), "--no-history",
              "--no-jobs", "--quiet"])
    env = json.loads((tmp_path / "o" / "envelopes.jsonl").read_text().splitlines()[0])
    website = next(c for c in env["claims"] if c["field"] == "official_website")
    assert website["availability"] == "available" and website["value"]["origin"] == "search_candidate:tavily"
    report = json.loads((tmp_path / "o" / "run_report.json").read_text())
    assert report["search"]["searches"] == 1 and report["operations"]["third_party_cost_usd"] > 0


def test_number_check_is_exact_not_substring():
    big = [{"id": "c1", "field": "revenue", "label": "Revenue",
            "value": {"amount": 1250000.0, "currency": "NOK", "year": "2025"}, "availability": "available"}]
    ok = narrative._numbers_supported
    assert ok("Revenue was NOK 1.25 m in 2025.", big)
    assert ok("Revenue was NOK 1 250 000 in 2025.", big) and ok("Revenue was NOK 1,250,000.", big)
    assert not ok("Revenue was 125 thousand.", big)
    assert not ok("Revenue grew 12%.", big)
    assert not ok("Revenue was NOK 1.3 m.", big)
    assert not ok("It has two sites.", big)
    assert not ok("Revenue was 1.25 billion.", big)


def test_validate_survives_malformed_model_output():
    assert narrative.validate('{"sentences": 5}', CLAIMS) == ([], 1)
    kept, dropped = narrative.validate('Sure! {"sentences":[{"text":"ACME AS.","claim_ids":[["c1"]]}]} done', CLAIMS)
    assert kept == [] and dropped == 1
    kept, _ = narrative.validate('{"sentences":[{"text":"Visit www.acme.no today.","claim_ids":["c1"]}]}', CLAIMS)
    assert kept == []


def test_narrator_never_raises_on_bad_reply():
    client = FakeClient({narrative.API_URL: (200, {"content": ["not a dict"], "usage": {"input_tokens": None}})})
    narr = narrative.Narrator(client, "k")
    env = {"claims": CLAIMS, "summary": {}}
    assert narr.add_narrative(env, time.monotonic() + 60) is env


def test_search_disables_provider_on_bad_request_and_stops_when_none_left():
    client = FakeClient({TAVILY: (400, b"")})
    svc = search.from_env(environ={"TAVILY_API_KEY": "t"})
    assert svc.search(client, "a") == ([], None) and svc.search(client, "b") == ([], None)
    assert svc.searches == 1 and len(client.posts) == 1


def test_candidate_sites_ignore_ports_and_credentials():
    assert search.candidate_sites(["https://linkedin.com:443/company/x", "https://u:p@acme.no/", "https://acme.no:8443/x"]) \
        == ["https://acme.no/"]


def test_search_candidate_needs_tie_beyond_orgnr():
    from signalpost.sources.website import crawl_site
    spam = "https://company-lists.example/"
    client = FakeClient({spam: (200, "<title>All companies</title><li>FJORDLYS BYGG AS 923609016</li>")},
                        content_types={spam: "text/html"})
    result = crawl_site(client, spam, "923609016", "FJORDLYS BYGG AS", discovered=True, extra_pages=0,
                        origin="search_candidate:exa", postcode="5003")
    assert result.state == "not_available"
    tied = "https://fjordlys.no/"
    client = FakeClient({tied: (200, "<title>Fjordlys Bygg</title><p>org 923609016</p>")}, content_types={tied: "text/html"})
    ok = crawl_site(client, tied, "923609016", "FJORDLYS BYGG AS", discovered=True, extra_pages=0,
                    origin="search_candidate:exa")
    assert ok.state == "available" and ok.facts[0].confidence == "medium"


def test_http_client_post_does_not_follow_redirects():
    from signalpost.http_client import _API_OPENER, _NoRedirects
    assert any(isinstance(h, _NoRedirects) for h in _API_OPENER.handlers)
