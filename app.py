"""
Blazer's Edge Deni/Avdija Scanner – Flask app and API.
"""

import logging
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from scanner import storage
from scanner.crawler import crawl_article_urls
from scanner.articles import check_article_for_keywords
from scanner.comments import scrape_comments_for_urls

app = Flask(__name__)
app.config["MAX_ARTICLES"] = 80
app.config["KEYWORDS"] = ["deni", "Deni", "avdija", "Avdija", "Deni Avdija", "deni avdija"]

# In-memory scan status for async runs (scan_id -> "running" | "done" | "failed")
_scan_status = {}
_scan_lock = threading.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_scan(scan_id: int):
    """Background task: crawl articles, match keywords, scrape comments, save to DB."""
    articles_found = 0
    comments_found = 0
    error_message = None
    try:
        with _scan_lock:
            _scan_status[scan_id] = "running"

        urls = crawl_article_urls(max_articles=app.config["MAX_ARTICLES"])
        keywords = app.config["KEYWORDS"]
        # Only articles from the last week (for comment scraping)
        article_list_for_comments = []

        for url in urls:
            try:
                result, within_week, title, article_date = check_article_for_keywords(
                    url, keywords=keywords, only_last_week=True
                )
                if within_week:
                    article_list_for_comments.append({"url": url, "title": title or "", "article_date": article_date or ""})
                if result:
                    storage.add_article_match(
                        scan_id,
                        url=result["url"],
                        title=result["title"],
                        snippet_or_body=result["snippet_or_body"],
                        matched_keywords=result["matched_keywords"],
                        article_date=result.get("article_date") or "",
                    )
                    articles_found += 1
            except Exception as e:
                logger.warning("Article %s failed: %s", url, e)

        # Scrape comments only on articles from the last week; filter by keywords in comments
        try:
            comment_matches = scrape_comments_for_urls(article_list_for_comments, keywords=keywords)
            for c in comment_matches:
                storage.add_comment_match(
                    scan_id,
                    article_url=c["article_url"],
                    article_title=c["article_title"],
                    author=c["author"],
                    comment_text=c["comment_text"],
                    matched_keywords=c["matched_keywords"],
                    article_date=c.get("article_date") or "",
                    comment_index=c.get("comment_index"),
                    total_comments=c.get("total_comments"),
                )
                comments_found += 1
        except Exception as e:
            logger.warning("Comments scrape failed: %s", e)

    except Exception as e:
        logger.exception("Scan failed")
        error_message = str(e)
    finally:
        storage.finish_scan(scan_id, articles_found, comments_found, error_message)
        with _scan_lock:
            _scan_status[scan_id] = "failed" if error_message else "done"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def api_start_scan():
    """Start a new scan (runs in background). Returns scan id."""
    storage.init_db()
    scan_id = storage.create_scan()
    thread = threading.Thread(target=run_scan, args=(scan_id,))
    thread.daemon = True
    thread.start()
    return jsonify({"scan_id": scan_id}), 201


@app.route("/api/scans")
def api_list_scans():
    """List recent scans."""
    storage.init_db()
    limit = request.args.get("limit", 20, type=int)
    scans = storage.get_scans(limit=min(limit, 100))
    return jsonify({"scans": scans})


@app.route("/api/scans/<int:scan_id>/results")
def api_scan_results(scan_id):
    """Get article and comment matches for a scan."""
    storage.init_db()
    scan = storage.get_scan(scan_id)
    if not scan:
        return jsonify({"error": "Scan not found"}), 404
    results = storage.get_scan_results(scan_id)
    return jsonify({"scan": scan, "results": results})


@app.route("/api/scans", methods=["DELETE"])
def api_delete_all_scans():
    """Delete all scans and their results."""
    storage.init_db()
    storage.delete_all_scans()
    return jsonify({"ok": True}), 200


@app.route("/api/scans/<int:scan_id>/status")
def api_scan_status(scan_id):
    """Get current status of a scan (running / done / failed)."""
    storage.init_db()
    scan = storage.get_scan(scan_id)
    if not scan:
        return jsonify({"error": "Scan not found"}), 404
    with _scan_lock:
        status = _scan_status.get(scan_id, scan.get("status", "unknown"))
    return jsonify({"scan_id": scan_id, "status": status, "scan": scan})


def main():
    storage.init_db()
    app.run(host="0.0.0.0", debug=True, port=5000, use_reloader=False)


if __name__ == "__main__":
    main()
