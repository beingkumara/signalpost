"""Registered-name variants (STI suffix, spaced initials) and careers pages that list a dated opening."""

from conftest import FakeClient

from signalpost import item_checks as ic
from signalpost import webparse as wp
from signalpost.sources.website import crawl_site

HTMLCT = "text/html; charset=utf-8"


def _crawl(site, routes, orgnr, name, extra_pages=1):
    client = FakeClient({site + "robots.txt": (404, b""), **routes}, content_types={u: HTMLCT for u in routes})
    return crawl_site(client, site, orgnr, name, discovered=False, extra_pages=extra_pages)


def test_sti_is_a_legal_suffix():
    assert wp.core_name("BERGEN BIBELSKOLE STI") == "BERGEN BIBELSKOLE"


def test_spaced_initials_match_initials_written_together():
    page = wp.parse_page("<title>ST Dahl | Fallsikringskurs</title>", "https://www.stdahl.no/")
    assert wp.name_in_title(page, "S T DAHL AS")
    dotted = wp.parse_page("<title>S.T. Dahl | Kurs</title>", "https://www.stdahl.no/")
    assert wp.name_in_title(dotted, "S T DAHL AS")
    other = wp.parse_page("<title>Dahl Bygg | Kurs</title>", "https://dahlbygg.no/")
    assert not wp.name_in_title(other, "S T DAHL AS")


def test_initials_are_one_name_token_not_dropped():
    assert ic.names_company("ST Dahl holder kurs", ("S T DAHL AS",)) == "S T DAHL"
    assert ic.names_company("Dahl holder kurs", ("S T DAHL AS",)) is None


def test_registry_site_with_name_variant_is_published_at_medium():
    site = "https://bergenbibelskole.no/"
    html = "<title>Bergen Bibelskole | Verden blir ikke den samme</title><p>© 2026 Bergen Bibelskole</p>"
    result = _crawl(site, {site: (200, html)}, "912333221", "BERGEN BIBELSKOLE STI")
    assert result.state == "available"
    assert {f.field: f for f in result.facts}["official_website"].confidence == "medium"


def test_listed_openings_need_a_future_application_deadline():
    page = wp.parse_page("<p>Produksjonsmedarbeider / Operatør Arbeidssted: Kjeller Søknadsfrist: 2026-11-22</p>"
                         "<p>Lagerarbeider Søknadsfrist: 01.10.2026</p><p>Selger søknadsfrist 5. desember 2026</p>",
                         "https://agilera.no/jobb-med-oss/")
    found = wp.listed_openings(page, today="2026-11-02")
    assert [o["deadline"] for o in found] == ["2026-11-22", "2026-12-05"]
    assert "Produksjonsmedarbeider" in found[0]["context"]
    none = wp.parse_page("<p>Vi har ingen ledige stillinger. Søknadsfrist: løpende</p>", "https://x.no/karriere")
    assert wp.listed_openings(none, today="2026-11-02") == []


SITE = "https://agilera.no/"
HOME = ("<title>Agilera Pharma</title><p>Org.nr 928 041 069</p>"
        "<a href='/kontakt/'>Kontakt</a><a href='/jobb-med-oss/'>Karriere</a>")
CAREERS = "<h1>Jobb med oss</h1><p>Produksjonsmedarbeider Arbeidssted: Kjeller Søknadsfrist: 2030-11-22</p>"


def test_careers_page_listing_a_dated_opening_is_fetched_and_published():
    routes = {SITE: (200, HOME), SITE + "kontakt/": (200, "<p>Kontakt oss</p>"), SITE + "jobb-med-oss/": (200, CAREERS)}
    result = _crawl(SITE, routes, "928041069", "AGILERA PHARMA AS", extra_pages=1)
    fields = {f.field: f for f in result.facts}
    assert fields["careers_page"].value == SITE + "jobb-med-oss/"
    assert fields["careers_page"].confidence == "medium"
    assert "2030-11-22" in fields["careers_page"].evidence["claim_span"]


def test_careers_page_without_a_dated_opening_stays_cant_tell():
    routes = {SITE: (200, HOME), SITE + "kontakt/": (200, "<p>Kontakt</p>"),
              SITE + "jobb-med-oss/": (200, "<p>Vi søker jevnlig etter nye medarbeidere.</p>")}
    result = _crawl(SITE, routes, "928041069", "AGILERA PHARMA AS")
    assert "careers_page" not in {f.field for f in result.facts}
    assert "no opening" in result.extra["withheld"]["careers_page"]


def test_holding_company_careers_page_is_the_groups():
    routes = {SITE: (200, HOME.replace("Agilera Pharma", "Agilera Holding")),
              SITE + "kontakt/": (200, "<p>Kontakt</p>"), SITE + "jobb-med-oss/": (200, CAREERS)}
    result = _crawl(SITE, routes, "928041069", "AGILERA HOLDING AS")
    assert "careers_page" not in {f.field for f in result.facts}
    assert "holding company" in result.extra["withheld"]["careers_page"]


def test_group_site_careers_page_is_not_published():
    home = HOME + "<p>Agilera Holding AS Org.nr 996 668 290</p>"
    routes = {SITE: (200, home), SITE + "kontakt/": (200, "<p>Kontakt</p>"), SITE + "jobb-med-oss/": (200, CAREERS)}
    result = _crawl(SITE, routes, "928041069", "AGILERA PHARMA AS")
    assert "careers_page" not in {f.field for f in result.facts}


def test_job_board_listing_other_employers_openings_is_not_the_companys():
    board = ("<h1>Karriere</h1><p>Ledige stillinger, karrieremesser og internships. Ledige stillinger 16 stillinger.</p>"
             "<p>Leder for kundelivssyklus og lojalitet TV 2 Søknadsfrist 2. november 2030</p>")
    routes = {SITE: (200, HOME.replace("/jobb-med-oss/", "/karriere")), SITE + "kontakt/": (200, "<p>Kontakt</p>"),
              SITE + "karriere": (200, board)}
    result = _crawl(SITE, routes, "928041069", "AGILERA PHARMA AS")
    assert "careers_page" not in {f.field for f in result.facts}
    assert "does not say they are the company's own" in result.extra["withheld"]["careers_page"]


def test_own_opening_wording_on_a_plain_careers_url_counts():
    own = "<p>Vi søker en produksjonsmedarbeider til Kjeller. Søknadsfrist: 2030-11-22</p>"
    routes = {SITE: (200, HOME.replace("/jobb-med-oss/", "/karriere")), SITE + "kontakt/": (200, "<p>Kontakt</p>"),
              SITE + "karriere": (200, own)}
    fields = {f.field: f for f in _crawl(SITE, routes, "928041069", "AGILERA PHARMA AS").facts}
    assert fields["careers_page"].value == SITE + "karriere"
