"""SQLite storage for scout items.

One table — `items` — stores everything pulled from any source. Sources
upsert by (source, external_id), so re-syncing updates rather than duplicates.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                  -- 'github', 'trac', 'make_p2'
    external_id TEXT NOT NULL,             -- e.g. 'WordPress/wporg-main-2022#123'
    title TEXT NOT NULL,
    body TEXT,
    url TEXT NOT NULL,
    state TEXT,                            -- 'open', 'new', 'reopened', etc.
    labels_json TEXT,                      -- JSON array of label strings
    author TEXT,
    created_at TEXT,                       -- ISO8601
    updated_at TEXT,                       -- ISO8601
    fetched_at TEXT NOT NULL,              -- ISO8601, set on every upsert
    classification TEXT,                   -- one of CLASSIFICATIONS, or NULL if not yet classified
    classification_reason TEXT,            -- one-line explanation from the LLM
    classification_confidence REAL,        -- 0.0..1.0
    classified_at TEXT,                    -- ISO8601
    UNIQUE(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_items_classification ON items(classification);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_classified_at ON items(classified_at);
"""


CLASSIFICATIONS = (
    "code-bug",
    "content-fix",
    "ux-issue",
    "feature-request",
    "question",
    "meta",
    "unclear",
)


@dataclass
class Item:
    """One thing pulled from a source. Keys are normalized across sources."""

    source: str
    external_id: str
    title: str
    body: Optional[str]
    url: str
    state: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    author: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    # Filled in by db.upsert / classify
    id: Optional[int] = None
    fetched_at: Optional[str] = None
    classification: Optional[str] = None
    classification_reason: Optional[str] = None
    classification_confidence: Optional[float] = None
    classified_at: Optional[str] = None


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection with row_factory set + schema applied."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, item: Item) -> int:
    """Insert or update an item by (source, external_id). Returns the row id.

    Preserves classification fields if the item already exists and was classified.
    """
    now = _now()
    cur = conn.cursor()
    existing = cur.execute(
        "SELECT id, classification, classification_reason, classification_confidence, classified_at "
        "FROM items WHERE source = ? AND external_id = ?",
        (item.source, item.external_id),
    ).fetchone()

    if existing:
        cur.execute(
            """
            UPDATE items
            SET title = ?, body = ?, url = ?, state = ?, labels_json = ?,
                author = ?, created_at = ?, updated_at = ?, fetched_at = ?
            WHERE id = ?
            """,
            (
                item.title,
                item.body,
                item.url,
                item.state,
                json.dumps(item.labels),
                item.author,
                item.created_at,
                item.updated_at,
                now,
                existing["id"],
            ),
        )
        return existing["id"]

    cur.execute(
        """
        INSERT INTO items
            (source, external_id, title, body, url, state, labels_json, author,
             created_at, updated_at, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item.source,
            item.external_id,
            item.title,
            item.body,
            item.url,
            item.state,
            json.dumps(item.labels),
            item.author,
            item.created_at,
            item.updated_at,
            now,
        ),
    )
    return cur.lastrowid


def mark_classified(
    conn: sqlite3.Connection,
    item_id: int,
    classification: str,
    reason: str,
    confidence: float,
) -> None:
    if classification not in CLASSIFICATIONS:
        raise ValueError(
            f"Unknown classification {classification!r}. "
            f"Expected one of {CLASSIFICATIONS}."
        )
    conn.execute(
        """
        UPDATE items
        SET classification = ?, classification_reason = ?,
            classification_confidence = ?, classified_at = ?
        WHERE id = ?
        """,
        (classification, reason, confidence, _now(), item_id),
    )


def unclassified(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    """Items that haven't been LLM-classified yet."""
    return conn.execute(
        "SELECT * FROM items WHERE classification IS NULL ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def list_items(
    conn: sqlite3.Connection,
    classification: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Filter triaged items."""
    sql = "SELECT * FROM items WHERE 1=1"
    params: list = []
    if classification:
        sql += " AND classification = ?"
        params.append(classification)
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY classified_at DESC NULLS LAST, id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_item(conn: sqlite3.Connection, item_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def stats(conn: sqlite3.Connection) -> dict:
    """Quick counts by source and by classification."""
    by_source = {
        row["source"]: row["c"]
        for row in conn.execute(
            "SELECT source, COUNT(*) c FROM items GROUP BY source"
        )
    }
    by_class = {
        (row["classification"] or "(unclassified)"): row["c"]
        for row in conn.execute(
            "SELECT classification, COUNT(*) c FROM items GROUP BY classification"
        )
    }
    total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    return {"total": total, "by_source": by_source, "by_classification": by_class}
