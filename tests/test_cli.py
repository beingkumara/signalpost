import json

import pytest

from conftest import EQUINOR, FakeClient
from signalpost import cli


@pytest.fixture
def fake_http(monkeypatch, equinor_routes):
    monkeypatch.setattr(cli, "HttpClient", lambda budget: FakeClient(equinor_routes, budget=budget))


def test_run_writes_profiles_results_and_report(tmp_path, fake_http, capsys):
    inputs = tmp_path / "in.txt"
    inputs.write_text(f"# header\n{EQUINOR}\n123456789\n", encoding="utf-8")
    code = cli.main(["--profiles-dir", str(tmp_path / "p"), "run", "--input", str(inputs),
                     "--out", str(tmp_path / "out"), "--no-website"])
    assert code == 0
    report = json.loads((tmp_path / "out" / "run_report.json").read_text())
    assert report["companies_completed"] == 2
    assert report["status_counts"] == {"active": 1, "invalid_input": 1}
    assert report["external_api_cost_usd"] == 0.0
    lines = (tmp_path / "out" / "results.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["orgnr"] == EQUINOR
    assert (tmp_path / "p" / f"{EQUINOR}.json").exists()


def test_second_run_detects_no_changes(tmp_path, fake_http):
    args = ["--profiles-dir", str(tmp_path / "p"), "run", EQUINOR, "--out", str(tmp_path / "o"), "--no-website"]
    cli.main(args)
    cli.main(args)
    report = json.loads((tmp_path / "o" / "run_report.json").read_text())
    assert report["profiles_changed"] == 0


def test_profile_text_and_show(tmp_path, fake_http, capsys):
    assert cli.main(["--profiles-dir", str(tmp_path), "profile", EQUINOR, "--text", "--no-website"]) == 0
    assert "EQUINOR ASA" in capsys.readouterr().out
    assert cli.main(["--profiles-dir", str(tmp_path), "show", EQUINOR]) == 0
    assert cli.main(["--profiles-dir", str(tmp_path), "show", "100000016"]) == 1


def test_run_without_input_fails(tmp_path, fake_http):
    assert cli.main(["--profiles-dir", str(tmp_path), "run"]) == 2


def test_export(tmp_path, fake_http):
    cli.main(["--profiles-dir", str(tmp_path / "p"), "profile", EQUINOR, "--no-website"])
    out = tmp_path / "all.jsonl"
    assert cli.main(["--profiles-dir", str(tmp_path / "p"), "export", "--out", str(out)]) == 0
    assert len(out.read_text().splitlines()) == 1


def test_update_with_nothing_stored(tmp_path, fake_http):
    assert cli.main(["--profiles-dir", str(tmp_path), "update"]) == 1
