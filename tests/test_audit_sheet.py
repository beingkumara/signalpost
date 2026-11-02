import csv
import json

from signalpost import audit, cli


def _envelope():
    return {"organisation_number": "923609016", "name": "FJORDLYS BYGG AS",
            "evidence": [{"id": "e1", "source_url": "https://fjordlys.no/kontakt"}],
            "claims": [
                {"field": "official_website", "availability": "available", "confidence": 0.97, "evidence_ids": ["e1"],
                 "value": {"final_url": "https://fjordlys.no/", "verification": "orgnr_on_page", "origin": "registry"}},
                {"field": "social_profiles", "availability": "available", "confidence": 0.97, "evidence_ids": ["e1"],
                 "value": {"facebook": "https://facebook.com/fjordlysbygg"}},
                {"field": "website_news", "availability": "available", "confidence": 0.8, "evidence_ids": ["e1"],
                 "value": [{"date": "2026-09-01", "title": "Fjordlys Bygg vinner", "url": "https://fjordlys.no/n/1",
                            "confidence": "high", "reason": "headline names FJORDLYS BYGG"}]},
                {"field": "careers_page", "availability": "ambiguous", "value": None, "evidence_ids": []},
            ]}


def test_sample_and_score_round_trip(tmp_path):
    env = tmp_path / "envelopes.jsonl"
    env.write_text(json.dumps(_envelope()) + "\n", encoding="utf-8")
    sheet = tmp_path / "sheet.csv"
    assert cli.main(["audit-sample", "--envelopes", str(env), "--out", str(sheet)]) == 0
    rows = list(csv.DictReader(sheet.open(encoding="utf-8")))
    assert {r["source_type"] for r in rows} == {"official_website", "social_profile", "website_news"}
    news = next(r for r in rows if r["source_type"] == "website_news")
    assert news["confidence"] == "high" and "headline names" in news["reason"]

    for r in rows:
        r["verdict"] = "wrong" if r["source_type"] == "social_profile" else "right"
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=audit.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    out = tmp_path / "ACCURACY.md"
    assert cli.main(["audit-score", "--sheet", str(sheet), "--out", str(out)]) == 0
    scores = json.loads(out.with_suffix(".json").read_text())
    assert scores["official_website"]["precision"] == 1.0 and scores["social_profile"]["precision"] == 0.0
    assert "| website news |" in out.read_text() and "Wrong entries" in out.read_text()


def test_bad_verdict_is_rejected(tmp_path):
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(",".join(audit.COLUMNS) + "\nwebsite_news,1,x,v,u,high,r,maybe,\n", encoding="utf-8")
    assert cli.main(["audit-score", "--sheet", str(sheet), "--out", str(tmp_path / "A.md")]) == 2


def test_unchecked_sheet_is_refused(tmp_path):
    sheet = tmp_path / "sheet.csv"
    sheet.write_text(",".join(audit.COLUMNS) + "\nwebsite_news,1,x,v,u,high,r,,\n", encoding="utf-8")
    out = tmp_path / "A.md"
    assert cli.main(["audit-score", "--sheet", str(sheet), "--out", str(out)]) == 2
    assert not out.exists()


def test_sample_takes_at_most_one_entry_per_company_per_type(tmp_path):
    env = _envelope()
    env["claims"][1]["value"] = {"facebook": "https://facebook.com/a", "instagram": "https://instagram.com/a",
                                 "linkedin": "https://linkedin.com/company/a"}
    path = tmp_path / "e.jsonl"
    path.write_text(json.dumps(env) + "\n", encoding="utf-8")
    rows = audit.sample(path, per_type=20, seed=1)
    assert sum(1 for r in rows if r["source_type"] == "social_profile") == 1
    assert sum(1 for r in rows if r["source_type"] == "social_profile")  # still sampled
    assert len(audit.sample(path, per_type=20, seed=1, per_company=3)) == 5
