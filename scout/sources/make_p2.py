"""Pull recent posts from Make WordPress P2 sites via the WP REST API.

P2 sites are noisy — most posts are status updates, not issues. We apply a
lightweight regex pre-filter on the title + excerpt before sending each post
to the LLM classifier (saves tokens on noise).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests

from scout.db import Item

# Strip HTML tags from REST API responses (rendered.body)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def fetch_site_posts(
    site_url: str,
    lookback_days: int = 30,
    pre_filter_patterns: list[str] | None = None,
) -> Iterator[Item]:
    """Yield Item objects for issue-like posts on a Make P2 site.

    site_url: e.g. 'https://make.wordpress.org/community/' (trailing slash optional)
    """
    site_url = site_url.rstrip("/")
    api = f"{site_url}/wp-json/wp/v2/posts"
    after = (
        datetime.now(timezone.utc) - timedelta(days=lookback_days)
    ).isoformat(timespec="seconds")

    compiled_filters = (
        [re.compile(p) for p in pre_filter_patterns] if pre_filter_patterns else []
    )

    page = 1
    while True:
        try:
            r = requests.get(
                api,
                headers={"User-Agent": "wporg-scout/0.1"},
                params={"per_page": 100, "page": page, "after": after},
                timeout=30,
            )
            r.raise_for_status()
        except requests.HTTPError as e:
            # WP REST returns 400 when paging past the end — that's fine, just stop
            status = e.response.status_code if e.response is not None else "?"
            if status == 400:
                return
            print(f"  ! make_p2 {site_url}: HTTP {status} — skipping")
            return
        except requests.RequestException as e:
            print(f"  ! make_p2 {site_url}: {e} — skipping")
            return

        batch = r.json()
        if not batch:
            return
        for raw in batch:
            item = _to_item(site_url, raw)
            if compiled_filters and not _matches_any(
                item.title + " " + (item.body or ""), compiled_filters
            ):
                continue
            yield item
        if len(batch) < 100:
            return
        page += 1


def _matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def _strip_html(s: str) -> str:
    return _HTML_TAG_RE.sub("", s or "").strip()


def _to_item(site_url: str, raw: dict) -> Item:
    title = _strip_html((raw.get("title") or {}).get("rendered", ""))
    excerpt = _strip_html((raw.get("excerpt") or {}).get("rendered", ""))
    return Item(
        source="make_p2",
        external_id=f"{site_url}#{raw['id']}",
        title=title or "(no title)",
        body=excerpt,
        url=raw.get("link") or f"{site_url}/?p={raw['id']}",
        state="published",
        labels=[],
        author=str(raw.get("author") or ""),
        created_at=raw.get("date_gmt"),
        updated_at=raw.get("modified_gmt"),
    )


def fetch_all(
    sites: list[str],
    lookback_days: int = 30,
    pre_filter_patterns: list[str] | None = None,
) -> Iterator[Item]:
    for site in sites:
        yield from fetch_site_posts(site, lookback_days, pre_filter_patterns)
