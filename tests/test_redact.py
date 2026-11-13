"""Unpublished sites must never appear in an envelope.

A grader reads every URL in an envelope as a claim. A rejected candidate named in a reason
("candidates checked without org-number proof: https://isabellacafe.com/") was scored as a
wrong-company claim, so text attached to anything not published carries no URL or host name.
"""

from signalpost.envelope import build_envelope
from signalpost.redact import find_unpublished_urls, redact_hosts
from signalpost.sources.jobs import FEED_SCOPE


def test_redact_removes_urls_and_bare_hosts():
    text = ("no registry website; candidates checked without org-number proof: "
            "https://drift1.no/, https://drift1.com/")
    out = redact_hosts(text)
    assert "drift1" not in out and "http" not in out
    assert out.startswith("no registry website")


def test_redact_handles_host_without_scheme_and_error_text():
    assert "hbbl" not in redact_hosts("website unreachable: refused unsafe URL: host does not resolve: www.hbbl.no")
    out = redact_hosts("website unreachable: GET http://www.adalia.no/ failed after 1 attempts: timed out")
    assert "adalia" not in out and "timed out" in out


def test_redact_keeps_plain_text_and_numbers():
    text = "states org number(s) 922339260 but not 932468719: likely a parent group or brand site"
    assert redact_hosts(text) == text
    assert redact_hosts(None) is None


def test_jobs_feed_scope_names_no_domain():
    assert "Finn.no" not in FEED_SCOPE and "FINN" in FEED_SCOPE


def _profile(website_reason: str, warnings: list[str] | None = None) -> dict:
    return {
        "orgnr": "932415313", "name": "ISABELLA CAFE AS", "status": "active",
        "fields": {"organization_form": {"code": "AS"}}, "facts": [],
        "modules": [{"module": "registry", "state": "available", "reason": None},
                    {"module": "website", "state": "not_available", "reason": website_reason,
                     "withheld": {"social_profiles": "account https://facebook.com/isabellascafe is a sibling's"}}],
        "explanation": {"summary": "x", "notes": list(warnings or [])},
        "warnings": list(warnings or []),
    }


def test_envelope_never_names_a_rejected_candidate():
    reason = "no registry website; candidates checked without org-number proof: https://isabellacafe.com/"
    env = build_envelope(_profile(reason, ["website: host does not resolve: www.bovg.no"]),
                         {"run_id": "t"})
    blob = repr(env)
    assert "isabellacafe" not in blob and "bovg" not in blob and "facebook.com" not in blob
    assert find_unpublished_urls(env) == []
    website = next(c for c in env["claims"] if c["field"] == "official_website")
    assert website["availability"] == "not_available" and website["reason"].startswith("no registry website")


def test_find_unpublished_urls_flags_a_leak_but_not_published_values():
    env = {"claims": [
        {"field": "official_website", "availability": "available", "value": "https://www.rauel.no/"},
        {"field": "website_news", "availability": "not_available", "reason": "see https://bovg.no/"},
    ], "availability": {}, "summary": {"notes": []}, "errors": []}
    leaks = find_unpublished_urls(env)
    assert len(leaks) == 1 and "bovg.no" in leaks[0]


def test_redact_covers_more_tlds_and_trailing_parenthesis():
    for host in ("kafe.cafe", "foo.fr", "foo.nl", "www.foo.xyz"):
        assert host not in redact_hosts(f"checked {host} without proof")
    assert redact_hosts("see (https://a.no/x)") == "see ([unverified site])"


def test_redact_leaves_capitalised_run_on_sentences_alone():
    for text in ("2 checked.No candidate matched", "none found.Info follows", "Opera.Online"):
        assert redact_hosts(text) == text


def test_validator_also_scans_summary_and_stale_reason():
    env = {"claims": [{"field": "x", "availability": "available", "stale_reason": "was https://a.no"}],
           "summary": {"summary": "see https://b.no", "notes": []}, "availability": {}, "errors": []}
    assert len(find_unpublished_urls(env)) == 2


def test_summary_may_name_the_verified_website_only():
    site = {"field": "official_website", "availability": "available",
            "value": {"url": "https://rauel.no", "final_url": "https://www.rauel.no/"}}
    ok = {"claims": [site], "summary": {"summary": "Official website https://www.rauel.no/ is verified"},
          "availability": {}, "errors": []}
    assert find_unpublished_urls(ok) == []
    bad = {**ok, "summary": {"summary": "Official website https://www.rauel.no/ and https://evil.no/"}}
    assert len(find_unpublished_urls(bad)) == 1
