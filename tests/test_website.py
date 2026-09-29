import pytest

from conftest import FakeClient
from signalpost.sources.website import core_name, fetch_website_facts, identity_check, parse_page

SITE = "https://www.acme.no"
PAGE = """<html><head><title> Acme Bygg | Home </title>
<meta name="description" content="Acme Bygg builds houses in Bergen.">
<script>var fake = "999999999";</script></head>
<body><a href="mailto:post@acme.no">mail</a><a href="mailto:someone@gmail.com">x</a>
<a href="https://www.linkedin.com/company/acme">in</a>
<footer>Org.nr: 923 609 016</footer></body></html>"""


def test_parse_page_extracts_metadata_and_ignores_scripts():
    page = parse_page(PAGE, SITE)
    assert page.title == "Acme Bygg | Home"
    assert page.description.startswith("Acme Bygg")
    assert page.social == {"linkedin": "https://www.linkedin.com/company/acme"}
    assert "999999999" not in page.text


@pytest.mark.parametrize("name,expected", [("ACME BYGG AS", "ACME BYGG"), ("EQUINOR ASA", "EQUINOR"), ("AB AS", "")])
def test_core_name_strips_legal_suffix(name, expected):
    assert core_name(name) == expected


def test_identity_levels():
    page = parse_page(PAGE, SITE)
    assert identity_check(page, "923609016", "Whatever AS")["level"] == "orgnr_on_page"
    assert identity_check(page, "100000016", "ACME BYGG AS")["level"] == "name_on_page"
    assert identity_check(page, "100000016", "OTHER FIRMA AS")["level"] == "unverified"


def _client(body, status=200, robots=(404, b""), ctype="text/html; charset=utf-8"):
    return FakeClient({SITE: (status, body), "https://www.acme.no/robots.txt": robots}, content_types={SITE: ctype})


def test_verified_site_gives_facts_and_only_same_domain_emails():
    facts, warnings = fetch_website_facts(_client(PAGE), "923609016", "ACME BYGG AS", SITE)
    by = {f.field: f for f in facts}
    assert by["website_verification"].value == "orgnr_on_page"
    assert by["website_emails"].value == ["post@acme.no"]
    assert by["website_description"].confidence == "high"
    assert warnings == []


def test_unverified_site_publishes_nothing():
    facts, warnings = fetch_website_facts(_client(PAGE), "100000016", "OTHER FIRMA AS", SITE)
    assert facts == ()
    assert "content not used" in warnings[0]


def test_robots_disallow_is_respected():
    client = _client(PAGE, robots=(200, b"User-agent: *\nDisallow: /\n"))
    facts, warnings = fetch_website_facts(client, "923609016", "ACME BYGG AS", SITE)
    assert facts == () and "robots.txt" in warnings[0]
    assert SITE not in client.calls


def test_server_error_raises_so_old_facts_are_kept():
    with pytest.raises(ConnectionError):
        fetch_website_facts(_client(PAGE, status=503), "923609016", "ACME BYGG AS", SITE)


def test_non_html_or_404_is_a_warning():
    facts, warnings = fetch_website_facts(_client(b"", status=404), "923609016", "ACME BYGG AS", SITE)
    assert facts == () and "404" in warnings[0]
