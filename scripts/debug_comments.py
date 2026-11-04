"""
One-off script to debug comment extraction. Run from blazers-scanner folder:
  python scripts/debug_comments.py

Saves page structure and any text containing "Deni" so we can see where comments live.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

URL = "https://www.blazersedge.com/gameday-coverage/110469/denver-nuggets-balanced-attack-sinks-portland-trail-blazers-box-score-recap#comments"
OUT_DIR = Path(__file__).resolve().parent.parent / "debug_out"
OUT_DIR.mkdir(exist_ok=True)


def main():
    dump_js = r"""
    (function() {
        var results = { frames: [], textContainingDeni: [], shadowHosts: [] };
        function walk(root, frameId) {
            if (!root) return;
            try {
                var all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                all.forEach(function(el) {
                    var text = (el.innerText || el.textContent || '').trim();
                    if (text.indexOf('Deni') !== -1 || text.indexOf('Avdija') !== -1) {
                        var tag = el.tagName || '';
                        var cls = (el.className && typeof el.className === 'string') ? el.className : '';
                        var id = el.id || '';
                        results.textContainingDeni.push({
                            tag: tag,
                            id: id,
                            className: cls ? cls.substring(0, 80) : '',
                            textLength: text.length,
                            textPreview: text.substring(0, 200)
                        });
                    }
                    if (el.shadowRoot) {
                        results.shadowHosts.push({ tag: el.tagName, id: el.id, className: (el.className || '').substring(0, 60) });
                        walk(el.shadowRoot, frameId);
                    }
                });
            } catch (e) { results.error = String(e); }
        }
        walk(document, 'main');
        return results;
    })();
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(30000)
        page.goto(URL, wait_until="load", timeout=30000)
        import time
        time.sleep(10)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)
        data = page.evaluate(dump_js)
        for i, frame in enumerate(page.frames):
            try:
                fd = frame.evaluate(dump_js)
                if fd.get("textContainingDeni") or fd.get("shadowHosts"):
                    data.setdefault("frames", []).append({"index": i, "url": frame.url, "data": fd})
            except Exception as e:
                data.setdefault("frameErrors", []).append({"index": i, "error": str(e)})
        browser.close()
    out = OUT_DIR / "comment_debug.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("Wrote", out)
    print("Shadow hosts:", len(data.get("shadowHosts", [])))
    print("Text containing Deni/Avdija (main):", len(data.get("textContainingDeni", [])))
    for i, t in enumerate(data.get("textContainingDeni", [])[:5]):
        print(" ", i + 1, t.get("tag"), t.get("className", "")[:50], "->", (t.get("textPreview") or "")[:80])


if __name__ == "__main__":
    main()
