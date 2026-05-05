"""Set up a worktree + branch + context file for an item, ready for Claude Code.

Layer 2 of the loop: human triggers `scout work <id>` once they've picked
something from the digest. This:
  1. Looks up the item
  2. Resolves the target repo (for github items)
  3. Creates a git worktree at a known path with a fresh branch
  4. Writes a context file (issue.md) into the worktree root
  5. Prints the next-step command

Optional `--auto` flag spawns `claude` in the worktree with the context
preloaded — pr-review-toolkit auto-fires when Claude Code reviews its work.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scout import db


# Where worktrees get created. Override via SCOUT_WORKTREES env var.
DEFAULT_WORKTREES_ROOT = Path.home() / "repos" / "scout-worktrees"

# Where your local clones of WordPress repos live. Override via SCOUT_REPOS_ROOT.
DEFAULT_REPOS_ROOT = Path.home() / "repos"


@dataclass
class WorktreeSetup:
    item_id: int
    repo: str                # e.g. 'WordPress/wporg-main-2022'
    repo_path: Path          # local clone path
    worktree_path: Path      # where the worktree was created
    branch: str              # e.g. 'scout/123-fix-broken-hero'
    context_path: Path       # path to issue.md inside the worktree


class WorkError(Exception):
    """Raised when we can't set up the worktree (no clone, bad item, etc.)."""


def setup_worktree(
    conn: sqlite3.Connection,
    item_id: int,
    worktrees_root: Optional[Path] = None,
    repos_root: Optional[Path] = None,
) -> WorktreeSetup:
    """Set up everything Claude Code needs to work on this item.

    Raises WorkError with a clear message if anything's missing.
    """
    worktrees_root = worktrees_root or Path(
        os.environ.get("SCOUT_WORKTREES", DEFAULT_WORKTREES_ROOT)
    )
    repos_root = repos_root or Path(
        os.environ.get("SCOUT_REPOS_ROOT", DEFAULT_REPOS_ROOT)
    )

    row = db.get_item(conn, item_id)
    if not row:
        raise WorkError(f"No item with id {item_id}.")

    if row["source"] != "github":
        raise WorkError(
            f"Item #{item_id} is from {row['source']}, not github. "
            "Layer 2 only handles GitHub issues right now."
        )

    repo = _repo_from_external_id(row["external_id"])
    if not repo:
        raise WorkError(f"Could not parse repo from external_id={row['external_id']!r}.")

    # Local clone must already exist. We don't auto-clone — too much could go wrong.
    repo_local_name = repo.split("/")[-1]
    repo_path = repos_root / repo_local_name
    if not (repo_path / ".git").exists():
        raise WorkError(
            f"No local clone at {repo_path}. Clone it first:\n"
            f"  cd {repos_root} && git clone git@github.com:{repo}.git"
        )

    # Branch + worktree paths
    issue_number = row["external_id"].split("#", 1)[-1]
    slug = _slugify(row["title"] or "untitled")[:40]
    branch = f"scout/{issue_number}-{slug}"
    worktree_path = worktrees_root / f"{repo_local_name}-{issue_number}"

    if worktree_path.exists():
        raise WorkError(
            f"Worktree path already exists: {worktree_path}\n"
            f"Remove it first or check it out manually:\n"
            f"  cd {repo_path} && git worktree remove {worktree_path}"
        )

    worktrees_root.mkdir(parents=True, exist_ok=True)

    # Find the default branch — usually 'trunk' for wporg, 'main' otherwise
    default_branch = _resolve_default_branch(repo_path)

    # Create the worktree off the default branch with a new branch
    _run(
        ["git", "worktree", "add", "-b", branch, str(worktree_path), default_branch],
        cwd=repo_path,
    )

    # Write the context file
    context_path = worktree_path / "ISSUE.md"
    context_path.write_text(_render_context(row, repo, branch))

    return WorktreeSetup(
        item_id=item_id,
        repo=repo,
        repo_path=repo_path,
        worktree_path=worktree_path,
        branch=branch,
        context_path=context_path,
    )


def spawn_claude(setup: WorktreeSetup) -> int:
    """Spawn `claude` CLI in the worktree with the context preloaded.

    Requires Claude Code (`claude` binary) on PATH. Returns the exit code.
    pr-review-toolkit auto-triggers from ~/.claude/agents/ when it reviews.
    """
    if not shutil.which("claude"):
        raise WorkError(
            "`claude` CLI not found on PATH. Install Claude Code first, or "
            "open the worktree manually and start work."
        )
    # Hand off control entirely — Claude Code becomes the foreground process
    return subprocess.call(
        ["claude", "Read ISSUE.md and propose how you'd approach this fix."],
        cwd=setup.worktree_path,
    )


# --- helpers ----------------------------------------------------------------


def _repo_from_external_id(external_id: str) -> Optional[str]:
    """Pull 'WordPress/wporg-main-2022' out of 'WordPress/wporg-main-2022#123'."""
    if "#" not in external_id:
        return None
    return external_id.split("#", 1)[0]


def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "untitled"


def _resolve_default_branch(repo_path: Path) -> str:
    """Try to find the repo's default branch by reading origin/HEAD."""
    try:
        out = subprocess.check_output(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=repo_path,
            text=True,
        ).strip()
        # 'origin/trunk' -> 'trunk'
        return out.split("/", 1)[-1]
    except subprocess.CalledProcessError:
        # Fall back to common defaults — wporg-* uses 'trunk'
        for candidate in ("trunk", "main", "master"):
            try:
                subprocess.check_output(
                    ["git", "rev-parse", "--verify", candidate],
                    cwd=repo_path,
                    stderr=subprocess.DEVNULL,
                )
                return candidate
            except subprocess.CalledProcessError:
                continue
        raise WorkError(
            f"Could not resolve default branch in {repo_path}. "
            "Check `git branch -a` manually."
        )


def _run(cmd: list[str], cwd: Path) -> None:
    """Run a subprocess, surface stderr nicely on failure."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise WorkError(
            f"Command failed: {' '.join(cmd)}\n"
            f"  cwd: {cwd}\n"
            f"  stderr: {proc.stderr.strip()}"
        )


def _render_context(row: sqlite3.Row, repo: str, branch: str) -> str:
    labels = json.loads(row["labels_json"] or "[]")
    body = (row["body"] or "_(no body)_").strip()
    classification = row["classification"] or "(unclassified)"
    reason = row["classification_reason"] or ""
    confidence = row["classification_confidence"]
    confidence_str = f"{confidence:.2f}" if confidence is not None else "—"

    return f"""# {row['title']}

**Source:** {repo}
**Issue:** {row['url']}
**Author:** {row['author']}
**State:** {row['state']}
**Labels:** {', '.join(labels) if labels else '_(none)_'}
**Created:** {row['created_at']}
**Updated:** {row['updated_at']}

**Triage:** `{classification}` (confidence {confidence_str})
**Why this category:** {reason}

**Working branch:** `{branch}`

---

## Issue body

{body}

---

## Notes for Claude Code

This worktree was prepared by **wporg-scout**. The `pr-review-toolkit` agents
in `~/.claude/agents/` will auto-trigger when you ask for review.

Suggested workflow:
1. Read the issue body above and understand the ask.
2. Propose an approach — confirm with the human before making changes.
3. Make the edit. Use small commits.
4. Ask for `code-reviewer` (and `silent-failure-hunter` if you touched error handling).
5. When the human says ship, push the branch and open the PR.

Stop and ask the human if:
- The fix needs design judgment.
- It's not actually a code change (e.g., it's a content edit needing the WP block editor).
- The issue spans multiple repos or needs upstream coordination.
"""
