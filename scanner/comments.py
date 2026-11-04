"""Load article pages with Playwright and extract comments that match keywords."""

import base64
import re
import time
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlencode

import requests as _requests

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Max times to click "Load more" / "Show more" until no new comments appear (e.g. 98 comments need many clicks)
LOAD_MORE_MAX_ITERATIONS = 80
LOAD_MORE_WAIT_SEC = 3.5
# Additional Coral sort orders to cycle through after the default "Newest" pass.
# High-volume articles (100+ comments) cap at ~23 rendered in any single sort view;
# cycling exposes different subsets of the full comment list.
CORAL_EXTRA_SORT_VALUES = ["CREATED_AT_ASC", "REPLIES_DESC", "REACTION_DESC"]
# Min extra length to treat long text as "wrapper" (author + body + Rec/Reply/Share) duplicate
MIN_WRAPPER_EXTRA_LEN = 25
# Max ratio of long/short text to still consider it a wrapper.
# If the "long" text is more than 3x the "short" text, it's a parent container that includes
# nested reply text — NOT a simple wrapper. Removing it would drop valid keyword mentions.
MAX_WRAPPER_RATIO = 3.0

KEYWORDS = ["deni", "Deni", "avdija", "Avdija", "Deni Avdija"]
COMMENT_WAIT_TIMEOUT_MS = 30000
COMMENT_LOAD_EXTRA_SEC = 30  # Max seconds to poll for comment elements before giving up
PAGE_DELAY_SEC = 2

