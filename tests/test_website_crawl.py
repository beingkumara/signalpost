from conftest import FakeClient
from signalpost.sources.website import crawl_site
from test_webparse import HTML

SITE = "https://www.acme.no/"
HTMLCT = "text/html; charset=utf-8"


def client(routes, robots=(404, b"")):
    all_routes = {"https://www.acme.no/robots.txt": robots, **routes}
    return FakeClient(all_routes, content_types={u: HTMLCT for u in routes})


def test_registry_site_with_orgnr_publishes_facts():
    result = crawl_site(client({SITE: (200, HTML)}), SITE, "923609016", "ACME BYGG AS", discovered=False,
                        extra_pages=2)
    fields = {f.field: f for f in result.facts}
    assert result.state == "available"
    assert fields["official_website"].value["verification"] == "orgnr_on_page"
    assert fields["official_website"].confidence == "high"
    assert "923 609 016" in fields["official_website"].evidence["claim_span"]
    assert fields["website_emails"].value == ["post@acme.no"]
    assert fields["careers_page"].value == "https://www.acme.no/karriere"
    assert fields["social_profiles"].value["linkedin"].endswith("/company/acme")
    assert fields["brand_name"].value == "Acme"
    assert all(f.source.content_sha256 for f in result.facts)


def test_orgnr_found_on_contact_page():
    home = "<title>Acme Bygg</title><a href='/kontakt'>Kontakt</a>"
    contact = "<p>Acme Bygg AS, org nr 923609016</p>"
    c = client({SITE: (200, home), "https://www.acme.no/kontakt": (200, contact)})
    result = crawl_site(c, SITE, "923609016", "ACME BYGG AS", discovered=True, extra_pages=2)
    assert result.state == "available"
    assert {f.field: f for f in result.facts}["official_website"].evidence["proof_url"].endswith("/kontakt")


def test_discovered_site_without_orgnr_is_rejected():
    c = client({SITE: (200, "<title>Acme Bygg</title><p>Acme Bygg</p>")})
    result = crawl_site(c, SITE, "923609016", "ACME BYGG AS", discovered=True, extra_pages=1)
    assert result.state == "not_available" and result.facts == ()


def test_registry_site_with_name_only_is_medium_confidence():
    c = client({SITE: (200, "<title>Fjordlys Bygg</title>")})
    result = crawl_site(c, SITE, "923609016", "FJORDLYS BYGG AS", discovered=False, extra_pages=0)
    assert result.state == "available"
    assert result.facts[0].confidence == "medium"


def test_registry_site_with_generic_name_only_is_ambiguous():
    c = client({SITE: (200, "<title>Acme Bygg</title>")})
    assert crawl_site(c, SITE, "923609016", "ACME BYGG AS", discovered=False, extra_pages=0).state == "ambiguous"


def test_unreachable_www_falls_back_to_bare_domain():
    c = client({"https://acme.no/": (200, HTML)})
    c.fail.add(SITE)
    result = crawl_site(c, SITE, "923609016", "ACME BYGG AS", discovered=False, extra_pages=0)
    assert result.state == "available"


def test_orgnr_found_via_guessed_contact_path():
    home = "<title>Fjordlys</title><p>no links rendered without javascript</p>"
    c = client({SITE: (200, home), "https://www.acme.no/kontakt": (200, "<p>Org.nr. 923 609 016</p>")})
    result = crawl_site(c, SITE, "923609016", "FJORDLYS BYGG AS", discovered=True, extra_pages=2)
    assert result.state == "available"


def test_registry_site_unrelated_content_is_ambiguous():
    c = client({SITE: (200, "<title>Domain for sale</title>")})
    result = crawl_site(c, SITE, "923609016", "ACME BYGG AS", discovered=False, extra_pages=0)
    assert result.state == "ambiguous" and result.facts == ()


def test_robots_disallow_blocks():
    c = client({SITE: (200, HTML)}, robots=(200, b"User-agent: *\nDisallow: /\n"))
    result = crawl_site(c, SITE, "923609016", "ACME BYGG AS", discovered=False, extra_pages=1)
    assert result.state == "blocked"
    assert SITE not in c.calls


def test_server_error_is_failed():
    routes = {u: (503, b"") for u in (SITE, "https://acme.no/", "http://www.acme.no/")}
    result = crawl_site(client(routes), SITE, "923609016", "ACME BYGG AS", discovered=False, extra_pages=0)
    assert result.state == "failed"


def test_non_html_is_not_available():
    c = FakeClient({SITE: (200, b"%PDF")}, content_types={SITE: "application/pdf"})
    assert crawl_site(c, SITE, "923609016", "X AS", discovered=False, extra_pages=0).state == "not_available"


def test_budget_exhaustion_is_blocked():
    c = FakeClient({SITE: (200, HTML)}, budget=0)
    assert crawl_site(c, SITE, "923609016", "X AS", discovered=False, extra_pages=0).state == "blocked"
