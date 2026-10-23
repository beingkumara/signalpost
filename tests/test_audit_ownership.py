"""Regression tests for the October 2026 Builderr audit.

1. The website must be the company's own, not a parent group, brand, registry or platform page.
2. Social links must be accounts whose handle matches this company (not the group, web agency or theme).
3. News must be real, dated posts (not menu labels, product pages or "modified" dates).
4. Jobs must be single ads for this company (not landing pages, NAV search links or another employer's ads).
"""

import json

from conftest import FakeClient

from signalpost import webparse as wp
from signalpost.sources.jobs import _posting
from signalpost.sources.website import crawl_site

SITE = "https://www.acme.no/"
HTMLCT = "text/html; charset=utf-8"
OWNERS = ("ACME BYGG AS", "acmebygg.no")
TODAY = "2026-10-23"


def page(html: str, url: str = SITE) -> wp.Page:
    return wp.parse_page(html, url)


def crawl(routes, redirects=None, name="FJORDLYS BYGG AS", **kwargs):
    robots = {f"https://{h}/robots.txt": (404, b"") for h in ("www.acme.no", "acme.no", "w2.brreg.no",
                                                                "sjekkpunkt.no", "www.fjordlys.no")}
    c = FakeClient({**robots, **routes}, content_types={u: HTMLCT for u in routes}, redirects=redirects)
    return crawl_site(c, SITE, "923609016", name, discovered=False, extra_pages=kwargs.pop("extra_pages", 0),
                      **kwargs)


# 1. Website ownership ----------------------------------------------------------------------------------

def test_registry_site_redirecting_to_the_business_register_is_not_published():
    brreg = "https://w2.brreg.no/enhet/sok/detalj.jsp?orgnr=923609016"
    html = "<title>Enhetsregisteret</title><p>Org.nr 923 609 016</p><a href='https://www.facebook.com/brreg'>f</a>"
    result = crawl({brreg: (200, html)}, redirects={SITE: brreg})
    assert result.state == "ambiguous" and result.facts == ()
    assert "brreg.no" in result.reason


def test_registry_site_redirecting_to_a_listing_on_a_shared_platform_is_not_published():
    listing = "https://sjekkpunkt.no/bilverksted/fjordlys"
    html = "<title>Fjordlys Bygg | Sjekkpunkt</title><p>Org.nr 923 609 016</p>"
    result = crawl({listing: (200, html)}, redirects={SITE: listing})
    assert result.state == "ambiguous" and result.facts == ()


def test_redirect_to_the_companys_own_new_domain_is_still_published():
    new = "https://www.fjordlys.no/no"
    result = crawl({new: (200, "<title>Fjordlys</title><p>Org.nr 923 609 016</p>")}, redirects={SITE: new})
    assert result.state == "available"


def test_name_only_site_that_names_another_org_number_is_a_group_site():
    html = "<title>Fjordlys Bygg</title><footer>© Fjordlys Gruppen AS · Org.nr 914 778 271</footer>"
    result = crawl({SITE: (200, html)})
    assert result.state == "ambiguous" and result.facts == ()
    assert "914778271" in result.reason


def test_registry_listing_a_social_network_page_is_not_a_website():
    fb = "https://www.facebook.com/fjordlysbygg"
    c = FakeClient({fb: (200, "<title>Fjordlys Bygg</title>")}, content_types={fb: HTMLCT})
    result = crawl_site(c, fb, "923609016", "FJORDLYS BYGG AS", discovered=False, extra_pages=0)
    assert result.state == "not_available" and result.facts == ()
    assert fb not in c.calls


def test_orgnrs_on_page_reads_labelled_numbers_only():
    p = page("<p>Org.nr: 923 609 016</p><p>Foretaksregisteret NO 914778271 MVA</p><p>Tlf 22 33 44 55 66</p>"
             "<p>Konto 1234.56.78903</p>")
    assert wp.orgnrs_on_page(p) == {"923609016", "914778271"}
    ld = page('<script type="application/ld+json">{"@type":"Organization","vatID":"NO923609016MVA"}</script>')
    assert wp.orgnrs_on_page(ld) == {"923609016"}


