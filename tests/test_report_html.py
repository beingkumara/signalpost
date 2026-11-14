"""report.html must open fast at the official batch size (1,000+ companies) on desktop and phone.

The page embeds only a small index; each company's full profile is a separate script file loaded when the
company is opened (script tags work from file://, so the report still opens by double-click).
"""

import json

from signalpost.report_html import DETAIL_DIR, write_report

MAX_INDEX_BYTES_PER_COMPANY = 600


def envelope(org, name="ACME AS", claims=200):
    big = [{"id": f"c{i}", "field": f"f{i}", "section": "annual_accounts", "label": "x" * 40, "value": "v" * 200,
            "availability": "available", "evidence_ids": ["e1"]} for i in range(claims)]
    return {"organisation_number": org, "name": name, "company_status": "active",
            "sections": {"annual_accounts": {"state": "available"}},
            "summary": {"summary": "s", "statements": []}, "claims": big,
            "evidence": [{"id": "e1", "source_url": "https://data.brreg.no/x"}], "changes": [],
            "refresh": {"checked_at": "2026-11-14"}}


def test_index_stays_small_and_each_company_gets_a_detail_file(tmp_path):
    envs = [envelope(f"{900000000 + i}") for i in range(1000)]
    write_report(tmp_path / "report.html", envs, {"run_id": "r", "emitted_envelopes": 1000})
    size = (tmp_path / "report.html").stat().st_size
    assert size < 1000 * MAX_INDEX_BYTES_PER_COMPANY + 60_000
    detail = (tmp_path / DETAIL_DIR / "900000007.js").read_text(encoding="utf-8")
    assert detail.startswith("window.SP_LOAD(") and detail.rstrip().endswith(");")
    data = json.loads(detail.strip()[len("window.SP_LOAD("):-2])
    assert data["org"] == "900000007" and len(data["claims"]) == 200


def test_rewrite_removes_stale_detail_files(tmp_path):
    write_report(tmp_path / "report.html", [envelope("900000001"), envelope("900000002")], {})
    write_report(tmp_path / "report.html", [envelope("900000001")], {})
    assert sorted(p.name for p in (tmp_path / DETAIL_DIR).iterdir()) == ["900000001.js"]


def test_hostile_text_cannot_close_the_script(tmp_path):
    write_report(tmp_path / "report.html", [envelope("900000001", name="</script><img src=x onerror=alert(1)>")], {})
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "</script><img" not in html
    detail = (tmp_path / DETAIL_DIR / "900000001.js").read_text(encoding="utf-8")
    assert "</script>" not in detail and " " not in detail


def test_page_only_loads_detail_files_for_valid_org_numbers(tmp_path):
    write_report(tmp_path / "report.html", [envelope("900000001")], {})
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "/^\\d{9}$/" in html and f'"dir":"{DETAIL_DIR}"' in html and 'META.dir+"/"' in html


def test_changes_carry_a_readable_note(tmp_path):
    env = envelope("900000001")
    env["changes"] = [{"field": "subunits", "label": "Registered workplaces", "change": "changed",
                       "change_type": "location_change", "old": [{"name": "A AVD X", "employees": 2}],
                       "new": [{"name": "A AVD X", "employees": 3}]}]
    write_report(tmp_path / "report.html", [env], {})
    detail = (tmp_path / DETAIL_DIR / "900000001.js").read_text(encoding="utf-8")
    data = json.loads(detail.strip()[len("window.SP_LOAD("):-2])
    assert data["changes"][0]["note"] == "Registered workplaces: updated A AVD X."


def test_placeholder_text_in_data_does_not_splice_the_page(tmp_path):
    write_report(tmp_path / "report.html", [envelope("900000001", name="X __META__ __INDEX__ AS")], {})
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert html.count("const META={") == 1 and "X __META__ __INDEX__ AS" in html


def test_html_comment_openers_in_data_are_escaped(tmp_path):
    write_report(tmp_path / "report.html", [envelope("900000001", name="<!--<script>")], {})
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "<!--<script>" not in html


def test_null_summary_and_odd_industry_do_not_crash(tmp_path):
    env = envelope("900000001")
    env["summary"] = None
    env["claims"].append({"id": "i", "field": "industry_codes", "value": ["x"], "availability": "available"})
    write_report(tmp_path / "report.html", [env], {})
    assert (tmp_path / DETAIL_DIR / "900000001.js").exists()


def test_index_uses_the_newest_accounting_year(tmp_path):
    env = envelope("900000001", claims=0)
    env["claims"] = [{"id": f"r{y}", "field": "revenue", "section": "annual_accounts", "as_of": f"{y}-12-31",
                      "value": {"amount": y, "currency": "NOK", "year": str(y)}, "availability": "available"}
                     for y in (2025, 2023, 2024)]
    write_report(tmp_path / "report.html", [env], {})
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert '"rev":2025' in html
    assert "function claimOf(d,f){const all=d.claims.filter(c=>c.field===f)" in html
