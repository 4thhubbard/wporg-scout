"""Pull open issues from GitHub repos via the REST API."""

from __future__ import annotations

import os
from typing import Iterator

import requests

from scout.db import Item

GH_API = "https://api.github.com"


def _headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wporg-scout/0.1",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_repo_issues(
    repo: str,
    labels: list[str] | None = None,
    state: str = "open",
) -> Iterator[Item]:
    """Yield Item objects for each open issue in `repo` (e.g. 'WordPress/wporg-main-2022').

    Skips pull requests (the GitHub Issues endpoint includes PRs by default).
    """
    page = 1
    while True:
        params = {"state": state, "per_page": 100, "page": page}
        if labels:
            params["labels"] = ",".join(labels)
        r = requests.get(
            f"{GH_API}/repos/{repo}/issues",
            headers=_headers(),
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            return
        for raw in batch:
            # /issues includes PRs — skip them
            if "pull_request" in raw:
                continue
            yield _to_item(repo, raw)
        if len(batch) < 100:
            return
        page += 1


def _to_item(repo: str, raw: dict) -> Item:
    return Item(
        source="github",
        external_id=f"{repo}#{raw['number']}",
        title=raw.get("title") or "(no title)",
        body=raw.get("body") or "",
        url=raw.get("html_url", ""),
        state=raw.get("state"),
        labels=[lbl["name"] for lbl in raw.get("labels", []) if isinstance(lbl, dict)],
        author=(raw.get("user") or {}).get("login"),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
    )


def fetch_all(repos: list[str], labels: list[str] | None = None) -> Iterator[Item]:
    for repo in repos:
        try:
            yield from fetch_repo_issues(repo, labels=labels)
        except requests.HTTPError as e:
            # Don't let one bad repo kill the whole sync. Log and move on.
            status = e.response.status_code if e.response is not None else "?"
            print(f"  ! github {repo}: HTTP {status} — skipping")
        except requests.RequestException as e:
            print(f"  ! github {repo}: {e} — skipping")
