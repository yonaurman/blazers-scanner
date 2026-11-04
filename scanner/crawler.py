"""Discover article URLs from Blazer's Edge homepage and listings."""

import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.blazersedge.com/"
FEED_URL = "https://www.blazersedge.com/feed"
NBA_NEWS_URL = "https://www.blazersedge.com/nba-news-rumors"
GAME_THREADS_URL = "https://www.blazersedge.com/blazers-game-threads"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
REQUEST_DELAY_SEC = 1.0

# Article URLs contain a numeric ID segment, e.g. /trail-blazers-news/109834/...
ARTICLE_PATH_PATTERN = re.compile(r"/[a-z0-9-]+/\d+/[a-z0-9-]+")


def _is_article_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc and "blazersedge.com" not in parsed.netloc:
        return False
    return bool(ARTICLE_PATH_PATTERN.search(parsed.path or ""))


def _get_urls_from_feed(feed_text: str) -> set[str]:
    """
    Extract blazersedge.com article URLs from an RSS/Atom feed.
    RSS/Atom feeds are XML – they use <link>, <guid>, and <id> elements, NOT <a href>.
    This function scans the raw feed text for any matching article URL.
    """
    urls = set()
    # Match any blazersedge.com URL that looks like an article (has a numeric ID segment)
    pattern = re.compile(r'https?://(?:www\.)?blazersedge\.com(/[^\s<>"\'&#]+)')
    for match in pattern.finditer(feed_text):
        path = match.group(1).split("#")[0].rstrip("/")
        url = "https://www.blazersedge.com" + path
        if _is_article_url(url):
            urls.add(url)
    return urls


def get_article_urls_from_page(html: str, base: str = BASE_URL) -> set[str]:
    """Parse HTML and return set of Blazer's Edge article URLs (with numeric ID)."""
    soup = BeautifulSoup(html, "lxml")
    urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(base, href)
        parsed = urlparse(full)
        if "blazersedge.com" in (parsed.netloc or "") and _is_article_url(full):
            # Normalize: no fragment
            normalized = full.split("#")[0]
            urls.add(normalized)
    return urls


def crawl_article_urls(max_articles: int = 50) -> list[str]:
    """
    Fetch Blazer's Edge homepage and extract article links.
    Returns deduplicated list of article URLs, up to max_articles.
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    all_urls = set()
    pages_to_fetch = [BASE_URL, FEED_URL, NBA_NEWS_URL, GAME_THREADS_URL]

    for page_url in pages_to_fetch:
        try:
            resp = session.get(page_url, timeout=15)
            resp.raise_for_status()
            # RSS/Atom feeds are XML — use a dedicated feed parser instead of HTML link extraction
            if page_url == FEED_URL:
                urls = _get_urls_from_feed(resp.text)
            else:
                urls = get_article_urls_from_page(resp.text, page_url)
            all_urls.update(urls)
        except Exception:
            pass
        time.sleep(REQUEST_DELAY_SEC)

    result = list(all_urls)[:max_articles]
    return result
