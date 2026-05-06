"""Load and validate config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULTS = {
    "github": {"repos": [], "labels": None},
    "trac": {
        "base_url": "https://core.trac.wordpress.org",
        "components": [],
        "status": ["new", "reopened"],
        "max_age_days": 365,
    },
    "make_p2": {"sites": [], "lookback_days": 30, "pre_filter_patterns": []},
    "classify": {"allowlist": []},
    "llm": {"provider": "openrouter", "model": "anthropic/claude-haiku-4.5"},
    "db_path": "./scout.db",
}


@dataclass
class Config:
    github_repos: list[str] = field(default_factory=list)
    github_labels: Optional[list[str]] = None
    trac_base_url: str = "https://core.trac.wordpress.org"
    trac_components: list[str] = field(default_factory=list)
    trac_statuses: list[str] = field(default_factory=lambda: ["new", "reopened"])
    trac_max_age_days: Optional[int] = 365
    p2_sites: list[str] = field(default_factory=list)
    p2_lookback_days: int = 30
    p2_pre_filter_patterns: list[str] = field(default_factory=list)
    # Classify allowlist: list of external_id prefixes. If non-empty, only items
    # whose external_id starts with one of these prefixes get LLM-classified.
    # Empty list (default) = classify everything that's been synced.
    # Examples: "WordPress/wporg-main-2022" (a single GitHub repo),
    #           "github" (all GitHub items), "trac" (all Trac tickets).
    classify_allowlist: list[str] = field(default_factory=list)
    llm_model: str = "anthropic/claude-haiku-4.5"
    db_path: str = "./scout.db"


def load(path: str | Path = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Copy config.example.yaml to {p.name} and edit it."
        )
    with p.open() as f:
        raw = yaml.safe_load(f) or {}

    gh = {**DEFAULTS["github"], **(raw.get("github") or {})}
    tr = {**DEFAULTS["trac"], **(raw.get("trac") or {})}
    p2 = {**DEFAULTS["make_p2"], **(raw.get("make_p2") or {})}
    cl = {**DEFAULTS["classify"], **(raw.get("classify") or {})}
    llm = {**DEFAULTS["llm"], **(raw.get("llm") or {})}

    return Config(
        github_repos=list(gh.get("repos") or []),
        github_labels=gh.get("labels"),
        trac_base_url=tr["base_url"],
        trac_components=list(tr.get("components") or []),
        trac_statuses=list(tr.get("status") or ["new", "reopened"]),
        trac_max_age_days=tr.get("max_age_days"),
        p2_sites=list(p2.get("sites") or []),
        p2_lookback_days=int(p2.get("lookback_days") or 30),
        p2_pre_filter_patterns=list(p2.get("pre_filter_patterns") or []),
        classify_allowlist=list(cl.get("allowlist") or []),
        llm_model=llm.get("model") or "anthropic/claude-haiku-4.5",
        db_path=raw.get("db_path") or "./scout.db",
    )
