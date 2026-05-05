"""Render the triage queue as a markdown digest.

Used by the morning scheduled task and by the `scout digest` CLI command.
Output is markdown so it renders nicely in Cowork artifacts, in your editor,
or as an email body.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Optional

from scout import db


# Order matters — most actionable first
DIGEST_ORDER = (
    "content-fix",
    "ux-issue",
    "code-bug",
    "feature-request",
    "meta",
    "question",
    "unclear",
)

# How many items to show per category in the digest
TOP_PER_CATEGORY = 5


def render(
    conn: sqlite3.Connection,
    top_per_category: int = TOP_PER_CATEGORY,
    title: Optional[str] = None,
) -> str:
    """Return a markdown digest of the current triage queue."""
    today = datetime.now().strftime("%Y-%m-%d")
    title = title or f"wporg-scout digest — {today}"

    s = db.stats(conn)
    total = s["total"]
    classified = sum(v for k, v in s["by_classification"].items() if k != "(unclassified)")
    unclassified_count = s["by_classification"].get("(unclassified)", 0)

    lines: list[str] = []
    lines.append(f"# {title}\n")
    lines.append(f"_Total: **{total}** items · classified: **{classified}** · unclassified: **{unclassified_count}**_\n")

    # Headline: top of each category, in priority order
    for category in DIGEST_ORDER:
        rows = _top_in_category(conn, category, limit=top_per_category)
        if not rows:
            continue
        total_in_cat = s["by_classification"].get(category, 0)
        more = ""
        if total_in_cat > top_per_category:
            more = f" — _showing top {top_per_category} of {total_in_cat}_"
        lines.append(f"\n## {category}{more}\n")
        for row in rows:
            lines.append(_format_row(row))

    if not classified:
        lines.append("\n_Nothing classified yet. Run `python3 -m scout classify` first._\n")

    lines.append("\n---")
    lines.append(f"\n_Pick something to work on:_ `python3 -m scout work <id>`")
    return "\n".join(lines)


def _top_in_category(
    conn: sqlite3.Connection,
    category: str,
    limit: int,
) -> list[sqlite3.Row]:
    """Highest-confidence, most recently classified items in a category."""
    return conn.execute(
        """
        SELECT * FROM items
        WHERE classification = ?
        ORDER BY classification_confidence DESC NULLS LAST, classified_at DESC
        LIMIT ?
        """,
        (category, limit),
    ).fetchall()


def _format_row(row: sqlite3.Row) -> str:
    conf = row["classification_confidence"]
    conf_str = f"{conf:.2f}" if conf is not None else "—"
    src = row["source"]
    title = (row["title"] or "(no title)").strip()
    if len(title) > 100:
        title = title[:97] + "…"
    reason = (row["classification_reason"] or "").strip()
    reason_suffix = f" — _{reason}_" if reason else ""
    return (
        f"- **#{row['id']}** [{src}] [conf {conf_str}] [{title}]({row['url']}){reason_suffix}"
    )
