# Blazer's Edge Deni/Avdija Scanner

A dashboard that scans [Blazer's Edge](https://www.blazersedge.com/) for **articles from the last 3 days** and their
comments that mention **Deni Avdija** (keywords `Deni`, `Avdija`, and the common misspelling `Avidja`), and shows the
results in a web UI.

## How it works

1. Collects article links from the homepage, section pages and the RSS feed (newest first, up to 80).
2. Fetches each article (4 in parallel) and reads its title, publish date and body from the page's JSON-LD metadata.
   Articles older than the lookback window are skipped.
3. For each recent article, downloads **all** comments from SB Nation's comment service ("Cyclone",
   `https://beta.sbnation.com/api`, GraphQL) in one request — no browser needed.
4. Matches keywords case-insensitively on word boundaries, so `Deni`, `deni`, `Deni's` and `Denis` match but
   `denied` does not.

A full scan takes well under a minute.

## Setup

Requires Python 3.10+.

```bash
cd blazers-scanner
python -m venv venv
venv\Scripts\activate          # Windows  (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

Open **http://127.0.0.1:5000**. To share it publicly, run `start.bat`, which also starts a Cloudflare quick tunnel.
`stop.bat` shuts both down.

Because the tunnel URL is public, set a password so only you can start or delete scans (viewing stays open):

```bat
setx SCANNER_PASSWORD "choose-a-password"
```

The browser asks for it the first time you click a protected button and remembers it.

## Command line

```bash
python -m scanner                       # run a full scan, save it to the DB, print a summary
python -m scanner <article-url>         # check one article and print its matching comments
```

## Configuration

All settings are environment variables (defaults in `scanner/config.py`):

| Variable           | Default                | Meaning                                    |
|--------------------|------------------------|--------------------------------------------|
| `KEYWORDS`         | `Deni,Avdija,Avidja`   | Comma-separated keywords                   |
| `LOOKBACK_DAYS`    | `3`                    | Only articles newer than this are scanned  |
| `MAX_ARTICLES`     | `80`                   | Max article links to check                 |
| `FETCH_WORKERS`    | `4`                    | Parallel article fetches                   |
| `SCANNER_PASSWORD` | *(unset)*              | Required to start/delete scans when set    |
| `HOST` / `PORT`    | `127.0.0.1` / `5000`   | Bind address                               |

## Data

Results are stored in SQLite at `data/scanner.db` (created automatically). Scans interrupted by a server restart
are marked as failed on the next start.

## If it breaks

The scanner depends on the site's markup and comment API. If scans start returning zero comments:

- Run `python -m scanner <article-url>` on an article you know has comments.
- If the comment service moved again, open an article in a browser, open DevTools → Network, click the comments
  button, and look for the GraphQL request; update `CYCLONE_API_URL` / the queries in `scanner/comments.py`.
