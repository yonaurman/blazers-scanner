"""Run a full scan: discover articles, keep recent ones, match keywords in articles and comments."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import config, storage
from .articles import Article, fetch_article
from .comments import fetch_comments
from .crawler import crawl_article_urls
from .keywords import KeywordMatcher

logger = logging.getLogger(__name__)


@dataclass
class ArticleResult:
    article: Article | None
    recent: bool = False
    article_keywords: list[str] = field(default_factory=list)
    comment_rows: list[dict] = field(default_factory=list)
    error: str | None = None


def _process_article(url: str, matcher: KeywordMatcher, lookback_days: int) -> ArticleResult:
    try:
        article = fetch_article(url)
    except Exception as e:
        return ArticleResult(None, error=f"article fetch failed: {e}")
    if not article.is_recent(lookback_days):
        return ArticleResult(article)

    result = ArticleResult(article, recent=True,
                           article_keywords=matcher.find(f"{article.title}\n{article.body}"))
    try:
        comments, total = fetch_comments(article)
    except Exception as e:
        result.error = f"comments fetch failed: {e}"
        return result
    for c in comments:
        matched = matcher.find(c.text)
        if matched:
            result.comment_rows.append({
                "article_url": article.url,
                "article_title": article.title,
                "author": c.author,
                "comment_text": c.text,
                "matched_keywords": matched,
                "article_date": article.published_iso,
                "comment_index": c.index,
                "total_comments": total,
                "comment_created_at": c.created_at,
                "is_reply": int(c.is_reply),
            })
    return result


def run_scan(scan_id: int, keywords: list[str] | None = None,
             max_articles: int = config.MAX_ARTICLES, lookback_days: int = config.LOOKBACK_DAYS):
    matcher = KeywordMatcher(keywords or config.KEYWORDS)
    articles_found = comments_found = 0
    error_message = None
    try:
        storage.update_progress(scan_id, "Finding articles…")
        urls = crawl_article_urls(max_articles)
        if not urls:
            raise RuntimeError("No article links found on Blazer's Edge (site layout may have changed)")

        done = recent = failures = 0
        with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as pool:
            futures = {pool.submit(_process_article, url, matcher, lookback_days): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                res = future.result()
                done += 1
                if res.error:
                    failures += 1
                    logger.warning("%s: %s", url, res.error)
                if res.recent:
                    recent += 1
                a = res.article
                if a and res.article_keywords:
                    storage.add_article_match(scan_id, a.url, a.title, matcher.snippet(a.body or a.title),
                                              res.article_keywords, a.published_iso)
                    articles_found += 1
                if res.comment_rows:
                    storage.add_comment_matches(scan_id, res.comment_rows)
                    comments_found += len(res.comment_rows)
                storage.update_progress(
                    scan_id,
                    f"Checked {done}/{len(urls)} articles ({recent} recent) · "
                    f"{articles_found} article matches · {comments_found} comment matches",
                    articles_scanned=done,
                )
        if failures == len(urls):
            raise RuntimeError(f"All {failures} article fetches failed; see server log")
        logger.info("Scan %d done: %d urls, %d recent, %d failures, %d article / %d comment matches",
                    scan_id, len(urls), recent, failures, articles_found, comments_found)
    except Exception as e:
        logger.exception("Scan %d failed", scan_id)
        error_message = str(e)
    finally:
        storage.finish_scan(scan_id, articles_found, comments_found, error_message)