# Coral GraphQL API constants.
# SBNation's Coral instance at sbnation.coral.coralproject.net uses persisted queries.
# Story IDs are base64(sb34:post:{article_id}) — the "sb34" prefix is the Blazers Edge
# site token assigned by SBNation's Coral instance.
_CORAL_API_URL = "https://sbnation.coral.coralproject.net/api/graphql"
_CORAL_STORY_PREFIX = "sb34:post:"
_CORAL_COMMENTS_QUERY_ID = "9c28aa927a6d4ee29ff476337d9563b4"
_CORAL_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://www.blazersedge.com",
    "Referer": "https://www.blazersedge.com/",
}


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML → plain text converter for Coral comment body fields."""
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("br", "p", "div", "li", "blockquote"):
            self._parts.append("\n")

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.get_text()


def _derive_coral_story_id(article_url: str) -> str | None:
    """
    Derive the Coral story ID from a Blazers Edge article URL.
    Blazers Edge article IDs appear as 5–7 digit numbers in the URL path.
    The Coral story ID is base64(sb34:post:{article_id}).
    """
    m = re.search(r"/(\d{5,7})/", article_url)
    if not m:
        return None
    article_id = m.group(1)
    raw = f"{_CORAL_STORY_PREFIX}{article_id}"
    return base64.b64encode(raw.encode()).decode()


def _fetch_all_coral_comments_via_api(
    story_id: str,
    story_url: str,
    order_by: str = "CREATED_AT_ASC",
) -> list[dict]:
    """
    Fetch ALL comments for a Coral story via the public GraphQL API.
    Returns a flat list of dicts with 'author' and 'comment_text' keys,
    including both top-level comments and their replies.
    Falls back to empty list on any error.

    Coral paginates with cursor-based `after` parameter when hasNextPage is True.
    For most articles, all top-level comments fit in one page; replies are nested.
    """
    all_comments: list[dict] = []
    after: str | None = None

    while True:
        variables: dict = {
            "storyID": story_id,
            "storyURL": story_url,
            "commentsOrderBy": order_by,
            "flattenReplies": False,
            "commentID": None,
        }
        if after:
            variables["after"] = after

        params = {
            "query": "",
            "id": _CORAL_COMMENTS_QUERY_ID,
            "variables": __import__("json").dumps(variables, separators=(",", ":")),
        }
        try:
            resp = _requests.get(
                _CORAL_API_URL + "?" + urlencode(params),
                headers=_CORAL_API_HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            break

        story = data.get("data", {}).get("story") or {}
        comments_page = story.get("comments") or {}
        edges = comments_page.get("edges") or []
        page_info = comments_page.get("pageInfo") or {}

        def _collect(node: dict) -> None:
            if not isinstance(node, dict):
                return
            if node.get("deleted"):
                return
            author_obj = node.get("author") or {}
            username = author_obj.get("username", "") if isinstance(author_obj, dict) else ""
            body_text = _html_to_text(node.get("body") or "")
            if username and body_text:
                all_comments.append({"author": username, "comment_text": body_text})
            # Recurse into direct replies
            for reply_edge in (node.get("replies") or {}).get("edges") or []:
                _collect(reply_edge.get("node") or {})

        for edge in edges:
            _collect(edge.get("node") or {})

        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break

    return all_comments


def _find_matched_keywords(text: str, keywords: list[str]) -> list[str]:
    if not text:
        return []
    t = text.lower()
    seen = set()
    result = []
    for kw in keywords:
        if kw.lower() in t and kw.lower() not in seen:
            seen.add(kw.lower())
            result.append(kw)
    return result


# Substrings that indicate the text is Coral/site UI or the whole comment stream, not a single comment
_COMMENT_STREAM_CHROME_MARKERS = (
    "Be respectful in your interactions",
    "community guidelines",
    "Go to the My Profile tab",
    "Z key jumps to Next Unread",
    "shift-C scrolls backwards",
    "Sign in and Join the Conversation",
    "All Comments ( ",
    "Sort by Newest",
    "Top of comments",
    "support@sbnation.com",
    "moderation@sbnation.com",
    "person viewing this discussion",
)


def _is_comment_stream_or_chrome(text: str) -> bool:
    """True if text looks like the full comment stream or Coral UI chrome, not a single comment."""
    if not text or len(text) < 100:
        return False
    t = text.strip()
    if len(t) > 20000:
        return True
    # Require at least 2 chrome markers — a single marker can legitimately appear in a long comment
    marker_hits = sum(1 for m in _COMMENT_STREAM_CHROME_MARKERS if m in t)
    return marker_hits >= 2


def _filter_chrome_and_stream_blobs(comments: list[dict]) -> list[dict]:
    """Drop entries that are the entire comment stream or UI chrome."""
    if not comments:
        return comments
    return [c for c in comments if not _is_comment_stream_or_chrome(c.get("comment_text") or "")]


def _normalize_ws(text: str) -> str:
    """Normalize Unicode whitespace and quote/apostrophe variants for comparison."""
    import unicodedata
    # Normalize Unicode (NFC), then fold curly quotes/apostrophes to ASCII equivalents
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u2018", "'").replace("\u2019", "'")   # curly single quotes
    text = text.replace("\u201c", '"').replace("\u201d", '"')   # curly double quotes
    text = text.replace("\u2013", "-").replace("\u2014", "-")   # en/em dashes
    text = text.replace("\xa0", " ")                            # non-breaking space
    return " ".join(text.split())


def _deduplicate_comment_bodies(comments: list[dict]) -> list[dict]:
    """
    Remove duplicates where one comment is the full block (author, timestamp, body, Rec/Reply/Share)
    and another is just the body. Keep the body-only version. Preserves input order so comment numbers match the page.
    Uses a two-pass approach so the result is order-independent (wrapper-before-body and body-before-wrapper both handled correctly).

    Normalizes whitespace (including \\xa0 non-breaking spaces) before substring check so that
    browser-extracted text variants don't cause deduplication misses.
    """
    if not comments:
        return []
    texts = [c["comment_text"] for c in comments]
    norm_texts = [_normalize_ws(t) for t in texts]
    # Mark each index as a "wrapper" if another (shorter) comment's text is a substring of it,
    # BUT only if the longer text is ≤ MAX_WRAPPER_RATIO times the shorter text.
    # If it's much longer (e.g. parent container that includes nested reply text), keep it —
    # removing it would silently drop keyword mentions that appear in the parent comment only.
    is_wrapper = [False] * len(texts)
    for i, long_norm in enumerate(norm_texts):
        for j, short_norm in enumerate(norm_texts):
            if i == j:
                continue
            # Use raw lengths for the size guard — normalized lengths collapse whitespace
            # (e.g. \n\n\n\n → " ") and can make the "extra chars" disappear, causing
            # near-identical duplicates to be skipped. Raw length preserves the real diff.
            raw_long_len = len(texts[i])
            raw_short_len = len(texts[j])
            if raw_long_len <= raw_short_len + MIN_WRAPPER_EXTRA_LEN:
                continue
            if raw_long_len > raw_short_len * MAX_WRAPPER_RATIO:
                continue  # Too much extra content — it's a container, not a simple wrapper
            # Check for wrapper using two strategies:
            # 1. Exact normalized substring (works for most comments)
            # 2. Space-stripped comparison (handles "passes.Pregame" vs "passes. Pregame"
            #    caused by different <p>/<div> rendering in different DOM extractions)
            # For long comments, only compare the first 120 chars to avoid false negatives
            # from rendering differences in the middle of the text.
            def _is_contained(short: str, long: str) -> bool:
                if len(short) <= 300:
                    if short in long:
                        return True
                    # Fallback: strip all spaces and compare
                    return short.replace(" ", "") in long.replace(" ", "")
                else:
                    prefix = short[:120]
                    if prefix in long:
                        return True
                    return prefix.replace(" ", "") in long.replace(" ", "")

            if not _is_contained(short_norm, long_norm):
                continue
            is_wrapper[i] = True
            break
    return [c for c, w in zip(comments, is_wrapper) if not w]


def _make_comment_extraction_js() -> str:
    """
    Return the shared JS used to extract comments from a document (main page or iframe).
    Works in both the main page and inside Coral embed iframes:
    - When window !== window.top (inside an iframe) the whole document is the comment stream,
      so we skip the '#coral / .coral-stream' container check and sweep everything.
    - Also extracts via data-testid attributes used by modern React-based Coral builds.
    - Clones parent .coral-comment elements before reading body text so nested reply text
      is excluded from the parent body, preventing mis-classification by deduplication.
    """
    return r"""
    (function() {
        var comments = [];
        var seen = {};
        // Detect if we are running inside an embedded iframe (e.g. the Coral comment iframe).
        // In that case the entire document IS the comment stream — no '#coral' ancestor exists.
        var IS_IFRAME = (function() {
            try { return window !== window.top; } catch(e) { return true; }
        })();
        function addComment(author, body) {
            if (!body || body.length < 15) return;
            var key = body.substring(0, 120);
            if (seen[key]) return;
            seen[key] = true;
            comments.push({ author: author || 'Unknown', comment_text: body });
        }
        function getAuthor(el) {
            if (!el) return '';
            var authorEl = el.querySelector(
                '[class*="username"], [class*="author"], [class*="Author"],' +
                '[data-testid*="author"], [data-testid*="username"]'
            ) || el.querySelector('a[href*="user"], a[href*="profile"]');
            return authorEl ? (authorEl.innerText || authorEl.textContent || '').trim() : '';
        }
        function processEl(el) {
            var text = (el.innerText || el.textContent || '').trim();
            if (text.length < 10) return;
            var author = '';
            var body = '';
            var cls = el.getAttribute ? (el.getAttribute('class') || '') : '';
            var clsLower = cls.toLowerCase();
            if (el.classList && el.classList.contains('coral-comment')) {
                author = getAuthor(el);
                var bodyEl = el.querySelector('[class*="body"], [class*="Body"], [class*="content"]') || el;
                // Clone and strip nested comment/reply elements so parent body text
                // does not include reply content (which would trigger false dedup removal).
                var cloned = bodyEl.cloneNode(true);
                var nested = cloned.querySelectorAll
                    ? cloned.querySelectorAll('.coral-comment, [class*="coral-reply"], [data-testid*="comment"]')
                    : [];
                for (var k = 0; k < nested.length; k++) {
                    try { nested[k].parentNode && nested[k].parentNode.removeChild(nested[k]); } catch(e2) {}
                }
                body = (cloned.innerText || cloned.textContent || '').trim();
            } else if (el.classList && el.classList.contains('coral-indent') || clsLower.indexOf('coral-indent') !== -1) {
                body = text;
                var prev = el.previousElementSibling || el.parentElement;
                if (prev && prev.querySelector) author = getAuthor(prev);
            } else if (clsLower.indexOf('coral-reply') !== -1 || clsLower.indexOf('coral-comment') !== -1) {
                author = getAuthor(el);
                body = text;
            }
            if (body.length >= 10) addComment(author, body);
        }
        function sweepCoralCommentLike(root) {
            if (!root) return;
            try {
                var container;
                if (IS_IFRAME) {
                    // We ARE inside the Coral embed iframe — treat the whole document as the stream.
                    container = root.body || root.documentElement || root;
                } else {
                    container = root.querySelector
                        ? root.querySelector('#coral, .coral-stream, [data-coral-stream], [data-coral]')
                        : null;
                }
                if (!container) return;
                // ── Modern Coral (React, hashed class names): use data-testid ──
                var byTestId = container.querySelectorAll
                    ? container.querySelectorAll('[data-testid*="comment"], [data-testid*="Comment"], [role="article"]')
                    : [];
                for (var ti = 0; ti < byTestId.length; ti++) {
                    var tel = byTestId[ti];
                    var ttext = (tel.innerText || tel.textContent || '').trim();
                    if (ttext.length < 20 || ttext.length > 15000) continue;
                    // Skip containers that wrap other comment elements (only want leaf comments).
                    var innerComments = tel.querySelectorAll('[data-testid*="comment"], [role="article"]');
                    if (innerComments.length > 0) continue;
                    var tauthor = getAuthor(tel) || getAuthor(tel.parentElement);
                    addComment(tauthor, ttext);
                }
                // ── Classic Coral (class*="coral") ──
                var nodes = container.querySelectorAll ? container.querySelectorAll('[class*="coral"]') : [];
                for (var i = 0; i < nodes.length; i++) {
                    var el = nodes[i];
                    var text = (el.innerText || el.textContent || '').trim();
                    if (text.length < 50 || text.length > 15000) continue;
                    var hasUser = el.querySelector('a[href*="user"], a[href*="profile"], [class*="username"], [class*="author"]');
                    if (!hasUser && el.parentElement) hasUser = el.parentElement.querySelector('a[href*="user"], [class*="username"], [class*="author"]');
                    if (!hasUser) continue;
                    var children = el.querySelectorAll('[class*="coral"]');
                    var childWithContent = false;
                    for (var j = 0; j < children.length; j++) {
                        if (children[j] === el) continue;
                        var ct = (children[j].innerText || children[j].textContent || '').trim();
                        if (ct.length >= 50) { childWithContent = true; break; }
                    }
                    if (childWithContent) continue;
                    var author = getAuthor(el) || getAuthor(el.parentElement);
                    addComment(author, text);
                }
            } catch (e) {}
        }
        function walk(root) {
            if (!root) return;
            try {
                var sel = '.coral-comment, .coral-indent, [class*="coral-reply"], [class*="coral-comment"]';
                var inOrder = root.querySelectorAll ? root.querySelectorAll(sel) : [];
                inOrder.forEach(processEl);
                sweepCoralCommentLike(root);
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                all.forEach(function(el) {
                    if (el.shadowRoot) walk(el.shadowRoot);
                });
            } catch (e) {}
        }
        walk(document);
        sweepCoralCommentLike(document);
        return comments;
    })();
    """


def _extract_comments_via_shadow_dom(page) -> list[dict]:
    """
    Coral embeds use Shadow DOM. Collect .coral-comment, .coral-indent, and any [class*="coral-reply"]
    in document order so comment numbers match the page (top-level and replies interleaved).
    Also sweep for any comment-like block inside coral (50-5000 chars + author link) in case structure varies.
    """
    try:
        raw = page.evaluate(_make_comment_extraction_js())
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _extract_comments_via_selectors(page) -> list[dict]:
    """Fallback: extract using selectors that pierce shadow DOM (Playwright pierces by default)."""
    comments = []
    selectors = [
        ".coral-comment",
        "#coral .coral-comment",
        ".coral-stream .coral-comment",
        '[class*="Comment"] [class*="body"]',
        '[class*="comment"] [class*="body"]',
        '[data-testid="comment"]',
        'article[class*="comment"]',
    ]
    seen = set()
    for sel in selectors:
        try:
            els = page.query_selector_all(sel)
            for el in els:
                text = (el.inner_text() or "").strip()
                if not text or len(text) < 10 or text in seen:
                    continue
                author = "Unknown"
                try:
                    author_el = el.query_selector("[class*='username'], [class*='author'], a[href*='user']")
                    if author_el:
                        author = (author_el.inner_text() or "").strip() or author
                except Exception:
                    pass
                comments.append({"author": author, "comment_text": text})
                seen.add(text)
        except Exception:
            continue
    return comments


def _extract_comments_from_frame(frame) -> list[dict]:
    """Run shadow-DOM extraction in a given frame (including Coral embed iframes)."""
    try:
        raw = frame.evaluate(_make_comment_extraction_js())
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _extract_comments_via_full_page_keyword_search(page, keywords: list[str]) -> list[dict]:
    """
    Fallback: inside the comment/Coral area only, find elements whose text contains a keyword.
    Must be inside #coral, .coral-stream, or [data-coral] so we never pick up sidebar/related (e.g. "More in Blazers News").
    """
    keywords_lower = [k.lower() for k in keywords]
    js = r"""
    (keywords) => {
        var results = [];
        var seen = {};
        // When running inside the Coral embed iframe, the whole document is the comment stream.
        // Skip the '#coral / .coral-stream' ancestor check so keyword matches aren't filtered out.
        var IS_IFRAME = (function() {
            try { return window !== window.top; } catch(e) { return true; }
        })();
        if (!keywords || !Array.isArray(keywords)) return results;
        function walk(root) {
            if (!root) return;
            try {
                var nodes = root.querySelectorAll ? root.querySelectorAll('p, div[class], span[class], section, article') : [];
                nodes.forEach(function(el) {
                    var text = (el.innerText || el.textContent || '').trim();
                    if (text.length < 20 || text.length > 15000) return;
                    if (el.closest && (el.closest('.c-entry-content') || el.closest('article [itemprop="articleBody"]'))) return;
                    // On the main page, restrict to the Coral container to avoid article body hits.
                    // Inside the Coral iframe itself, skip this check — we're already in the stream.
                    if (!IS_IFRAME && el.closest && !el.closest('#coral, .coral-stream, [data-coral-stream], [data-coral], [id*="coral"]')) return;
                    var hasKeyword = keywords.some(function(kw) { return text.toLowerCase().indexOf(kw) !== -1; });
                    if (!hasKeyword) return;
                    var key = text.substring(0, 150);
                    if (seen[key]) return;
                    seen[key] = true;
                    var author = 'Unknown';
                    var parent = el.closest
                        ? el.closest('[class*="comment"], [class*="Comment"], [id*="comment"], [data-coral], [data-testid*="comment"]')
                        : el.parentElement;
                    if (parent && parent.querySelector) {
                        var authorEl = parent.querySelector(
                            '[class*="username"], [class*="author"], [class*="Author"],' +
                            'a[href*="user"], a[href*="profile"],' +
                            '[data-testid*="author"], [data-testid*="username"]'
                        );
                        if (authorEl) author = (authorEl.innerText || authorEl.textContent || '').trim();
                    }
                    results.push({ author: author || 'Unknown', comment_text: text });
                });
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                all.forEach(function(el) {
                    if (el.shadowRoot) walk(el.shadowRoot);
                });
            } catch (e) {}
        }
        walk(document);
        return results;
    }
    """
    try:
        raw = page.evaluate(js, keywords_lower)
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _click_load_more_in_shadow_dom(page) -> bool:
    """
    Click 'Load more' / 'Show more' / 'View replies' / 'Load more replies' etc. inside Shadow DOM.
    Scrolls each button into view before clicking. Clicks all such buttons in one pass.
    Returns True if at least one click was performed.
    """
    click_js = r"""
    (function() {
        var clicked = 0;
        function isExpandButton(t) {
            // t is already lowercased and trimmed.
            // Match only genuine "expand" controls: load-more, view-replies, show-comment, etc.
            // Use word-boundary-safe checks to avoid false matches inside usernames
            // (e.g. "philthebballplayer" contains "all", "more" may appear in random text).
            if (/\bload more\b/.test(t)) return true;
            if (/\bshow more\b/.test(t)) return true;
            if (/\bview more\b/.test(t)) return true;
            if (/\bmore repl(y|ies)\b/.test(t)) return true;
            if (/\bmore comments?\b/.test(t)) return true;
            if (/\bview repl(y|ies)\b/.test(t)) return true;
            if (/\bshow repl(y|ies)\b/.test(t)) return true;
            if (/\bsee repl(y|ies)\b/.test(t)) return true;
            if (/\bload repl(y|ies)\b/.test(t)) return true;
            if (/\bexpand repl(y|ies)\b/.test(t)) return true;
            // "N replies" at start (collapsed reply expander), e.g. "3 replies"
            if (/^\d+\s+repl(y|ies)\b/.test(t)) return true;
            // "show comment by X and its replies" — Coral collapsed-comment toggle
            if (/\bshow comment\b/.test(t) && /\brepl(y|ies)\b/.test(t)) return true;
            return false;
        }
        function findAndClickAll(root) {
            if (!root) return;
            try {
                var buttons = root.querySelectorAll ? root.querySelectorAll('button, a, [role="button"], [class*="loadMore"], [class*="LoadMore"]') : [];
                for (var i = 0; i < buttons.length; i++) {
                    var b = buttons[i];
                    var t = ((b.innerText || b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase().trim();
                    if (t.length > 200) continue;
                    if (isExpandButton(t)) {
                        try { b.scrollIntoView({ behavior: 'instant', block: 'end' }); } catch (e) {}
                        b.click();
                        clicked++;
                    }
                }
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var j = 0; j < all.length; j++) {
                    if (all[j].shadowRoot) findAndClickAll(all[j].shadowRoot);
                }
            } catch (e) {}
        }
        findAndClickAll(document);
        return clicked > 0;
    })();
    """
    try:
        return bool(page.evaluate(click_js))
    except Exception:
        return False


def _is_coral_or_content_frame(frame, page) -> bool:
    """Return True if this frame is safe to click inside (main frame or Coral-related)."""
    if frame == page.main_frame:
        return True
    url = frame.url or ""
    # Allow blank frames (about:blank) and coral-related frames; block all ad/tracking frames.
    # IMPORTANT: check the hostname only — some ad frames (e.g. doubleclick.net) embed
    # blazersedge.com as a query-parameter value, which would incorrectly pass a string-contains check.
    if not url or url == "about:blank":
        return True
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return False
    return "coral" in netloc or "blazersedge.com" in netloc


def _click_load_more_in_all_frames(page) -> bool:
    """
    Run 'Load more' / 'View replies' click in the main page shadow DOM and Coral-related frames.
    Skips ad/tracking iframes — clicking buttons there can navigate the page and wipe the shadow DOM.
    The main-page shadow-DOM click already pierces #coral-shadow-container's shadow root.
    Returns True if at least one click was performed.
    """
    any_clicked = _click_load_more_in_shadow_dom(page)
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        if not _is_coral_or_content_frame(frame, page):
            continue
        try:
            click_js = r"""
            (function() {
                var clicked = 0;
                function isExpandButton(t) {
                    if (/\bload more\b/.test(t)) return true;
                    if (/\bshow more\b/.test(t)) return true;
                    if (/\bview more\b/.test(t)) return true;
                    if (/\bmore repl(y|ies)\b/.test(t)) return true;
                    if (/\bmore comments?\b/.test(t)) return true;
                    if (/\bview repl(y|ies)\b/.test(t)) return true;
                    if (/\bshow repl(y|ies)\b/.test(t)) return true;
                    if (/\bsee repl(y|ies)\b/.test(t)) return true;
                    if (/\bload repl(y|ies)\b/.test(t)) return true;
                    if (/\bexpand repl(y|ies)\b/.test(t)) return true;
                    if (/^\d+\s+repl(y|ies)\b/.test(t)) return true;
                    if (/\bshow comment\b/.test(t) && /\brepl(y|ies)\b/.test(t)) return true;
                    return false;
                }
                function findAndClickAll(root) {
                    if (!root) return;
                    try {
                        var buttons = root.querySelectorAll('button, a, [role="button"], [class*="loadMore"], [class*="LoadMore"]');
                        for (var i = 0; i < buttons.length; i++) {
                            var b = buttons[i];
                            var t = ((b.innerText || b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase().trim();
                            if (t.length > 200) continue;
                            if (isExpandButton(t)) {
                                try { b.scrollIntoView({ behavior: 'instant', block: 'end' }); } catch (e) {}
                                b.click();
                                clicked++;
                            }
                        }
                        var all = root.querySelectorAll('*');
                        for (var j = 0; j < all.length; j++) {
                            if (all[j].shadowRoot) findAndClickAll(all[j].shadowRoot);
                        }
                    } catch (e) {}
                }
                findAndClickAll(document);
                return clicked > 0;
            })();
            """
            if frame.evaluate(click_js):
                any_clicked = True
        except Exception:
            pass
    return any_clicked


def _get_total_comment_count_from_page(page) -> Optional[int]:
    """
    Try to read the total comment count (e.g. '98 Comments') from the page.
    Collects all candidate numbers (from patterns like "N comment(s)", "Comments (N)") and returns
    the maximum so we get the real total (98) not a smaller number (e.g. "Load 20 more").
    """
    get_total_js = r"""
    (function() {
        var candidates = [];
        function collect(root) {
            if (!root) return;
            try {
                var text = (root.innerText || root.textContent || '').trim();
                var re = /\b(\d{1,5})\s*comment(s?)\b/gi;
                var m;
                while ((m = re.exec(text)) !== null) candidates.push(parseInt(m[1], 10));
                re = /comment(s?)\s*[\(\s](\d{1,5})\)?/gi;
                while ((m = re.exec(text)) !== null) candidates.push(parseInt(m[2], 10));
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var i = 0; i < all.length; i++) {
                    if (all[i].shadowRoot) collect(all[i].shadowRoot);
                }
            } catch (e) {}
        }
        collect(document);
        if (candidates.length === 0) return null;
        var max = Math.max.apply(null, candidates);
        return max > 0 ? max : null;
    })();
    """
    try:
        raw = page.evaluate(get_total_js)
        if isinstance(raw, int) and raw > 0:
            return raw
        return None
    except Exception:
        return None


def _click_all_comments_tab(page) -> bool:
    """
    Click Coral's 'All Comments (N)' tab so we see ALL chronological comments,
    not just the default 'Top Comments' (best-rec'd) subset.
    Returns True if clicked.
    """
    js = r"""
    (function() {
        var clicked = false;
        function findAll(root) {
            if (!root) return;
            try {
                var buttons = root.querySelectorAll ? root.querySelectorAll('button, a, [role="button"]') : [];
                for (var i = 0; i < buttons.length; i++) {
                    var b = buttons[i];
                    var t = ((b.innerText || b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).toLowerCase().trim();
                    if (/\ball comments?\b/.test(t) && /\d/.test(t)) {
                        b.click();
                        clicked = true;
                    }
                }
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var j = 0; j < all.length; j++) {
                    if (all[j].shadowRoot) findAll(all[j].shadowRoot);
                }
            } catch (e) {}
        }
        findAll(document);
        return clicked;
    })();
    """
    try:
        return bool(page.evaluate(js))
    except Exception:
        return False


def _scroll_coral_shadow_dom(page) -> bool:
    """
    Scroll the Coral shadow DOM's internal scroll container to the bottom.
    Modern Coral uses an intersection observer inside the shadow DOM to lazy-load
    comments as the user scrolls — this triggers that mechanism directly.
    Returns True if a scrollable element was found and scrolled.
    """
    js = r"""
    (function() {
        var host = document.getElementById('coral-shadow-container');
        if (!host || !host.shadowRoot) return false;
        var sr = host.shadowRoot;
        // .coral-allComments or .coral-stream are the inner scroll containers
        var containers = ['coral-allComments', 'coral-stream', 'coral-comments'];
        for (var ci = 0; ci < containers.length; ci++) {
            var el = sr.querySelector('.' + containers[ci]);
            if (el && el.scrollHeight > el.clientHeight) {
                el.scrollTop = el.scrollHeight;
                return true;
            }
        }
        // Fallback: find any element with meaningful scroll height inside shadow DOM
        var all = sr.querySelectorAll('*');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (el.scrollHeight > el.clientHeight + 100) {
                el.scrollTop = el.scrollHeight;
                return true;
            }
        }
        return false;
    })()
    """
    try:
        return bool(page.evaluate(js))
    except Exception:
        return False


def _change_coral_sort(page, sort_value: str) -> bool:
    """
    Change Coral's comment sort order via the shadow DOM <select> element.
    sort_value: Coral internal value string, e.g. 'CREATED_AT_ASC', 'REPLIES_DESC',
                'REACTION_DESC', 'CREATED_AT_DESC'.
    Returns True if the select was found and changed (or was already set to that value).
    """
    js = """
    (sortVal) => {
        var host = document.getElementById('coral-shadow-container');
        if (!host || !host.shadowRoot) return false;
        var sr = host.shadowRoot;
        var sel = sr.querySelector('select');
        if (!sel) return false;
        if (sel.value === sortVal) return true;
        var setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
        setter.call(sel, sortVal);
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        sel.dispatchEvent(new Event('input', { bubbles: true }));
        return true;
    }
    """
    try:
        return bool(page.evaluate(js, sort_value))
    except Exception:
        return False


def _count_coral_shadow_comments(page) -> int:
    """
    Count actual comment nodes inside #coral-shadow-container's shadow DOM on the main page.
    Matches data-testid="comment-{UUID}" (excludes reaction/reply/report buttons).
    Used for the initial load wait so we don't break early due to ad-frame elements.
    """
    js = r"""
    (function() {
        var host = document.getElementById('coral-shadow-container');
        if (!host || !host.shadowRoot) return 0;
        var sr = host.shadowRoot;
        // UUID pattern: comment-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        var byTestId = sr.querySelectorAll('[data-testid^="comment-"]');
        var count = 0;
        for (var i = 0; i < byTestId.length; i++) {
            var tid = byTestId[i].getAttribute('data-testid') || '';
            if (/^comment-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(tid)) count++;
        }
        if (count === 0) {
            // Fallback: classic coral-comment class
            count = sr.querySelectorAll('.coral-comment, [class*="coral-comment"]').length;
        }
        return count;
    })()
    """
    try:
        result = page.evaluate(js)
        return result if isinstance(result, int) else 0
    except Exception:
        return 0


def _count_comment_elements(page) -> int:
    """
    Fast DOM-level count of comment elements across all frames (including Coral embed iframes).
    Used for load-more loop termination — does NOT extract text, so it works even when
    the text-extraction methods haven't matched modern Coral's hashed class names yet.
    Counts data-testid="comment*", role="article", and coral-class elements in every frame.
    """
    count_js = r"""
    (function() {
        var count = 0;
        var seen = new Set();
        function countIn(root) {
            if (!root) return;
            try {
                var els = root.querySelectorAll
                    ? root.querySelectorAll(
                        '[data-testid*="comment"], [data-testid*="Comment"],' +
                        '[role="article"], [role="comment"],' +
                        '.coral-comment, [class*="coral-comment"], [class*="coral-indent"]'
                      )
                    : [];
                for (var i = 0; i < els.length; i++) {
                    // Use a loose identity key to avoid double-counting shadow-DOM duplicates
                    var el = els[i];
                    var key = (el.getAttribute('data-testid') || el.className || '') + '|' + (el.innerText || '').substring(0, 40);
                    if (!seen.has(key)) { seen.add(key); count++; }
                }
                // Walk shadow roots
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var j = 0; j < all.length; j++) {
                    if (all[j].shadowRoot) countIn(all[j].shadowRoot);
                }
            } catch(e) {}
        }
        countIn(document);
        return count;
    })();
    """
    total = 0
    for frame in page.frames:
        try:
            result = frame.evaluate(count_js)
            if isinstance(result, int):
                total += result
        except Exception:
            pass
    return total


def extract_comments_from_page(page, keywords: list[str] | None = None) -> list[dict]:
    """
    Extract comments from current page (and any iframes). Tries Coral-specific extraction first,
    then full-page keyword search in each frame as fallback.
    Deduplicates so body-only and full-block (author + body + Rec/Reply/Share) versions count as one.
    Filters out Coral UI chrome and whole-stream blobs (e.g. "Be respectful... All Comments... Top of comments").
    """
    keywords = keywords or KEYWORDS
    all_comments = []
    for frame in page.frames:
        comments = _extract_comments_from_frame(frame)
        all_comments.extend(comments)
    if all_comments:
        by_text = {c["comment_text"]: c for c in all_comments}
        return _filter_chrome_and_stream_blobs(_deduplicate_comment_bodies(list(by_text.values())))
    comments = _extract_comments_via_shadow_dom(page)
    if comments:
        by_text = {c["comment_text"]: c for c in comments}
        return _filter_chrome_and_stream_blobs(_deduplicate_comment_bodies(list(by_text.values())))
    comments = _extract_comments_via_selectors(page)
    if comments:
        by_text = {c["comment_text"]: c for c in comments}
        return _filter_chrome_and_stream_blobs(_deduplicate_comment_bodies(list(by_text.values())))
    for frame in page.frames:
        comments = _extract_comments_via_full_page_keyword_search(frame, keywords)
        all_comments.extend(comments)
    by_text = {c["comment_text"]: c for c in all_comments}
    return _filter_chrome_and_stream_blobs(_deduplicate_comment_bodies(list(by_text.values())))


def get_matching_comments_for_article(
    page,
    article_url: str,
    article_title: str,
    keywords: list[str] | None = None,
    article_date: str = "",
) -> list[dict]:
    """
    Assume page is already navigated to article_url.
    Wait for comments to load, extract them, filter by keywords.
    Returns list of dicts: article_url, article_title, author, comment_text, matched_keywords, article_date.
    """
    keywords = keywords or KEYWORDS
    if not article_title:
        try:
            article_title = page.title() or ""
        except Exception:
            article_title = ""
    # Wait for the Coral shadow host or comment section to appear in the DOM.
    # SB Nation's Coral renders inside #coral-shadow-container (Shadow DOM), so we can't
    # wait for #coral directly — it lives inside the shadow root, invisible to CSS selectors.
    try:
        page.wait_for_selector(
            "#coral-shadow-container, [id*='comment'], [class*='comment'],"
            " .coral-stream, #coral, [data-coral-stream]",
            timeout=COMMENT_WAIT_TIMEOUT_MS
        )
    except PlaywrightTimeout:
        pass
    # Scroll to trigger lazy-loading
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass
    # Smart wait: poll until actual comment elements appear in Coral's shadow DOM (or timeout).
    # We use _count_coral_shadow_comments (shadow-DOM specific) rather than _count_comment_elements
    # (all frames) because ad iframes contain [role="article"] elements that would give false positives.
    for _ in range(COMMENT_LOAD_EXTRA_SEC):
        if _count_coral_shadow_comments(page) > 0:
            break
        time.sleep(1)
    else:
        # Comments still not visible — give one more generous wait
        time.sleep(5)
    # Final scroll after load
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)
    except Exception:
        pass
    # --- Primary extraction: Coral public GraphQL API ---
    # Coral's DOM rendering caps each sort view at ~23 comments regardless of scrolling.
    # For articles with 100+ comments, scroll-based extraction misses the majority.
    # The Coral GraphQL API at sbnation.coral.coralproject.net returns ALL comments
    # (top-level + replies) in one or a few paginated requests — no DOM scrolling needed.
    story_id = _derive_coral_story_id(article_url)
    api_comments: list[dict] = []
    if story_id:
        api_comments = _fetch_all_coral_comments_via_api(story_id, article_url)

    if api_comments:
        # API succeeded: use its results directly (complete, no DOM pagination limits)
        raw_for_dedup = api_comments
    else:
        # API fallback: scroll-based DOM extraction (handles non-standard embeds)
        prev_count = _count_comment_elements(page)
        consecutive_no_growth = 0
        for _ in range(LOAD_MORE_MAX_ITERATIONS):
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.5)
            except Exception:
                pass
            _scroll_coral_shadow_dom(page)
            for _ in range(3):
                clicked = _click_load_more_in_all_frames(page)
                if clicked:
                    time.sleep(1.2)
                    try:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        time.sleep(0.4)
                    except Exception:
                        pass
                else:
                    break
            time.sleep(LOAD_MORE_WAIT_SEC)
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.8)
            except Exception:
                pass
            new_count = _count_comment_elements(page)
            if new_count > prev_count:
                prev_count = new_count
                consecutive_no_growth = 0
            else:
                consecutive_no_growth += 1
                if consecutive_no_growth >= 3 and prev_count > 0:
                    break

        def _collect_raw_from_frames():
            result = []
            for frame in page.frames:
                result.extend(_extract_comments_from_frame(frame))
            return result

        first_batch = _collect_raw_from_frames()
        _click_all_comments_tab(page)
        prev_shadow = _count_coral_shadow_comments(page)
        for _ in range(15):
            time.sleep(1)
            new_shadow = _count_coral_shadow_comments(page)
            if new_shadow != prev_shadow and new_shadow > 0:
                break
            prev_shadow = new_shadow
        time.sleep(2)
        second_batch = _collect_raw_from_frames()

        extra_batches: list[dict] = []
        for sort_val in CORAL_EXTRA_SORT_VALUES:
            if not _change_coral_sort(page, sort_val):
                continue
            time.sleep(4)
            prev_shadow = _count_coral_shadow_comments(page)
            consecutive_stable = 0
            for _ in range(20):
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(0.3)
                except Exception:
                    pass
                _scroll_coral_shadow_dom(page)
                time.sleep(2.5)
                new_shadow = _count_coral_shadow_comments(page)
                if new_shadow > prev_shadow:
                    prev_shadow = new_shadow
                    consecutive_stable = 0
                else:
                    consecutive_stable += 1
                    if consecutive_stable >= 3 and prev_shadow > 0:
                        break
            extra_batches.extend(_collect_raw_from_frames())

        raw_for_dedup = first_batch + second_batch + extra_batches

    # Deduplicate by normalized text then by space-stripped key
    by_norm: dict[str, dict] = {}
    for c in raw_for_dedup:
        key = _normalize_ws(c["comment_text"])
        if key not in by_norm:
            by_norm[key] = c
    by_text: dict[str, dict] = {}
    for key, c in by_norm.items():
        stripped_key = key.replace(" ", "")
        if stripped_key not in by_text:
            by_text[stripped_key] = c
    # API comments are clean text; skip DOM-specific wrapper dedup when using API
    if api_comments:
        raw = list(by_text.values())
    else:
        raw = _filter_chrome_and_stream_blobs(_deduplicate_comment_bodies(list(by_text.values())))

    extracted_count = len(raw)
    # Use total from page (e.g. "98 Comments") when available so "X of Y" matches the site.
    # Fall back to the count of extracted comments so comment_index/total_comments are never None.
    page_total = _get_total_comment_count_from_page(page)
    if page_total is not None and page_total >= extracted_count:
        total_comments = page_total
    else:
        total_comments = extracted_count if extracted_count > 0 else None
    results = []
    for i, c in enumerate(raw):
        matched = _find_matched_keywords(c["comment_text"], keywords)
        if matched:
            results.append({
                "article_url": article_url,
                "article_title": article_title,
                "author": c["author"],
                "comment_text": c["comment_text"],
                "matched_keywords": matched,
                "article_date": article_date,
                "comment_index": i + 1,
                "total_comments": total_comments,
            })
    return results


def scrape_comments_for_urls(
    article_list: list[dict],
    keywords: list[str] | None = None,
    delay_sec: float = PAGE_DELAY_SEC,
) -> list[dict]:
    """
    article_list: list of dicts with at least "url" and "title".
    Opens each URL in a browser, waits for comments, collects matching comments.
    Returns flat list of comment match dicts.
    """
    keywords = keywords or KEYWORDS
    all_matches = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.set_default_timeout(60000)
            for item in article_list:
                url = item.get("url")
                title = item.get("title", "")
                if not url or not isinstance(url, str) or "blazersedge.com" not in url:
                    continue
                try:
                    url_with_comments = url if "#comments" in (url or "") else (url.split("#")[0] + "#comments")
                    page.goto(url_with_comments, wait_until="domcontentloaded", timeout=45000)
                    article_date = item.get("article_date") or ""
                    matches = get_matching_comments_for_article(page, url, title, keywords, article_date=article_date)
                    all_matches.extend(matches)
                except Exception:
                    pass
                time.sleep(delay_sec)
        finally:
            browser.close()
    return all_matches
