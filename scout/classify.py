"""Classify items using an LLM (default: OpenRouter / Claude Haiku).

Falls back to a tiny rules-based classifier if `llm.model: rules` is set in
config or if no API key is available — useful for offline runs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

from scout.db import CLASSIFICATIONS

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

CLASSIFY_PROMPT = """You triage open issues for the WordPress.org property. Each issue comes from one of:
- a public WordPress GitHub repo (wporg-main-2022, wporg-news-2021, wporg-learn, etc.)
- core.trac.wordpress.org (a Trac ticket)
- a Make WordPress P2 site (community, design, meta)

Classify the issue into EXACTLY ONE of these labels:

- code-bug: Requires a code fix and a PR. Bugs in PHP/JS/CSS, broken behavior, regressions.
- content-fix: Copy/typo/link/wording fix. Fixable via the WP block editor or a small markdown/template change. No real logic.
- ux-issue: Design or flow problem — confusing layout, bad information architecture, missing affordance. Needs design judgment.
- feature-request: Proposal for new functionality that doesn't yet exist.
- question: Someone asking for help or clarification, not actionable as a fix.
- meta: Discussion of tooling, process, governance, or coordination — not a fixable item.
- unclear: The title and body together don't give enough signal to classify confidently.

Reply with ONLY a JSON object on a single line, no prose, no code fences:
{"classification": "<label>", "reason": "<one short sentence>", "confidence": <0.0-1.0>}

Issue source: %SOURCE%
Issue URL: %URL%
Title: %TITLE%
Labels: %LABELS%

Body (may be empty):
%BODY%
"""


@dataclass
class Classification:
    label: str
    reason: str
    confidence: float


def classify_item(
    title: str,
    body: Optional[str],
    source: str,
    url: str,
    labels: list[str],
    model: str = "anthropic/claude-haiku-4.5",
    api_key: Optional[str] = None,
) -> Classification:
    """Classify a single item. Falls back to rules-based if no API key."""
    if model == "rules" or not (api_key or os.environ.get("OPENROUTER_API_KEY")):
        return _rules_classify(title, body, labels)

    return _llm_classify(title, body, source, url, labels, model, api_key)


def _llm_classify(
    title: str,
    body: Optional[str],
    source: str,
    url: str,
    labels: list[str],
    model: str,
    api_key: Optional[str],
) -> Classification:
    api_key = api_key or os.environ["OPENROUTER_API_KEY"]
    body_text = (body or "").strip()
    # Cap body length so very long issue descriptions don't blow up tokens
    if len(body_text) > 4000:
        body_text = body_text[:4000] + "\n\n[... truncated ...]"

    prompt = (
        CLASSIFY_PROMPT.replace("%SOURCE%", source)
        .replace("%URL%", url)
        .replace("%TITLE%", title)
        .replace("%LABELS%", ", ".join(labels) if labels else "(none)")
        .replace("%BODY%", body_text or "(empty)")
    )

    try:
        r = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/4thhubbard/wporg-scout",
                "X-Title": "wporg-scout",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 200,
            },
            timeout=60,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        return _parse_response(text)
    except Exception as e:
        # Don't crash the whole batch — fall back to rules and log
        print(f"  ! llm classify failed ({e}) — using rules for this item")
        return _rules_classify(title, body, labels)


def _parse_response(text: str) -> Classification:
    """The model is instructed to return one-line JSON. Be defensive anyway."""
    # Strip code fences if the model included any
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch: pull out a label keyword by regex
        for label in CLASSIFICATIONS:
            if label in text:
                return Classification(label, "(could not parse model JSON)", 0.3)
        return Classification("unclear", "(model returned unparseable response)", 0.0)

    label = obj.get("classification") or obj.get("label") or "unclear"
    if label not in CLASSIFICATIONS:
        label = "unclear"
    reason = (obj.get("reason") or "").strip()[:240]
    try:
        confidence = float(obj.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    return Classification(label, reason, confidence)


# --- Rules fallback -----------------------------------------------------------
# Used when the LLM is offline / no API key. Far less accurate; just a stopgap.

_RULES = [
    # (regex, classification, reason)
    (r"(?i)\b(typo|wording|copy|grammar|spelling|broken link|404)\b", "content-fix", "matches content-fix keywords"),
    (r"(?i)\b(error|exception|crash|undefined|null|fatal|stack trace)\b", "code-bug", "matches code-bug keywords"),
    (r"(?i)\b(confusing|unclear|hard to find|hidden|hard to use|UX)\b", "ux-issue", "matches ux-issue keywords"),
    (r"(?i)\b(propose|proposal|new feature|add (a|the) ability)\b", "feature-request", "matches feature-request keywords"),
    (r"(?i)\b(how do (i|we)|why is|why does|can someone|help me)\b", "question", "looks like a question"),
    (r"(?i)\b(meeting|agenda|next steps|process|governance)\b", "meta", "looks like meta/process discussion"),
]


def _rules_classify(title: str, body: Optional[str], labels: list[str]) -> Classification:
    haystack = " ".join(filter(None, [title, body or "", " ".join(labels)]))
    for pattern, label, reason in _RULES:
        if re.search(pattern, haystack):
            return Classification(label, f"rules: {reason}", 0.4)
    return Classification("unclear", "rules: no patterns matched", 0.2)
