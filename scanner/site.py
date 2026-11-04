"""
Build a static results site (used by the GitHub Actions workflow for GitHub Pages).

  python -m scanner.site --out site
"""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, storage
from .scan import run_scan

ROOT = Path(__file__).resolve().parent.parent


def build(out_dir: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        storage.DB_PATH = Path(tmp) / "scan.db"
        storage.init_db()
        scan_id = storage.create_scan()
        run_scan(scan_id)
        scan = storage.get_scan(scan_id)
        results = storage.get_scan_results(scan_id)

    if scan["status"] != "done":
        logging.error("Scan failed, not publishing: %s", scan["error_message"])
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY")
    payload = {"scan": scan, "results": results}
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
    html = env.get_template("site.html").render(
        payload=payload,
        scan=scan,
        keywords=config.KEYWORDS,
        lookback_days=config.LOOKBACK_DAYS,
        run_url=f"https://github.com/{repo}/actions/workflows/scan.yml" if repo else None,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for name in ("style.css", "results.js"):
        shutil.copy(ROOT / "static" / name, out_dir / name)
    logging.info("Wrote %s: %d article and %d comment matches",
                 out_dir / "index.html", len(results["articles"]), len(results["comments"]))
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="site", type=Path, help="output directory (default: site)")
    sys.exit(build(parser.parse_args().out))


if __name__ == "__main__":
    main()
