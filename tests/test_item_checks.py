"""Per-entry confidence and reasons for news and jobs (Builderr feedback, October 2026)."""

from conftest import FakeClient

from signalpost import item_checks as ic
from signalpost.sources.website import crawl_site

SITE = "https://www.fjordlys.no/"
NAME = "FJORDLYS BYGG AS"
OWNERS = (NAME, SITE)
HTMLCT = "text/html; charset=utf-8"


def crawl(html: str, extra: dict | None = None):
    routes = {SITE: (200, html), **(extra or {})}
    client = FakeClient({"https://www.fjordlys.no/robots.txt": (404, b""), **routes},
                        content_types={u: HTMLCT for u in routes})
    return crawl_site(client, SITE, "923609016", NAME, discovered=False, extra_pages=2)


def news(date: str, title: str, excerpt: str = "") -> dict:
    return {"date": date, "title": title, "url": SITE + "nyheter/x", "excerpt": excerpt, "kind": "article"}


def test_names_company_needs_the_whole_name_not_one_generic_word():
    assert ic.names_company("Fjordlys Bygg vinner anbud", OWNERS) == "FJORDLYS BYGG"
    assert ic.names_company("Ny avtale for FjordlysBygg", OWNERS) == "FJORDLYS BYGG"
    assert ic.names_company("Les mer på fjordlys.no", OWNERS) == "Fjordlys (domain fjordlys.no)"
    assert ic.names_company("Bygg-bransjen i vekst", OWNERS) is None
    assert ic.names_company("Fjordbygg åpner kontor", OWNERS) is None


def test_generic_single_word_names_never_match():
    assert ic.names_company("Ny hall for bygg", ("BYGG AS",)) is None


def test_headline_naming_company_is_high_with_reason():
    item = ic.assess_news(news("2026-09-01", "Fjordlys Bygg vinner anbud i Bergen"), OWNERS, "high")
    assert item["confidence"] == "high"
    assert "headline names FJORDLYS BYGG" in item["reason"] and "2026-09-01" in item["reason"]
    assert "excerpt" not in item and "kind" not in item


def test_name_only_in_article_text_is_medium():
    item = ic.assess_news(news("2026-09-01", "Ny avtale med kommunen", "Fjordlys Bygg har signert ..."),
                          OWNERS, "high")
    assert item["confidence"] == "medium" and "article text names" in item["reason"]


def test_article_not_naming_company_is_cant_tell():
    assert ic.assess_news(news("2026-09-01", "Ny avtale med kommunen", "Vi har signert ..."), OWNERS, "high") is None


def test_site_confidence_caps_item_confidence():
    item = ic.assess_news(news("2026-09-01", "Fjordlys Bygg vinner anbud"), OWNERS, "medium")
    assert item["confidence"] == "medium"


def test_job_ad_without_employer_is_cant_tell():
    job = {"title": "Tømrer", "published": "2026-09-01", "expires": None, "location": None,
           "url": SITE + "jobb/1", "employer": None}
    assert ic.assess_site_job(job, OWNERS, "high") is None
    named = ic.assess_site_job({**job, "employer": "Fjordlys Bygg AS", "expires": "2026-12-01"}, OWNERS, "high")
    assert named["confidence"] == "high" and "open until 2026-12-01" in named["reason"]
    assert "employer" not in named


def test_nav_reason_names_the_matching_org_number():
    assert "is this company" in ic.nav_job_reason({"employer_orgnr": "923609016"}, "923609016")
    assert "sub-unit 973861883" in ic.nav_job_reason({"employer_orgnr": "973861883"}, "923609016")


def test_login_pages_are_not_careers_pages():
    assert ic.login_url("https://acme.hrmanager.no/login")
    assert ic.login_url("https://www.acme.no/min-side/logg-inn")
    assert not ic.login_url("https://www.acme.no/karriere")
    assert not ic.login_url("https://design-studio.no/jobb")