# 2. Social links -----------------------------------------------------------------------------------------

SOCIAL_HTML = """<a href="https://www.facebook.com/Wix">Laget med Wix</a>
<a href="https://www.facebook.com/acmebygg">Facebook</a>
<a href="https://www.instagram.com/webbyraaet">Nettside av Webbyrået</a>
<a href="https://www.instagram.com/acme.bygg/">Instagram</a>
<a href="https://www.linkedin.com/company/fjordlys-gruppen">Konsernet</a>
<a href="https://www.youtube.com/watch?v=abc123">Se filmen</a>
<a href="https://www.facebook.com/profile.php?id=10001">Profil</a>
<a href="https://www.linkedin.com/in/ola-nordmann">Daglig leder</a>"""


def test_social_links_keep_only_accounts_whose_handle_matches_the_company():
    assert wp.social_links(page(SOCIAL_HTML), OWNERS) == {"facebook": "https://www.facebook.com/acmebygg",
                                                          "instagram": "https://www.instagram.com/acme.bygg"}


def test_social_links_skip_a_theme_link_and_keep_the_companys_own_later_link():
    html = ("<a href='https://www.facebook.com/elementor'>theme</a>"
            "<a href='https://www.facebook.com/acmebyggas'>us</a>")
    assert wp.social_links(page(html), OWNERS) == {"facebook": "https://www.facebook.com/acmebyggas"}


def test_channel_id_is_accepted_only_from_the_companys_own_structured_data():
    own = page('<script type="application/ld+json">{"@type":"Organization","name":"Acme Bygg AS",'
               '"sameAs":["https://www.youtube.com/channel/UCabc"]}</script>')
    other = page('<script type="application/ld+json">{"@type":"Organization","name":"Webbyrået AS",'
                 '"sameAs":["https://www.youtube.com/channel/UCxyz"]}</script>')
    assert wp.social_links(own, OWNERS) == {"youtube": "https://www.youtube.com/channel/UCabc"}
    assert wp.social_links(other, OWNERS) == {}


def test_handle_matching():
    assert wp.handle_matches("acmebyggas", OWNERS)
    assert wp.handle_matches("acme_bygg", OWNERS)
    assert not wp.handle_matches("wix", OWNERS)
    assert not wp.handle_matches("bygg", OWNERS)  # a generic word is not this company
    assert wp.handle_matches("uppigardnatadal", ("NATADAL AS", "natadal.no"))
    assert not wp.handle_matches("frihetspsykologen", ("LIVSKARTET AS", "livskartet.no"))


def test_crawled_site_publishes_only_matching_profiles():
    html = ("<title>Fjordlys Bygg</title><p>Org.nr 923 609 016</p>"
            "<a href='https://www.facebook.com/Wix'>x</a><a href='https://www.facebook.com/fjordlysbygg'>f</a>")
    result = crawl({SITE: (200, html)})
    assert {f.field: f.value for f in result.facts}["social_profiles"] == {
        "facebook": "https://www.facebook.com/fjordlysbygg"}


# 3. News ---------------------------------------------------------------------------------------------------

def test_article_block_with_headline_link_is_news():
    html = """<article><h2><a href="/nyheter/ny-avtale">Ny avtale med kommunen</a></h2>
    <time class="entry-date published" datetime="2026-09-01T10:00">1. september 2026</time></article>"""
    assert wp.dated_items(page(html, "https://www.acme.no/nyheter"), today=TODAY) == [
        {"date": "2026-09-01", "title": "Ny avtale med kommunen", "url": "https://www.acme.no/nyheter/ny-avtale"}]


def test_date_only_text_and_menu_labels_are_not_news():
    html = """<p><time datetime="2015-10-25">25. oktober 2015</time></p>
    <article><time datetime="2026-09-01">1. sep</time><a href="/nyheter">Se flere nyheter</a></article>
    <article><time datetime="2026-09-02">2. sep</time><a href="/kontakt">Kontakt oss</a></article>"""
    assert wp.dated_items(page(html), today=TODAY) == []


