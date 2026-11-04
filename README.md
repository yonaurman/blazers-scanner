# Blazer's Edge Deni/Avdija Scanner

A dashboard that scans [Blazer's Edge](https://www.blazersedge.com/) for **articles from the last 3 days** and their comments, matching the keywords **"deni"**, **"Deni"**, **"Avidja"**, **"Avdija"**, or **"Deni Avdija"**, then displays the results in a web UI.

## Requirements

- Python 3.10+
- Chromium (for Playwright comment scraping)

## Setup

1. **Create a virtual environment (recommended)**

   ```bash
   cd blazers-scanner
   python -m venv venv
   venv\Scripts\activate   # Windows
   # or: source venv/bin/activate   # macOS/Linux
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers (required for comment scraping)**

   ```bash
   playwright install chromium
   ```

## Run the app

```bash
 python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

## Usage

1. Click **Run new scan** to start a scan. The scanner will:
   - Crawl the Blazer's Edge homepage for article links
   - Keep only articles from the **last 3 days** (by article publish date)
   - For each recent article, look for "deni", "Deni", "Avidja", "Avdija", or "Deni Avdija" in the title and body
   - Open each recent article in a headless browser and collect comments that mention those keywords
2. Recent scans appear in the list. Click a scan to view **Matching articles** and **Matching comments**.
3. Use **Print results** to print the current results, or **Export CSV** to download them as a CSV file.

## Configuration

Edit `app.py` to change:

- `app.config["MAX_ARTICLES"]` — maximum number of articles to crawl (default: 50)
- `app.config["KEYWORDS"]` — list of keywords to search for (default: `["deni", "Deni", "Avidja", "Avdija", "Deni Avdija"]`)
- In `scanner/articles.py`, `LAST_WEEK_DAYS` (default: 3) controls how many days back an article is considered "recent"; only recent articles are scanned for keyword matches and comments.

## Data

Scan results are stored in SQLite at `blazers-scanner/data/scanner.db`. The database is created automatically on first run.

## Notes

- **Comments** are loaded by JavaScript (Coral). The app uses Playwright to render the page and then extracts comment text. If the site’s comment markup changes, extraction may need to be updated.
- **Rate limiting**: The scanner adds delays between requests and between opening article pages to avoid overloading the site.
- Scans run in a background thread; the dashboard polls for status until the scan finishes.
