import json
import time

import conftest
from conftest import FakeClient
from signalpost.sources import discovery, jobs


def test_candidate_domains():
    assert discovery.candidate_domains("FJELLGLØD HOLDING AS") == [
        "fjellglodholding.no", "fjellglod-holding.no", "fjellglod.no", "fjellglodholding.com"]
    assert discovery.candidate_domains("AB AS") == []


def test_email_domain_candidate_skips_webmail():
    assert discovery.email_domain_candidate("post@acme.no") == "acme.no"
    assert discovery.email_domain_candidate("ola@gmail.com") is None
    assert discovery.email_domain_candidate(None) is None


def test_discovery_uses_email_domain_and_requires_orgnr(monkeypatch):
    conftest.RESOLVABLE.add("acme-post.no")
    try:
        home = "https://acme-post.no/"
        c = FakeClient({home: (200, "<title>Acme</title><footer>Org.nr 923609016</footer>")},
                       content_types={home: "text/html"})
        result = discovery.discover_website(c, "923609016", "ACME BYGG AS", email="post@acme-post.no")
        assert result.state == "available"
        assert result.facts[0].value["origin"] == "registry_email_domain"
    finally:
        conftest.RESOLVABLE.discard("acme-post.no")


def test_discovery_with_no_candidates():
    result = discovery.discover_website(FakeClient({}), "923609016", "ACME BYGG AS")
    assert result.state == "not_available" and "no resolvable" in result.reason


TOKEN = "aaa.bbb.ccc"
UUID = "9fc82f3d-c7dc-4a8b-b3c6-ae715fd109c2"


def _feed_item(uuid, name, status="ACTIVE"):
    return {"url": f"/api/v1/feedentry/{uuid}", "date_modified": "2026-09-21T10:00:00+02:00",
            "_feed_entry": {"uuid": uuid, "status": status, "title": "Tømrer", "businessName": name}}


def _detail(orgnr):
    return {"ad_content": {"uuid": UUID, "title": "Tømrer", "published": "2026-09-21T00:00:00+02:00",
                           "expires": "2026-10-18T00:00:00+02:00", "link": f"https://arbeidsplassen.nav.no/stillinger/stilling/{UUID}",
                           "workLocations": [{"city": "BERGEN"}], "positioncount": 2,
                           "employer": {"orgnr": orgnr, "name": "Acme Bygg AS", "homepage": "https://acme.no/"}}}


def _index(items):
    c = FakeClient({jobs.FEED_URL: (200, {"items": items, "next_url": None})})
    return jobs.build_index(c, TOKEN, 45, time.monotonic() + 60)


def _detail_client(orgnr):
    return FakeClient({jobs.FEED_BASE + f"/api/v1/feedentry/{UUID}": (200, _detail(orgnr))})


def test_public_token_parsing():
    c = FakeClient({jobs.TOKEN_URL: (200, "Current public token for Nav Job Vacancy Feed:\n" + TOKEN + "\n")},
                   content_types={jobs.TOKEN_URL: "text/plain"})
    assert jobs.public_token(c) == TOKEN


def test_index_keeps_only_active_and_matches_all_name_tokens():
    index = _index([_feed_item(UUID, "Acme Bygg AS avd Bergen"), _feed_item("x1", "Acme Bygg AS"),
                    _feed_item("x1", "Acme Bygg AS", status="INACTIVE"), _feed_item("x2", "Acme Frisør")])
    assert index.complete and [a["uuid"] for a in index.candidates("ACME BYGG AS")] == [UUID]


def test_job_published_only_when_employer_orgnr_matches():
    index = _index([_feed_item(UUID, "Acme Bygg AS")])
    result = jobs.jobs_for_company(_detail_client("923609016"), TOKEN, index, "923609016", "ACME BYGG AS", set())
    fields = {f.field: f for f in result.facts}
    assert result.state == "available"
    assert fields["job_postings"].value[0]["location"] == "BERGEN"
    assert fields["job_postings"].source.human_url.endswith(UUID)
    assert fields["hiring"].value is True


def test_job_from_subunit_counts_and_other_company_is_rejected():
    index = _index([_feed_item(UUID, "Acme Bygg AS")])
    sub = jobs.jobs_for_company(_detail_client("976010043"), TOKEN, index, "923609016", "ACME BYGG AS", {"976010043"})
    assert sub.state == "available"
    other = jobs.jobs_for_company(_detail_client("111111111"), TOKEN, index, "923609016", "ACME BYGG AS", set())
    assert other.state == "not_available" and other.facts == ()


def test_no_candidates_and_incomplete_feed():
    index = _index([])
    assert jobs.jobs_for_company(FakeClient({}), TOKEN, index, "923609016", "ACME BYGG AS", set()).state == "not_available"
    broken = jobs.build_index(FakeClient({jobs.FEED_URL: (500, b"")}), TOKEN, 45, time.monotonic() + 60)
    assert not broken.complete
    assert jobs.jobs_for_company(FakeClient({}), TOKEN, broken, "923609016", "ACME BYGG AS", set()).state == "blocked"


def test_email_domain_with_name_only_is_accepted_but_unrelated_site_is_not():
    conftest.RESOLVABLE.update({"acme-post.no", "adviser.no"})
    try:
        home, other = "https://acme-post.no/", "https://adviser.no/"
        c = FakeClient({home: (200, "<title>Acme Bygg</title>"), other: (200, "<title>Adviser AS</title>")},
                       content_types={home: "text/html", other: "text/html"})
        ok = discovery.discover_website(c, "923609016", "ACME BYGG AS", email="post@acme-post.no")
        assert ok.state == "available" and ok.facts[0].confidence == "medium"
        no = discovery.discover_website(c, "923609016", "ACME BYGG AS", email="me@adviser.no")
        assert no.state == "not_available"
    finally:
        conftest.RESOLVABLE.difference_update({"acme-post.no", "adviser.no"})