def test_modified_dates_are_not_publish_dates():
    html = """<article><h2><a href="/nyheter/a">Vi har oppdatert prislisten</a></h2>
    <time class="updated" datetime="2026-09-03">3. sep</time></article>
    <meta property="article:modified_time" content="2026-09-04">"""
    assert wp.dated_items(page(html), today=TODAY) == []


def test_single_post_page_with_published_time_is_news():
    html = """<meta property="og:type" content="article"><meta property="og:title" content="Vi åpner i Bergen">
    <meta property="article:published_time" content="2026-08-15T08:00:00+00:00"><title>Vi åpner i Bergen - Acme</title>"""
    assert wp.dated_items(page(html, "https://www.acme.no/vi-apner-i-bergen/"), today=TODAY) == [
        {"date": "2026-08-15", "title": "Vi åpner i Bergen", "url": "https://www.acme.no/vi-apner-i-bergen/"}]


def test_article_markup_on_service_privacy_or_home_pages_is_not_news():
    ld = '<script type="application/ld+json">{"@type":"Article","headline":"%s","datePublished":"2025-04-24"}</script>'
    for url, headline in (("https://www.acme.no/behandlingsmetoder/", "Behandlingsmetoder i terapi"),
                          ("https://www.acme.no/personvern/", "Personvern i Acme"),
                          ("https://www.acme.no/", "Velkommen til Acme Bygg")):
        assert wp.dated_items(page(ld % headline, url), today=TODAY) == [], url
    news = page(ld % "Acme vinner pris for beste bygg", "https://www.acme.no/nyheter/acme-vinner-pris")
    assert wp.dated_items(news, today=TODAY)[0]["title"] == "Acme vinner pris for beste bygg"


def test_future_dates_are_not_news():
    html = '<article><h2><a href="/nyheter/kurs">Kurs i sikkerhet for alle ansatte</a></h2>' \
           '<time datetime="2027-01-10">10. jan</time></article>'
    assert wp.dated_items(page(html), today=TODAY) == []


# 4. Jobs ---------------------------------------------------------------------------------------------------

def test_careers_classifier_needs_a_whole_word():
    assert wp.classify_link("https://ulving.no/?page_id=2", "Utstillinger") is None
    assert wp.classify_link("https://pellybutik.se/no/fran-butiksjobb-till-vd-for-gekas/", "Les mer") is None
    assert wp.classify_link("https://acme.no/jobb-hos-oss", "") == "careers"
    assert wp.classify_link("https://acme.no/x", "Ledige stillinger") == "careers"
    assert wp.classify_link("https://acme.no/karriere/", "") == "careers"


def test_news_article_mentioning_a_vacancy_is_not_the_careers_page():
    url = "https://www.steinkjer24.no/nyheter/n/d4ApRo/enorm-interesse-51-sokere-pa-jobben"
    assert wp.classify_link(url, "Enorm interesse: 51 søkere på jobben") != "careers"


def test_careers_platforms_keep_only_the_companys_own_account():
    html = """<a href="https://arbeidsplassen.nav.no/stillinger?q=acme">Se ledige stillinger hos NAV</a>
    <a href="https://www.jobbnorge.no/ledige-stillinger/stilling/307054/stipendiat-i-medievitskap">Stipendiat</a>
    <a href="https://238765.webcruiter.no/Main2/recruit/public/5173454162">Søk her</a>
    <a href="https://acmebygg.teamtailor.com/jobs">Ledige stillinger</a>"""
    assert wp.careers_platforms(page(html), OWNERS) == {"Teamtailor": "https://acmebygg.teamtailor.com/jobs"}


def _jobposting(**extra) -> str:
    body = {"@type": "JobPosting", "title": "Tømrer", "datePosted": "2026-09-01", "validThrough": "2026-11-01",
            "url": "https://www.acme.no/jobb/tomrer", **extra}
    return f'<script type="application/ld+json">{json.dumps({k: v for k, v in body.items() if v})}</script>'


