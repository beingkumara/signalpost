"""Dated posts from the RSS or Atom feed a verified company site advertises.

Most small Norwegian sites run WordPress, which advertises a post feed in the page head
(<link rel="alternate" type="application/rss+xml" href="/feed/">). One request returns the latest
posts with their publish dates, links and excerpts, all on the company's own site. Feed items go
through the same checks as any other dated post (dated_items, then assess_news): a real headline,
a past publish date, a link on the same site, and the company named in the headline or text.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from html import unescape

from signalpost.webparse import ARTICLE_EXCERPT_CHARS, Page, _join, same_site

MAX_FEED_ITEMS = 20
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")
# WordPress appends "The post X appeared first on <Site>." to every item: it names the site, not the article.
_WP_FOOTER = re.compile(r"\s*(The post\b.*?\bappeared first on\b|Innlegget\b.*?\bdukket først opp på\b).*$",
                        re.IGNORECASE | re.DOTALL)
# Placeholder titles page builders give unnamed pages ("Elementor #6565", "Uten tittel"): not news.
_PLACEHOLDER_TITLE = re.compile(r"^(elementor|draft|utkast|untitled|uten tittel|no title|page|side)\b[\s#\d-]*$",
                                re.IGNORECASE)
_COMMENT_FEED = re.compile(r"/comments?/|comments?[-_]?feed|[?&]feed=comments", re.IGNORECASE)


def feed_url(page: Page) -> str | None:
    """The first post feed the page advertises on its own site (comment feeds skipped), or None."""
    for href in (page.meta.get("_feeds") or "").split("\n"):
        url = _join(page.url, href.strip()) if href.strip() else None
        if url and same_site(url, page.url) and not _COMMENT_FEED.search(url):
            return url
    return None


def parse_feed(xml_text: str, url: str, today: str | None = None) -> Page:
    """A Page whose `articles` are the feed's dated items, ready for dated_items(). Never raises."""
    today = today or date.today().isoformat()
    empty = Page(url, "", {}, "", (), (), ())
    head = (xml_text or "")[:2000].upper()
    if not xml_text or "<!DOCTYPE" in head or "<!ENTITY" in (xml_text or "").upper():
        return empty  # no DTDs: blocks entity-expansion bombs
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return empty
    articles = []
    for item in [*root.iter("item"), *root.iter(f"{_ATOM}entry")][:MAX_FEED_ITEMS]:
        entry = _entry(item, url)
        if entry and entry[0] <= today:
            articles.append(entry)
    return Page(url, "", {}, "", (), (), (), tuple(articles))


def _entry(item: ET.Element, feed: str) -> tuple[str, str, str, str] | None:
    title = _text(item, "title")
    raw_link = (_text(item, "link") or _atom_link(item)).strip()
    link = _join(feed, raw_link) if raw_link else None
    stamp = _rss_date(_text(item, "pubDate")) or (_text(item, f"{_ATOM}published") or "")[:10]
    body = _text(item, "description") or _text(item, f"{_ATOM}summary") or _text(item, f"{_ATOM}content")
    if not (title and link and re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp or "")):
        return None
    text = _WP_FOOTER.sub("", unescape(_TAG.sub(" ", unescape(body))))
    excerpt = " ".join(text.split())[:ARTICLE_EXCERPT_CHARS]
    headline = " ".join(_TAG.sub(" ", unescape(title)).split())
    if _PLACEHOLDER_TITLE.match(headline):
        return None
    return stamp, headline, link, excerpt


def _text(item: ET.Element, tag: str) -> str:
    node = item.find(tag)  # an Element with no children is falsy, so compare with None explicitly
    if node is None and not tag.startswith("{"):
        node = item.find(f"{_ATOM}{tag}")
    return (node.text or "").strip() if node is not None else ""


def _atom_link(item: ET.Element) -> str:
    for node in item.findall(f"{_ATOM}link"):
        if node.get("rel", "alternate") == "alternate" and node.get("href"):
            return node.get("href", "")
    return ""


def _rss_date(value: str) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return None
