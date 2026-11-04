"""Fetch article comments from SB Nation's Cyclone GraphQL API (no browser needed)."""

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

from . import config
from .articles import Article, parse_date
from .http import get_session

_ROOT_QUERY = """
query ($communityID: String!, $friendlyID: String!, $url: String!) {
  comments(friendlyID: $friendlyID, communityID: $communityID, url: $url) { id replyCount }
}"""

_REPLIES_QUERY = """
query ($communityID: String!, $parentID: String!) {
  allReplies(communityID: $communityID, id: $parentID) {
    id parentID createdAt deletedAt modStatus type
    ... on QuickPost { body opener author { username displayNameOrUsername } }
  }
}"""

_HIDDEN_MOD_STATUSES = {"Rejected", "Removed", "Spam"}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CycloneError(RuntimeError):
    pass


@dataclass
class Comment:
    id: str
    parent_id: str | None
    author: str
    created_at: str
    text: str
    index: int = 0
    is_reply: bool = False


class _TextExtractor(HTMLParser):
    _BLOCK_TAGS = {"p", "br", "div", "li", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def _graphql(query: str, variables: dict) -> dict:
    resp = get_session().post(
        config.CYCLONE_API_URL,
        json={"query": query, "variables": variables},
        headers={"Origin": config.SITE_URL, "Referer": config.SITE_URL + "/"},
        timeout=config.REQUEST_TIMEOUT_SEC * 2,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise CycloneError(payload["errors"][0].get("message", "GraphQL error"))
    return payload.get("data") or {}


def fetch_comments(article: Article) -> tuple[list[Comment], int]:
    """
    Return (comments in chronological order, total comment count reported by the site).
    Comments are numbered 1..N by posting time, matching the site's "Oldest" sort.
    """
    if not article.friendly_id:
        return [], 0
    root = _graphql(_ROOT_QUERY, {
        "communityID": article.community_id,
        "friendlyID": article.friendly_id,
        "url": article.url,
    }).get("comments")
    if not root or not root.get("replyCount"):
        return [], 0

    nodes = _graphql(_REPLIES_QUERY, {
        "communityID": article.community_id,
        "parentID": root["id"],
    }).get("allReplies") or []

    comments = []
    for node in nodes:
        if node.get("type") != "QuickPost" or node.get("deletedAt"):
            continue
        if node.get("modStatus") in _HIDDEN_MOD_STATUSES:
            continue
        # GIF/image-only comments have no text but still count toward the comment numbering.
        text = "\n".join(filter(None, [html_to_text(node.get("opener") or ""), html_to_text(node.get("body") or "")]))
        author = node.get("author") or {}
        comments.append(Comment(
            id=node["id"],
            parent_id=node.get("parentID"),
            author=author.get("displayNameOrUsername") or author.get("username") or "Unknown",
            created_at=node.get("createdAt") or "",
            text=text,
            is_reply=bool(node.get("parentID")) and node.get("parentID") != root["id"],
        ))

    comments.sort(key=lambda c: parse_date(c.created_at) or _EPOCH)
    for i, c in enumerate(comments, start=1):
        c.index = i
    return [c for c in comments if c.text], len(comments)