def test_job_posting_for_another_employer_is_dropped():
    other = page(_jobposting(hiringOrganization={"@type": "Organization", "name": "Fjordlys Gruppen AS"}))
    own = page(_jobposting(hiringOrganization={"@type": "Organization", "name": "Acme Bygg AS"}))
    assert wp.job_postings(other, OWNERS, today=TODAY) == []
    assert wp.job_postings(own, OWNERS, today=TODAY)[0]["url"] == "https://www.acme.no/jobb/tomrer"


def test_expired_job_posting_is_dropped():
    assert wp.job_postings(page(_jobposting(validThrough="2026-09-30")), OWNERS, today=TODAY) == []


def test_listing_page_postings_without_their_own_url_are_dropped():
    html = _jobposting(url=None) + _jobposting(title="Snekker", url=None)
    assert wp.job_postings(page(html, "https://www.acme.no/karriere"), OWNERS, today=TODAY) == []


def test_nav_posting_links_to_the_single_ad_not_a_search_or_landing_page():
    uuid = "cbf5160d-04b3-435d-839f-12c0aba0e90a"
    content = {"uuid": uuid, "title": "Renholder", "link": None, "sourceurl": "https://www.acme.no/karriere"}
    assert _posting(content, {"orgnr": "923609016"})["url"] == \
        f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"


# Review follow-ups ------------------------------------------------------------------------------------------

def test_group_site_listing_many_org_numbers_needs_a_title_or_domain_tie():
    html = ("<title>Konsernet</title><p>Våre selskaper: Fjordlys Bygg AS org.nr 923 609 016, "
            "Nordvik Tak AS org.nr 914 778 271</p><a href='https://www.facebook.com/konsernet'>f</a>")
    result = crawl({SITE: (200, html)})
    assert result.state == "ambiguous" and result.facts == ()


def test_own_site_naming_its_accountant_is_still_published():
    html = ("<title>Fjordlys Bygg</title><p>Org.nr 923 609 016</p>"
            "<p>Regnskap: Tall AS org.nr 914 778 271</p>")
    assert crawl({SITE: (200, html)}).state == "available"


def test_brand_name_must_match_the_company():
    html = ("<title>Fjordlys Bygg</title><meta property='og:site_name' content='Webbyrået'>"
            "<p>Org.nr 923 609 016</p>")
    assert "brand_name" not in {f.field for f in crawl({SITE: (200, html)}).facts}


def test_handle_matching_ignores_place_names_and_partial_handles():
    owners = ("ACME BYGG OSLO AS", "acmebyggoslo.no")
    assert not wp.handle_matches("oslo", owners)
    assert not wp.handle_matches("visitoslo", owners)
    assert not wp.handle_matches("byggo", ("BERGEN BYGGOPPDRAG AS", "bbo.no"))
    assert wp.handle_matches("acmebyggoslo", owners)


def test_recruitment_platform_account_must_be_in_the_host():
    html = '<a href="https://www.finn.no/job/acmebygg">Jobb</a><a href="https://acmebygg.recman.page/">Jobb</a>'
    assert wp.careers_platforms(page(html), OWNERS) == {"Recman": "https://acmebygg.recman.page/"}


def test_careers_page_label_says_it_is_not_a_job_ad():
    html = "<title>Fjordlys Bygg</title><p>Org.nr 923 609 016</p><a href='/karriere'>Karriere</a>"
    fact = {f.field: f for f in crawl({SITE: (200, html)}).facts}["careers_page"]
    assert "not a job ad" in fact.label


def test_malformed_urls_do_not_crash_parsing_or_filters():
    html = ('<a href="http://[bad">x</a><script type="application/ld+json">[{"@type":"Organization",'
            '"name":"Acme Bygg","sameAs":["http://[bad"]},{"@type":"NewsArticle","url":"http://[bad",'
            '"headline":"Acme vinner pris for bygg","datePublished":"2026-01-01"}]</script>')
    p = page(html)
    assert wp.social_links(p, OWNERS) == {} and wp.dated_items(p, today=TODAY) == []
    assert not wp.not_company_site("http://[bad")


