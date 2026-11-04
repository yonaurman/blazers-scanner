"""Discover article URLs from Blazer's Edge listing pages and the RSS feed."""

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import config
from .http import get_session

logger = logging.getLogger(__name__)

LISTING_URLS = [
    f"{config.SITE_URL}/",
    f"{config.SITE_URL}/rss/index.xml",
    f"{config.SITE_URL}/trail-blazers-news",
    f"{config.SITE_URL}/trail-blazers-analysis",
    f"{config.SITE_URL}/nba-news-rumors",
    f"{config.SITE_URL}/gameday-coverage",
    f"{config.SITE_URL}/trail-blazers-discussion-game-threads",
]

# Article paths look like /trail-blazers-news/117719/ja-morant-injury-news
ARTICLE_PATH = re.compile(r"^/[a-z0-9-]+/(\d{5,7})/[a-z0-9-]+/?$")
FEED_URL_PATTERN = re.compile(r"https?://(?:www\.)?blazersedge\.com(/[^\s<>\"'#?]+)")


def article_id(url: str) -> int | None:
    m = ARTICLE_PATH.match(urlparse(url).path)
    return int(m.group(1)) if m else None


def _normalize(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc and not parsed.netloc.endswith("blazersedge.com"):
        return None
    if not ARTICLE_PATH.match(parsed.path):
        return None
    return config.SITE_URL + parsed.path.rstrip("/")


def _urls_from_html(html: str, base: str) -> set[str]:
    soup = BeautifulSoup(html, "lxml")
    urls = set()
    for a in soup.find_all("a", href=True):
        url = _normalize(urljoin(base, a["href"].strip()))
        if url:
            urls.add(url)
    return urls


def _urls_from_feed(text: str) -> set[str]:
    urls = set()
    for m in FEED_URL_PATTERN.finditer(text):
        url = _normalize(config.SITE_URL + m.group(1))
        if url:
            urls.add(url)
    return urls


def crawl_article_urls(max_articles: int = config.MAX_ARTICLES) -> list[str]:
    """Return up to max_articles article URLs, newest first (article ids increase over time)."""
    session = get_session()
    found: set[str] = set()
    for page_url in LISTING_URLS:
        try:
            resp = session.get(page_url, timeout=config.REQUEST_TIMEOUT_SEC)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Listing %s failed: %s", page_url, e)
            continue
        urls = _urls_from_feed(resp.text) if page_url.endswith(".xml") else _urls_from_html(resp.text, page_url)
        logger.info("Listing %s: %d article links", page_url, len(urls))
        found |= urls

    # The same article can appear under several slugs/sections; keep one URL per id.
    by_id: dict[int, str] = {}
    for url in found:
        aid = article_id(url)
        if aid is not None:
            by_id.setdefault(aid, url)
    newest_first = [by_id[aid] for aid in sorted(by_id, reverse=True)]
    return newest_first[:max_articles]
