"""
Command-line helpers.

  python -m scanner                 run a full scan and print a summary (results are saved to the DB)
  python -m scanner <article-url>   check a single article and print its matching comments
"""

import logging
import sys

from . import config, storage
from .articles import fetch_article
from .comments import fetch_comments
from .keywords import KeywordMatcher
from .scan import run_scan


def check_one(url: str):
    matcher = KeywordMatcher(config.KEYWORDS)
    article = fetch_article(url)
    print(f"{article.title}\n  published: {article.published_iso or 'unknown'}  recent: {article.is_recent()}")
    print(f"  article keywords: {matcher.find(article.title + ' ' + article.body) or 'none'}")
    comments, total = fetch_comments(article)
    hits = [c for c in comments if matcher.find(c.text)]
    print(f"  comments: {total}  matching: {len(hits)}\n")
    for c in hits:
        print(f"#{c.index} {c.author} ({c.created_at}): {c.text[:200]}\n")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) > 1:
        check_one(sys.argv[1])
        return
    storage.init_db()
    scan_id = storage.create_scan()
    run_scan(scan_id)
    print(storage.get_scan(scan_id))


if __name__ == "__main__":
    main()