def test_post_slugs_containing_page_words_are_still_news():
    for slug in ("we-are-about-to-launch", "ny-kontaktperson-i-bergen"):
        html = f'<article><h2><a href="/nyheter/{slug}">Ny kontaktperson for kundene i Bergen</a></h2>' \
               '<time datetime="2026-09-01">1. sep</time></article>'
        assert wp.dated_items(page(html), today=TODAY), slug


def test_short_real_headline_is_news_but_menu_labels_are_not():
    assert wp.is_headline("Pressekonferanse i Oslo")
    assert not wp.is_headline("Kontakt oss")
    assert not wp.is_headline("Jobb hos oss")


def test_link_wrapping_the_heading_and_unclosed_tags():
    wrapped = '<article><a href="/nyheter/ny-sjef"><h2>Ny daglig leder</h2></a><time datetime="2026-09-01">x</time></article>'
    assert wp.dated_items(page(wrapped), today=TODAY)[0]["url"] == "https://www.acme.no/nyheter/ny-sjef"
    unclosed = ('<article><h2><a href="/nyheter/a">Første sak om noe viktig</a></h2><time datetime="2026-09-01">x</time>'
                '<article><h2><a href="/nyheter/b">Andre sak om noe viktig</a></h2><time datetime="2026-09-02">y</time>'
                '</article>')
    assert len(wp.dated_items(page(unclosed), today=TODAY)) == 2


def test_impossible_dates_and_list_types():
    bad = '<article><h2><a href="/nyheter/a">En sak med ugyldig dato</a></h2><time datetime="2026-02-31">x</time></article>'
    assert wp.dated_items(page(bad), today=TODAY) == []
    listed = page('<script type="application/ld+json">{"@type":["NewsArticle"],"url":"https://www.acme.no/nyheter/x",'
                  '"headline":"Acme vinner pris for bygg","datePublished":"2026-01-01"}</script>')
    assert wp.dated_items(listed, today=TODAY)


def test_sharepoint_account_is_not_a_share_link():
    assert wp.social_account("https://www.facebook.com/sharepointacme")[1] == "sharepointacme"


def test_discovered_brand_site_of_another_entity_says_why_it_was_rejected():
    # vaattogvilt.no: footer "Vått og Vilt – Org.nr 992 957 433"; our company only named as the shop's seller
    home = "<title>Vått og Vilt</title><footer>Vått og Vilt – Org.nr 914 778 271</footer><a href='/vilkar'>Vilkår</a>"
    terms = "<p>Selger: Fjordlys Bygg AS orgnr: 923609016</p><footer>Org.nr 914 778 271</footer>"
    routes = {SITE: (200, home), "https://www.acme.no/vilkar": (200, terms), "https://www.acme.no/robots.txt": (404, b"")}
    c = FakeClient(routes, content_types={u: HTMLCT for u in routes})
    result = crawl_site(c, SITE, "923609016", "FJORDLYS BYGG AS", discovered=True, extra_pages=2)
    assert result.state == "not_available" and "brand site" in result.reason


def test_handle_rule_matches_the_organisers_starter_kit():
    # Starter kit identity.assess_social_identity: the whole name, a single-word name, or >= 75% of
    # the name's words (at least two). One word of a longer name is not enough.
    jo = ("JO HOLUM SPORTSERVICE AS", "joholum.no")
    assert not wp.handle_matches("holumdombas", jo)
    assert wp.handle_matches("joholumsportservice", jo)
    assert not wp.handle_matches("acmegroup", ("ACME BYGG OSLO AS", "acmebyggoslo.no"))
    assert wp.handle_matches("nordvikbyggtak", ("NORDVIK BYGG OG TAK AS", "nbt.no"))  # 3 of 3 words
    assert wp.handle_matches("PellyButiksinredningar", ("PELLY BUTIKKINNREDNING AS", "pellybutik.se"))  # domain
