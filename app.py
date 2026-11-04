"""
Blazer's Edge Deni/Avdija Scanner – Flask app and API.

Environment variables:
  HOST / PORT          bind address (default 127.0.0.1:5000; cloudflared connects to localhost)
  SCANNER_PASSWORD     if set, starting/deleting scans requires this password
  KEYWORDS, LOOKBACK_DAYS, MAX_ARTICLES, FETCH_WORKERS   see scanner/config.py
"""

import hmac
import logging
import os
import threading
from functools import wraps

from flask import Flask, jsonify, render_template, request

from scanner import config, storage
from scanner.scan import run_scan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
PASSWORD = os.environ.get("SCANNER_PASSWORD", "")
_start_lock = threading.Lock()


def require_password(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if PASSWORD and not hmac.compare_digest(request.headers.get("X-Scanner-Password", ""), PASSWORD):
            return jsonify({"error": "Password required"}), 401
        return view(*args, **kwargs)
    return wrapper


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_config():
    return jsonify({
        "keywords": config.KEYWORDS,
        "lookback_days": config.LOOKBACK_DAYS,
        "max_articles": config.MAX_ARTICLES,
        "password_required": bool(PASSWORD),
    })


@app.route("/api/scan", methods=["POST"])
@require_password
def api_start_scan():
    with _start_lock:
        running = storage.get_running_scan()
        if running:
            return jsonify({"error": "A scan is already running", "scan_id": running["id"]}), 409
        scan_id = storage.create_scan()
    threading.Thread(target=run_scan, args=(scan_id,), daemon=True, name=f"scan-{scan_id}").start()
    return jsonify({"scan_id": scan_id}), 201


@app.route("/api/scans")
def api_list_scans():
    limit = max(1, min(request.args.get("limit", 20, type=int), 100))
    return jsonify({"scans": storage.get_scans(limit=limit)})


@app.route("/api/scans/<int:scan_id>")
def api_get_scan(scan_id):
    scan = storage.get_scan(scan_id)
    if not scan:
        return jsonify({"error": "Scan not found"}), 404
    return jsonify({"scan": scan})


@app.route("/api/scans/<int:scan_id>/results")
def api_scan_results(scan_id):
    scan = storage.get_scan(scan_id)
    if not scan:
        return jsonify({"error": "Scan not found"}), 404
    return jsonify({"scan": scan, "results": storage.get_scan_results(scan_id)})


@app.route("/api/scans/<int:scan_id>", methods=["DELETE"])
@require_password
def api_delete_scan(scan_id):
    if not storage.delete_scan(scan_id):
        return jsonify({"error": "Scan not found or still running"}), 409
    return jsonify({"ok": True})


@app.route("/api/scans", methods=["DELETE"])
@require_password
def api_delete_all_scans():
    storage.delete_all_scans()
    return jsonify({"ok": True})


def main():
    storage.init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    if not PASSWORD:
        logger.warning("SCANNER_PASSWORD is not set: anyone with the tunnel URL can start or delete scans")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