def test_crawl_publishes_only_news_that_names_the_company():
    html = ("<title>Fjordlys Bygg</title><p>Org.nr 923 609 016</p>"
            "<article><h2><a href='/nyheter/a'>Fjordlys Bygg vinner anbud</a></h2><time datetime='2026-09-01'>1</time></article>"
            "<article><h2><a href='/nyheter/b'>Sommerfest for hele bransjen</a></h2><time datetime='2026-08-01'>1</time></article>")
    result = crawl(html)
    items = {f.field: f for f in result.facts}["website_news"].value
    assert [i["title"] for i in items] == ["Fjordlys Bygg vinner anbud"]
    assert items[0]["confidence"] == "high" and items[0]["reason"]
    assert "1 other dated post" in result.extra["withheld"]["website_news"]


def test_group_careers_page_ads_for_other_companies_are_cant_tell():
    ad = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Elektriker","url":"%s",'
          '"hiringOrganization":{"name":"%s"}}</script>')
    html = ("<title>Fjordlys Bygg</title><p>Org.nr 923 609 016</p><a href='/karriere'>Karriere</a>"
            + ad % (SITE + "jobb/1", "Nordvik Elektro AS"))
    result = crawl(html)
    fields = {f.field for f in result.facts}
    assert "website_job_postings" not in fields and "careers_page" not in fields
    assert "do not name this company" in result.extra["withheld"]["website_job_postings"]


def test_careers_page_published_when_an_opening_for_the_company_is_seen():
    html = ("<title>Fjordlys Bygg</title><p>Org.nr 923 609 016</p><a href='/karriere'>Karriere</a>"
            '<script type="application/ld+json">{"@type":"JobPosting","title":"Tømrer","url":"%sjobb/1",'
            '"hiringOrganization":{"name":"Fjordlys Bygg AS"}}</script>' % SITE)
    fields = {f.field: f for f in crawl(html).facts}
    assert fields["website_job_postings"].value[0]["confidence"] == "high"
    assert fields["careers_page"].value == SITE + "karriere"


def test_envelope_shows_cant_tell_with_reason():
    from signalpost.envelope import build_envelope

    profile = {"orgnr": "923609016", "status": "active", "facts": [], "fields": {},
               "modules": [{"module": "website", "state": "available",
                            "withheld": {"careers_page": "careers link found but no opening was seen"}}]}
    claims = {c["field"]: c for c in build_envelope(profile, {"run_id": "t"})["claims"]}
    assert claims["careers_page"]["availability"] == "ambiguous"
    assert claims["careers_page"]["reason"].startswith("can't tell: careers link found")
    assert claims["website_news"]["availability"] == "not_available"


def test_jysk_global_accounts_are_parent_group():
    accounts = {"facebook": "https://www.facebook.com/JYSK.no", "instagram": "https://www.instagram.com/jysknorge",
                "youtube": "https://www.youtube.com/@jyskyoutubenorge", "linkedin": "https://www.linkedin.com/company/jysk",
                "tiktok": "https://www.tiktok.com/@jysk"}
    handles = {"facebook": "JYSK.no", "instagram": "jysknorge", "youtube": "jyskyoutubenorge", "linkedin": "jysk",
               "tiktok": "jysk"}
    dropped = ic.parent_group_accounts(accounts, handles, "JYSK AS")
    assert set(dropped) == {"linkedin", "tiktok"} and "international group" in dropped["linkedin"]


def test_regional_handle_is_parent_group_unless_in_the_name():
    acc = {"facebook": "https://www.facebook.com/NTTDATABusinessSolutionsNordics"}
    handles = {"facebook": "NTTDATABusinessSolutionsNordics"}
    assert "region or group" in ic.parent_group_accounts(acc, handles, "NTT DATA BUSINESS SOLUTIONS AS")["facebook"]
    assert ic.parent_group_accounts({"linkedin": "x"}, {"linkedin": "nordicfish"}, "NORDIC FISH AS") == {}


