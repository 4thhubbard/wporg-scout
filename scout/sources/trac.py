"""Pull tickets from a Trac instance via the CSV report endpoint.

Trac doesn't have a clean JSON API, but every Trac query supports `&format=csv`
which gives us a parseable feed. We point at a custom query URL and parse rows.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlencode

import requests

from scout.db import Item


def fetch_tickets(
    base_url: str,
    components: list[str] | None = None,
    statuses: list[str] | None = None,
    max_age_days: int | None = 365,
) -> Iterator[Item]:
    """Yield Item objects for matching Trac tickets.

    base_url: e.g. 'https://core.trac.wordpress.org'
    components: list of Trac components (filter — OR within the field)
    statuses: list of ticket statuses (default: new + reopened)
    max_age_days: skip tickets older than this many days (None = no limit)
    """
    query = _build_query(components, statuses, max_age_days)
    csv_url = f"{base_url}/query?{urlencode(query, doseq=True)}&format=csv"

    try:
        r = requests.get(
            csv_url,
            headers={"User-Agent": "wporg-scout/0.1"},
            timeout=60,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! trac fetch failed: {e}")
        return

    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        if not row.get("id"):
            continue
        yield _to_item(base_url, row)


def _build_query(
    components: list[str] | None,
    statuses: list[str] | None,
    max_age_days: int | None,
) -> dict:
    q: dict = {}
    if statuses:
        q["status"] = statuses
    else:
        q["status"] = ["new", "reopened"]
    if components:
        q["component"] = components
    # Trac's `time` filter expects relative format like ">-365d"
    if max_age_days is not None:
        q["time"] = f">-{max_age_days}d"
    # Useful columns to pull back in the CSV
    q["col"] = [
        "id",
        "summary",
        "status",
        "component",
        "type",
        "priority",
        "reporter",
        "time",
        "changetime",
        "keywords",
    ]
    q["max"] = "500"  # cap one fetch
    return q


def _to_item(base_url: str, row: dict) -> Item:
    ticket_id = row["id"]
    labels = []
    for key in ("component", "type", "priority", "keywords"):
        v = (row.get(key) or "").strip()
        if v:
            labels.append(f"{key}:{v}")

    return Item(
        source="trac",
        external_id=f"trac#{ticket_id}",
        title=row.get("summary") or "(no summary)",
        body=None,  # Trac CSV doesn't include description — could re-fetch per ticket if we want
        url=f"{base_url}/ticket/{ticket_id}",
        state=row.get("status"),
        labels=labels,
        author=row.get("reporter"),
        created_at=row.get("time") or None,
        updated_at=row.get("changetime") or None,
    )


def fetch_all(
    base_url: str,
    components: list[str] | None = None,
    statuses: list[str] | None = None,
    max_age_days: int | None = 365,
) -> Iterator[Item]:
    yield from fetch_tickets(base_url, components, statuses, max_age_days)
