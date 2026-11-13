"""Dated posts from the RSS/Atom feed a verified company site advertises."""

from signalpost.feeds import feed_url, parse_feed
from signalpost.item_checks import assess_news
from signalpost.webparse import dated_items, parse_page

HOME = """<html><head><title>Acme Bygg</title>
<link rel="alternate" type="application/rss+xml" title="Acme &raquo; Feed" href="/feed/">
<link rel="alternate" type="application/rss+xml" title="Acme &raquo; Comments" href="/comments/feed/">
</head><body>Hei</body></html>"""

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Acme Bygg</title><link>https://acmebygg.no</link>
<item><title>Acme Bygg vinner kontrakt på ny skole</title><link>https://acmebygg.no/2026/10/ny-skole/</link>
<pubDate>Tue, 20 Oct 2026 08:00:00 +0000</pubDate><description><![CDATA[<p>Acme Bygg skal bygge ny skole.</p>]]></description></item>
<item><title>Sommerfest for alle ansatte i år</title><link>https://acmebygg.no/2026/06/sommerfest/</link>
<pubDate>Mon, 01 Jun 2026 08:00:00 +0000</pubDate><description>Hyggelig kveld.</description></item>
<item><title>Lenke til en annen nettside her</title><link>https://other-site.no/post/</link>
<pubDate>Mon, 01 Jun 2026 08:00:00 +0000</pubDate></item>
<item><title>Planlagt innlegg i fremtiden</title><link>https://acmebygg.no/2099/01/x/</link>
<pubDate>Thu, 01 Jan 2099 08:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Acme</title>
<entry><title>Acme Bygg åpner nytt kontor i Drammen</title>
<link rel="alternate" href="https://acmebygg.no/nyheter/nytt-kontor"/>
<published>2026-09-02T10:00:00Z</published><updated>2026-11-01T10:00:00Z</updated>
<summary>Vi flytter.</summary></entry></feed>"""


def test_feed_url_is_the_first_same_site_post_feed():
    page = parse_page(HOME, "https://acmebygg.no/")
    assert feed_url(page) == "https://acmebygg.no/feed/"


def test_feed_url_ignores_other_sites_and_missing_links():
    page = parse_page(HOME.replace('href="/feed/"', 'href="https://feeds.example.com/acme"'), "https://acmebygg.no/")
    assert feed_url(page) is None  # the comments feed is skipped too
    assert feed_url(parse_page("<html><head></head></html>", "https://acmebygg.no/")) is None


def test_rss_items_become_dated_posts_on_the_same_site():
    page = parse_feed(RSS, "https://acmebygg.no/feed/", today="2026-11-13")
    items = dated_items(page, today="2026-11-13")
    titles = [i["title"] for i in items]
    assert titles == ["Acme Bygg vinner kontrakt på ny skole", "Sommerfest for alle ansatte i år"]
    assert items[0]["date"] == "2026-10-20" and items[0]["url"] == "https://acmebygg.no/2026/10/ny-skole/"
    assert "bygge ny skole" in items[0]["excerpt"] and "<p>" not in items[0]["excerpt"]


def test_feed_items_still_have_to_name_the_company():
    items = dated_items(parse_feed(RSS, "https://acmebygg.no/feed/", today="2026-11-13"), today="2026-11-13")
    owners = ("ACME BYGG AS", "https://acmebygg.no/")
    kept = [assess_news(i, owners, "high") for i in items]
    assert kept[0] and kept[0]["confidence"] == "high"
    assert kept[1] is None  # 'Sommerfest' does not name the company: can't tell


def test_atom_uses_published_not_updated():
    items = dated_items(parse_feed(ATOM, "https://acmebygg.no/feed.xml", today="2026-11-13"), today="2026-11-13")
    assert [(i["date"], i["url"]) for i in items] == [("2026-09-02", "https://acmebygg.no/nyheter/nytt-kontor")]


def test_hostile_or_broken_feeds_yield_nothing():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    assert parse_feed(bomb, "https://acmebygg.no/feed/").articles == ()
    assert parse_feed("<rss><channel><item>", "https://acmebygg.no/feed/").articles == ()
    assert parse_feed("", "https://acmebygg.no/feed/").articles == ()


def _one(title: str, link: str, body: str = "", feed: str = "https://dahl.no/feed/") -> list:
    rss = (f"<rss><channel><item><title>{title}</title><link>{link}</link>"
           f"<pubDate>Tue, 20 Oct 2026 08:00:00 +0000</pubDate><description>{body}</description></item></channel></rss>")
    return dated_items(parse_feed(rss, feed, today="2026-11-13"), today="2026-11-13")


def test_wordpress_feed_footer_does_not_count_as_naming_the_company():
    owners = ("DAHL AS", "https://dahl.no/")
    for footer in ("The post Sommerfest appeared first on Dahl AS.",
                   "Innlegget Sommerfest dukket først opp på Dahl AS."):
        items = _one("Sommerfest for alle ansatte", "https://dahl.no/2026/10/sommerfest/", f"Hyggelig kveld. {footer}")
        assert items and assess_news(items[0], owners, "high") is None
        assert "Dahl" not in items[0]["excerpt"]


def test_spoofed_or_non_web_links_are_not_same_site():
    assert _one("Dahl AS vinner kontrakt", "http://dahl.no:pw@evil.com/2026/10/a/") == []
    assert _one("Dahl AS vinner kontrakt", "javascript://dahl.no/%0aalert(1)") == []


def test_relative_atom_link_is_joined_to_the_feed():
    atom = ATOM.replace("https://acmebygg.no/nyheter/nytt-kontor", "/nyheter/nytt-kontor")
    items = dated_items(parse_feed(atom, "https://acmebygg.no/feed.xml", today="2026-11-13"), today="2026-11-13")
    assert items[0]["url"] == "https://acmebygg.no/nyheter/nytt-kontor"


def test_page_builder_placeholder_titles_are_not_news():
    for title in ("Elementor #6565", "Uten tittel", "Untitled"):
        assert _one(title, "https://dahl.no/2026/10/x/", "Dahl AS") == []