def test_single_unmarked_accounts_are_kept():
    acc = {"facebook": "a", "linkedin": "b"}
    handles = {"facebook": "musikkiinnlandet", "linkedin": "musikk-i-innlandet"}
    assert ic.parent_group_accounts(acc, handles, "MUSIKK I INNLANDET AS") == {}


def test_social_on_a_group_site_listing_other_org_numbers_is_cant_tell():
    html = ("<title>Fjordlys Bygg</title><p>Fjordlys Bygg AS Org.nr 923 609 016</p>"
            "<p>Fjordlys Holding AS Org.nr 996 668 290</p>"
            "<a href='https://www.facebook.com/fjordlysbygg'>fb</a>")
    result = crawl(html)
    assert "social_profiles" not in {f.field for f in result.facts}
    assert "996668290" in result.extra["withheld"]["social_profiles"]


def test_unmarked_accounts_next_to_a_regional_one_are_the_groups():
    acc = {"facebook": "a", "linkedin": "b"}
    handles = {"facebook": "NTTDATABusinessSolutionsNordics", "linkedin": "ntt-data-business-solutions"}
    assert set(ic.parent_group_accounts(acc, handles, "NTT DATA BUSINESS SOLUTIONS AS")) == {"facebook", "linkedin"}


def test_wordpress_pages_marked_as_article_are_not_news():
    from signalpost import webparse as wp

    meta = ('<meta property="og:type" content="article"><meta property="og:title" content="{t}">'
            '<meta property="article:published_time" content="2026-05-27T08:00:00+00:00">')
    about = meta.format(t="Om Akutt Kiropraktikk Oslo") + '<body class="wp-singular page-template page page-id-401">'
    info = (meta.format(t="Information about our lifts") + '<script type="application/ld+json">'
            '{"@type":"AboutPage","name":"x"}</script><body>')
    for html, url in ((about, "https://www.akutt.no/om-akutt-kiropraktikk-oslo/"),
                      (info, "https://techano.no/information/")):
        assert wp.dated_items(wp.parse_page(html, url), today="2026-11-01") == []
    post = meta.format(t="Israelsmisjonen åpner nytt senter") + '<body class="single single-post postid-10422">'
    assert len(wp.dated_items(wp.parse_page(post, "https://www.israelsmisjonen.no/nytt-senter/"),
                              today="2026-11-01")) == 1


def test_site_name_suffix_in_a_post_title_does_not_count_as_naming_the_company():
    from signalpost import webparse as wp

    title = "– Det som var en prioritet for Paulus › Den Norske Israelsmisjon"
    site = "Den Norske Israelsmisjon › Evangeliet tilbake til det jødiske folk"
    assert wp.strip_site_suffix(title, site) == "– Det som var en prioritet for Paulus"
    assert wp.strip_site_suffix("Acme Bygg vinner anbud | Acme", "Acme") == "Acme Bygg vinner anbud"
    assert wp.strip_site_suffix("Ny avtale - mer vekst", "") == "Ny avtale - mer vekst"


def test_login_url_ignores_job_slugs_that_contain_a_login_word():
    for url in ("https://acme.no/stillinger/admin-assistent", "https://acme.no/ledige-stillinger/portal-manager",
                "https://acme.no/en/account-manager", "https://acme.no/jobs/sign-up-bonus"):
        assert not ic.login_url(url), url
    assert ic.login_url("https://login.acme.no/") and ic.login_url("https://acme.no/my-account")
    assert ic.login_url("https://acme.no/sign-in")


def test_site_suffix_with_several_parts_or_owner_name_is_stripped():
    from signalpost import webparse as wp

    assert wp.strip_site_suffix("Nytt kontor - Acme - Norge", "Acme - Norge") == "Nytt kontor"
    item = ic.assess_news(news("2026-09-01", "Valgresultat | Fjordlys Bygg"), OWNERS, "high")
    assert item is None
    kept = ic.assess_news(news("2026-09-01", "Fjordlys Bygg vinner anbud | Fjordlys"), OWNERS, "high")
    assert kept["confidence"] == "high"
