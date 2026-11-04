"""SQLite storage for scans and keyword matches."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "scanner.db"


def get_db_path() -> Path:
    return DB_PATH


@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _add_column_if_missing(conn, table: str, column: str, col_type: str):
    """Add column to table if it doesn't exist (for migrations)."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    if any(row[1] == column for row in cur.fetchall()):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db():
    """Create tables if they don't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                articles_found INTEGER DEFAULT 0,
                comments_found INTEGER DEFAULT 0,
                error_message TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS article_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                snippet_or_body TEXT,
                matched_keywords TEXT NOT NULL,
                article_date TEXT,
                FOREIGN KEY (scan_id) REFERENCES scans(id)
            )
        """)
        conn.execute("""
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
            )
        """)
        _add_column_if_missing(conn, "article_matches", "article_date", "TEXT")
        _add_column_if_missing(conn, "comment_matches", "article_date", "TEXT")
        _add_column_if_missing(conn, "comment_matches", "comment_index", "INTEGER")
        _add_column_if_missing(conn, "comment_matches", "total_comments", "INTEGER")


def create_scan() -> int:
    """Create a new scan record (status=running). Returns scan_id."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO scans (started_at, status) VALUES (?, ?)",
            (datetime.utcnow().isoformat(), "running")
        )
        return cur.lastrowid


def finish_scan(scan_id: int, articles_found: int, comments_found: int, error_message: Optional[str] = None):
    """Mark scan as finished and set counts."""
    status = "failed" if error_message else "done"
    with get_connection() as conn:
        conn.execute(
            """UPDATE scans SET finished_at = ?, status = ?, articles_found = ?, comments_found = ?, error_message = ?
               WHERE id = ?""",
            (datetime.utcnow().isoformat(), status, articles_found, comments_found, error_message, scan_id)
        )


def add_article_match(scan_id: int, url: str, title: str, snippet_or_body: str, matched_keywords: list[str],
                      article_date: Optional[str] = None):
    """Store an article that matched keywords."""
    keywords_str = ",".join(matched_keywords)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO article_matches (scan_id, url, title, snippet_or_body, matched_keywords, article_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (scan_id, url, title, snippet_or_body, keywords_str, article_date or "")
        )


def add_comment_match(scan_id: int, article_url: str, article_title: str, author: str,
                      comment_text: str, matched_keywords: list[str], article_date: Optional[str] = None,
                      comment_index: Optional[int] = None, total_comments: Optional[int] = None):
    """Store a comment that matched keywords."""
    keywords_str = ",".join(matched_keywords)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO comment_matches (scan_id, article_url, article_title, author, comment_text, matched_keywords, article_date, comment_index, total_comments)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scan_id, article_url, article_title, author, comment_text, keywords_str, article_date or "", comment_index, total_comments)
        )


def get_scan(scan_id: int) -> Optional[dict]:
    """Get a single scan by id."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if row is None:
            return None
        return dict(row)


def get_scans(limit: int = 20) -> list[dict]:
    """List recent scans."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM scans ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_all_scans():
    """Delete all scans and their article/comment matches."""
    with get_connection() as conn:
        conn.execute("DELETE FROM comment_matches")
        conn.execute("DELETE FROM article_matches")
        conn.execute("DELETE FROM scans")


def get_scan_results(scan_id: int) -> dict:
    """Get article and comment matches for a scan, newest to oldest by article_date."""
    with get_connection() as conn:
        articles = conn.execute(
            """SELECT url, title, snippet_or_body, matched_keywords, article_date
               FROM article_matches WHERE scan_id = ?
               ORDER BY article_date IS NULL, article_date DESC""",
            (scan_id,)
        ).fetchall()
        comments = conn.execute(
            """SELECT article_url, article_title, author, comment_text, matched_keywords, article_date, comment_index, total_comments
               FROM comment_matches WHERE scan_id = ?
               ORDER BY article_date IS NULL, article_date DESC""",
            (scan_id,)
        ).fetchall()
        return {
            "articles": [dict(r) for r in articles],
            "comments": [dict(r) for r in comments],
        }
