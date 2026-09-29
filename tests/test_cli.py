import json

import pytest

from conftest import EQUINOR, FakeClient
from signalpost import cli, runner


@pytest.fixture
def fake_http(monkeypatch, equinor_routes):
    monkeypatch.setattr(cli, "HttpClient", lambda budget: FakeClient(equinor_routes, budget=budget))


def _run(tmp_path, *extra):
    return cli.main(["--profiles-dir", str(tmp_path / "p"), "run", "--out", str(tmp_path / "out"),
                     "--no-history", "--no-jobs", "--no-website-discovery", "--quiet", *extra])


def test_run_jsonl_batch_writes_one_envelope_per_input(tmp_path, fake_http):
    batch = tmp_path / "batch.jsonl"
    batch.write_text(json.dumps({"organisation_number": EQUINOR, "name": "x"}) + "\n"
                     + json.dumps({"organisation_number": "123456789"}) + "\n", encoding="utf-8")
    assert _run(tmp_path, "--input", str(batch), "--expected-count", "2") == 0
    lines = (tmp_path / "out" / "envelopes.jsonl").read_text().splitlines()
    assert [json.loads(l)["organisation_number"] for l in lines] == [EQUINOR, "123456789"]
    report = json.loads((tmp_path / "out" / "run_report.json").read_text())
    assert report["validation"]["passed"] and report["operations"]["third_party_cost_usd"] == 0.0
    html = (tmp_path / "out" / "report.html").read_text()
    assert "EQUINOR ASA" in html and "<\\/script>" not in html.split("const DATA=")[0]


def test_expected_count_mismatch_fails(tmp_path, fake_http):
    assert _run(tmp_path, EQUINOR, "--expected-count", "5") == 1


def test_rerun_is_idempotent(tmp_path, fake_http):
    _run(tmp_path, EQUINOR)
    _run(tmp_path, EQUINOR)
    env = json.loads((tmp_path / "out" / "envelopes.jsonl").read_text().splitlines()[0])
    assert env["changes"] == [] and env["refresh"]["revision"] == 1
    assert env["refresh"]["previous_checked_at"]


def test_previous_envelopes_file_drives_diff(tmp_path, fake_http):
    _run(tmp_path, EQUINOR)
    prev = tmp_path / "prev.jsonl"
    env = json.loads((tmp_path / "out" / "envelopes.jsonl").read_text().splitlines()[0])
    for claim in env["claims"]:
        if claim["field"] == "name":
            claim["value"] = "OLD NAME ASA"
    prev.write_text(json.dumps(env) + "\n", encoding="utf-8")
    cli.main(["--profiles-dir", str(tmp_path / "p2"), "run", EQUINOR, "--out", str(tmp_path / "o2"),
              "--previous", str(prev), "--no-history", "--no-jobs", "--quiet"])
    new = json.loads((tmp_path / "o2" / "envelopes.jsonl").read_text().splitlines()[0])
    assert any(c["field"] == "name" and c["change_type"] == "name_change" for c in new["changes"])


def test_profile_and_show(tmp_path, fake_http, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--profiles-dir", "p", "profile", EQUINOR, "--text", "--no-history", "--no-jobs"]) == 0
    assert "EQUINOR ASA" in capsys.readouterr().out
    assert cli.main(["--profiles-dir", "p", "show", EQUINOR]) == 0
    assert cli.main(["--profiles-dir", "p", "show", "100000016"]) == 1


def test_run_without_input(tmp_path, fake_http):
    assert cli.main(["--profiles-dir", str(tmp_path), "run"]) == 2


def test_update_without_profiles(tmp_path, fake_http):
    assert cli.main(["--profiles-dir", str(tmp_path), "update"]) == 1


def test_sample_from_universe(tmp_path):
    import gzip
    universe = tmp_path / "u.jsonl.gz"
    with gzip.open(universe, "wt", encoding="utf-8") as h:
        for i in range(10):
            h.write(json.dumps({"organisation_number": str(100000000 + i)}) + "\n")
    out = tmp_path / "s.jsonl"
    assert cli.main(["sample", "--universe", str(universe), "--count", "3", "--out", str(out)]) == 0
    assert len(out.read_text().splitlines()) == 3


def test_history_lane_attaches_filing_years(tmp_path, monkeypatch, equinor_routes):
    from signalpost.sources.regnskap import FILING_YEARS_URL
    routes = {**equinor_routes, FILING_YEARS_URL.format(orgnr=EQUINOR): (200, ["2024", "2025"])}
    monkeypatch.setattr(cli, "HttpClient", lambda budget: FakeClient(routes, budget=budget))
    monkeypatch.setattr(runner.config, "FILING_HISTORY_INTERVAL_SECONDS", 0)
    cli.main(["--profiles-dir", str(tmp_path / "p"), "run", EQUINOR, "--out", str(tmp_path / "o"), "--no-jobs",
              "--no-website-discovery", "--quiet"])
    env = json.loads((tmp_path / "o" / "envelopes.jsonl").read_text().splitlines()[0])
    history = next(c for c in env["claims"] if c["field"] == "filing_history")
    assert [y["year"] for y in history["value"]] == ["2025", "2024"]


def test_jobs_lane_end_to_end(tmp_path, monkeypatch, equinor_routes):
    from signalpost.sources import jobs
    uuid = "9fc82f3d-c7dc-4a8b-b3c6-ae715fd109c2"
    item = {"url": f"/api/v1/feedentry/{uuid}", "date_modified": "2026-09-21T10:00:00+02:00",
            "_feed_entry": {"uuid": uuid, "status": "ACTIVE", "businessName": "Equinor ASA"}}
    detail = {"ad_content": {"uuid": uuid, "title": "Ingeniør", "published": "2026-09-21T00:00:00+02:00",
                             "employer": {"orgnr": EQUINOR}}}
    routes = {**equinor_routes, jobs.TOKEN_URL: (200, "token:\naaa.bbb.ccc\n"),
              jobs.FEED_URL: (200, {"items": [item], "next_url": None}),
              jobs.FEED_BASE + item["url"]: (200, detail)}
    monkeypatch.setattr(cli, "HttpClient", lambda budget: FakeClient(routes, budget=budget))
    cli.main(["--profiles-dir", str(tmp_path / "p"), "run", EQUINOR, "--out", str(tmp_path / "o"), "--no-history",
              "--no-website-discovery", "--quiet"])
    env = json.loads((tmp_path / "o" / "envelopes.jsonl").read_text().splitlines()[0])
    postings = next(c for c in env["claims"] if c["field"] == "job_postings")
    assert postings["availability"] == "available" and postings["value"][0]["title"] == "Ingeniør"
    assert env["sections"]["hiring_and_activity"]["state"] == "available"
