"""Fetch an article page and parse its title, date, body and comment-thread ids."""

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from . import config
from .crawler import article_id
from .http import get_session

_CORAL_ID_META = re.compile(r'coral-id"\s+content="([^"]+)"')
_CYCLONE_COMMUNITY = re.compile(r'cycloneCommunityId\\?"\s*:\s*\\?"([0-9a-f-]{36})')


@dataclass
class Article:
    url: str
    title: str
    published_at: datetime | None
    body: str
    friendly_id: str | None
    community_id: str

    @property
    def published_iso(self) -> str:
        return self.published_at.isoformat().replace("+00:00", "Z") if self.published_at else ""

    def is_recent(self, days: int = config.LOOKBACK_DAYS) -> bool:
        """Articles with an unknown date are treated as recent so they are not silently skipped."""
        if self.published_at is None:
            return True
        return self.published_at >= datetime.now(timezone.utc) - timedelta(days=days)


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _json_ld_article(soup: BeautifulSoup) -> dict:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if isinstance(item, dict) and item.get("@type") in ("NewsArticle", "Article", "BlogPosting"):
                return item
    return {}


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def parse_article(html: str, url: str) -> Article:
    soup = BeautifulSoup(html, "lxml")
    ld = _json_ld_article(soup)

    title = (ld.get("headline") or "").strip() or _text(soup.find("h1"))
    if not title:
        og = soup.find("meta", property="og:title")
        title = (og.get("content") or "").strip() if og else ""

    published = parse_date(ld.get("datePublished") or "")
    if published is None:
        time_el = soup.find("time", attrs={"datetime": True})
        published = parse_date(time_el["datetime"]) if time_el else None

    body = " ".join((ld.get("articleBody") or "").split())
    if not body:
        body = _text(soup.find("article"))

    m = _CORAL_ID_META.search(html)
    friendly_id = m.group(1) if m else None
    if not friendly_id and (aid := article_id(url)) is not None:
        friendly_id = base64.b64encode(f"sb34:post:{aid}".encode()).decode()

    m = _CYCLONE_COMMUNITY.search(html)
    community_id = m.group(1) if m else config.CYCLONE_COMMUNITY_ID

    return Article(url=url, title=title, published_at=published, body=body,
                   friendly_id=friendly_id, community_id=community_id)


def fetch_article(url: str) -> Article:
    resp = get_session().get(url, timeout=config.REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    return parse_article(resp.text, url)
