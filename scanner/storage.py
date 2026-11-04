"""SQLite storage for scans and keyword matches."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "scanner.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db():
    """Create/migrate tables and mark scans orphaned by a previous shutdown as failed."""
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                articles_found INTEGER DEFAULT 0,
                comments_found INTEGER DEFAULT 0,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS article_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                snippet_or_body TEXT,
                matched_keywords TEXT NOT NULL,
                article_date TEXT,
                FOREIGN KEY (scan_id) REFERENCES scans(id)
            );
            CREATE TABLE IF NOT EXISTS comment_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                article_url TEXT NOT NULL,
                article_title TEXT,
                author TEXT,
                comment_text TEXT NOT NULL,
                matched_keywords TEXT NOT NULL,
                article_date TEXT,
                comment_index INTEGER,
                total_comments INTEGER,
                FOREIGN KEY (scan_id) REFERENCES scans(id)
            );
        """)
        _add_column_if_missing(conn, "scans", "progress", "TEXT")
        _add_column_if_missing(conn, "scans", "articles_scanned", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "article_matches", "article_date", "TEXT")
        _add_column_if_missing(conn, "comment_matches", "article_date", "TEXT")
        _add_column_if_missing(conn, "comment_matches", "comment_index", "INTEGER")
        _add_column_if_missing(conn, "comment_matches", "total_comments", "INTEGER")
        _add_column_if_missing(conn, "comment_matches", "comment_created_at", "TEXT")
        _add_column_if_missing(conn, "comment_matches", "is_reply", "INTEGER DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_article_matches_scan ON article_matches(scan_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_comment_matches_scan ON comment_matches(scan_id)")
        conn.execute(
            "UPDATE scans SET status = 'failed', finished_at = ?, error_message = 'Interrupted (server restarted)' "
            "WHERE status = 'running'",
            (utc_now(),),
        )


def create_scan() -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO scans (started_at, status, progress) VALUES (?, 'running', 'Starting…')",
            (utc_now(),),
        )
        return cur.lastrowid


def update_progress(scan_id: int, progress: str, articles_scanned: int | None = None):
    with get_connection() as conn:
        if articles_scanned is None:
            conn.execute("UPDATE scans SET progress = ? WHERE id = ?", (progress, scan_id))
        else:
            conn.execute("UPDATE scans SET progress = ?, articles_scanned = ? WHERE id = ?",
                         (progress, articles_scanned, scan_id))


def finish_scan(scan_id: int, articles_found: int, comments_found: int, error_message: str | None = None):
    with get_connection() as conn:
        conn.execute(
            """UPDATE scans SET finished_at = ?, status = ?, articles_found = ?, comments_found = ?,
                   error_message = ?, progress = NULL
               WHERE id = ?""",
            (utc_now(), "failed" if error_message else "done", articles_found, comments_found,
             error_message, scan_id),
        )


def add_article_match(scan_id: int, url: str, title: str, snippet: str, matched_keywords: list[str],
                      article_date: str):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO article_matches (scan_id, url, title, snippet_or_body, matched_keywords, article_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_id, url, title, snippet, ",".join(matched_keywords), article_date),
        )


def add_comment_matches(scan_id: int, rows: list[dict]):
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany(
            """INSERT INTO comment_matches (scan_id, article_url, article_title, author, comment_text,
                   matched_keywords, article_date, comment_index, total_comments, comment_created_at, is_reply)
               VALUES (:scan_id, :article_url, :article_title, :author, :comment_text,
                   :matched_keywords, :article_date, :comment_index, :total_comments, :comment_created_at, :is_reply)""",
            [{**r, "scan_id": scan_id, "matched_keywords": ",".join(r["matched_keywords"])} for r in rows],
        )


def get_scan(scan_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        return dict(row) if row else None


def get_running_scan() -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM scans WHERE status = 'running' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def get_scans(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_scan_results(scan_id: int) -> dict:
    """Matches for a scan: newest article first, comments in thread order within each article."""
    with get_connection() as conn:
        articles = conn.execute(
            """SELECT url, title, snippet_or_body, matched_keywords, article_date
               FROM article_matches WHERE scan_id = ?
               ORDER BY article_date IS NULL OR article_date = '', article_date DESC""",
            (scan_id,),
        ).fetchall()
        comments = conn.execute(
            """SELECT article_url, article_title, author, comment_text, matched_keywords, article_date,
                      comment_index, total_comments, comment_created_at, is_reply
               FROM comment_matches WHERE scan_id = ?
               ORDER BY article_date IS NULL OR article_date = '', article_date DESC, article_url, comment_index""",
            (scan_id,),
        ).fetchall()
        return {"articles": [dict(r) for r in articles], "comments": [dict(r) for r in comments]}


def delete_scan(scan_id: int) -> bool:
    """Delete a finished scan and its matches. Running scans are left alone."""
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if row is None or row["status"] == "running":
            return False
        conn.execute("DELETE FROM comment_matches WHERE scan_id = ?", (scan_id,))
        conn.execute("DELETE FROM article_matches WHERE scan_id = ?", (scan_id,))
        conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        return True


def delete_all_scans():
    with get_connection() as conn:
        conn.execute("DELETE FROM comment_matches WHERE scan_id NOT IN (SELECT id FROM scans WHERE status = 'running')")
        conn.execute("DELETE FROM article_matches WHERE scan_id NOT IN (SELECT id FROM scans WHERE status = 'running')")
        conn.execute("DELETE FROM scans WHERE status != 'running'")
