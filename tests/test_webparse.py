from signalpost import webparse as wp

SITE = "https://www.acme.no/"
HTML = """<html><head><title> Acme Bygg | Hjem </title>
<meta name="description" content="Acme Bygg bygger hus i Bergen.">
<meta property="og:site_name" content="Acme">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"Acme",
"telephone":"+47 55 12 34 56","sameAs":["https://www.facebook.com/acmebygg"],"vatID":"NO923609016MVA"}]}</script>
<script>var fake = "999999999";</script></head>
<body><a href="/kontakt">Kontakt oss</a><a href="/om-oss">Om oss</a><a href="/karriere">Jobb hos oss</a>
<a href="mailto:post@acme.no">mail</a><a href="mailto:someone@gmail.com">x</a><a href="tel:+4791234567">ring</a>
<a href="https://www.linkedin.com/company/acme/">in</a><a href="https://www.facebook.com/sharer.php?u=x">share</a>
<article><h2><a href="/nyheter/ny-avtale">Ny avtale med kommunen</a></h2><time datetime="2026-09-01T10:00">1. sep</time></article>
<footer>Org.nr: 923 609 016</footer></body></html>"""


def page():
    return wp.parse_page(HTML, SITE)


def test_parse_metadata_and_jsonld():
    p = page()
    assert p.title == "Acme Bygg | Hjem"
    assert p.site_name == "Acme"
    assert p.description.startswith("Acme Bygg")
    assert p.jsonld[0]["@type"] == "Organization"
    assert "999999999" not in p.text


def test_orgnr_span_handles_spacing_and_rejects_longer_numbers():
    assert "923 609 016" in wp.orgnr_span(page(), "923609016")
    other = wp.parse_page("<p>Kontonr 19236090161</p>", SITE)
    assert wp.orgnr_span(other, "923609016") is None


def test_orgnr_in_jsonld_counts_as_proof():
    only_ld = wp.parse_page('<script type="application/ld+json">{"@type":"Organization","taxID":"923609016"}</script>',
                            SITE)
    assert wp.orgnr_span(only_ld, "923609016")


def test_contacts_social_and_news():
    p = page()
    assert wp.emails(p, SITE) == ["post@acme.no"]
    assert wp.phones(p) == ["+4791234567", "+4755123456"]
    assert wp.social_links(p, ("ACME BYGG AS", SITE)) == {"linkedin": "https://www.linkedin.com/company/acme",
                                  "facebook": "https://www.facebook.com/acmebygg"}
    assert wp.dated_items(p, today="2026-10-23")[0] == {"date": "2026-09-01", "title": "Ny avtale med kommunen",
                                                        "url": "https://www.acme.no/nyheter/ny-avtale"}


def test_link_classification_and_domains():
    assert wp.classify_link("https://acme.no/kontakt", "") == "contact"
    assert wp.classify_link("https://acme.no/x", "Ledige stillinger") == "careers"
    assert wp.registrable_domain("www.shop.acme.co.uk") == "acme.co.uk"
    assert wp.same_site("https://acme.no/a", "https://www.acme.no/")
    assert not wp.same_site("https://evil.no/a", "https://www.acme.no/")


def test_name_helpers():
    assert wp.core_name("ACME BYGG AS") == "ACME BYGG"
    assert wp.core_name("AB AS") == ""
    assert wp.name_span(page(), "ACME BYGG AS") is None  # "acme" alone is too short to prove identity
    named = wp.parse_page("<title>Fjordlys Bygg | Hjem</title><p>body</p>", SITE)
    assert wp.name_span(named, "FJORDLYS BYGG AS")
    buried = wp.parse_page("<title>Velkommen</title><p>Vi samarbeider med Fjordlys Bygg AS.</p>", SITE)
    assert wp.name_span(buried, "FJORDLYS BYGG AS") is None  # only prominent places count
    footer = wp.parse_page("<title>Hjem</title><footer>© 2026 Fjordlys Bygg AS</footer>", SITE)
    assert wp.name_span(footer, "FJORDLYS BYGG AS")
    assert not wp.distinctive_name("BYGG OG EIENDOM AS") and wp.distinctive_name("FJORDLYS AS")
    assert wp.normalise_phone("0047 22 33 44 55") == "+4722334455"
    assert wp.normalise_phone("123") is None


def test_malformed_html_does_not_crash():
    assert wp.parse_page("<html><a href='/x'>unterminated", SITE).links


def test_job_postings_and_careers_platforms():
    html = """<script type="application/ld+json">{"@type":"JobPosting","title":"Tømrer","datePosted":"2026-09-01",
    "validThrough":"2026-10-01","jobLocation":{"address":{"addressLocality":"Bergen"}},"url":"https://acme.no/jobb/1"}</script>
    <a href="https://acme.teamtailor.com/jobs">Ledige stillinger</a>"""
    p = wp.parse_page(html, SITE)
    assert wp.job_postings(p, ("ACME BYGG AS", SITE), today="2026-09-15") == [{"title": "Tømrer", "published": "2026-09-01", "expires": "2026-10-01",
                                   "location": "Bergen", "url": "https://acme.no/jobb/1"}]
    assert wp.careers_platforms(p, ("ACME BYGG AS", SITE)) == {"Teamtailor": "https://acme.teamtailor.com/jobs"}


def test_parent_site_name_mention_does_not_tie_the_site():
    parent = wp.parse_page("<title>Enata</title><footer>© Enata | Prosjekter Lilleeng Helsepark</footer>",
                           "https://enata.no/")
    assert not wp.name_in_title(parent, "LILLEENG AS")
    assert not wp.domain_matches_name("https://enata.no/", "LILLEENG AS")
    assert wp.domain_matches_name("https://pellybutik.se/no/", "PELLY BUTIKKINNREDNING AS")
