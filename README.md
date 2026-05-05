# wporg-scout

A triage tool for WordPress.org work. Pulls open issues from across the
WordPress.org tracker landscape (GitHub Issues, Trac, Make P2s), classifies
each one, and gives you a ranked list of what's worth fixing next.

Built for the meta-janitors mandate: "fix what you find."

## Why this exists

Bug/idea reports for the WordPress.org property are scattered across:
- GitHub Issues on `WordPress/wporg-*` repos (newer code)
- `core.trac.wordpress.org` (most of WP core)
- Make P2 sites (`make.wordpress.org/community/`, `/design/`, `/meta/`, etc.)

There's no single place to ask "what's worth my time today?" — wporg-scout
pulls from all three, classifies each item, and lets you query.

## What it does

1. **Sync** — pulls open items from each configured source into a local SQLite DB
2. **Classify** — uses an LLM to label each item as one of:
   - `code-bug` — needs a code fix + PR
   - `content-fix` — copy/typo/link, fixable via the WP block editor
   - `ux-issue` — design or flow problem, needs design judgment
   - `feature-request` — new functionality proposal
   - `question` — user asking for help, not actionable
   - `meta` — tooling/process discussion
   - `unclear` — title+body alone don't tell us
3. **List** — query the DB by classification, source, repo, etc.

What it does NOT do (yet): spawn an agent to draft PRs. That's planned for v2.
v1 is triage-only — it tells you what's worth looking at, you decide what to do.

## Quick start

```bash
# 1. Install
pip3 install -r requirements.txt

# 2. Set env vars (see .env.example)
export GITHUB_TOKEN=ghp_...           # personal access token, public_repo scope
export OPENROUTER_API_KEY=sk-or-...   # for LLM classification

# 3. Configure what to track
cp config.example.yaml config.yaml
# edit config.yaml to add/remove repos, Trac queries, Make P2s

# 4. Run
python -m scout sync       # pulls from all sources into ./scout.db
python -m scout classify   # LLM-classifies new items
python -m scout list --type ux-issue --limit 20

# 5. Inspect a single item
python -m scout show 42
```

## Configuration

`config.yaml`:

```yaml
github:
  repos:
    - WordPress/wporg-main-2022
    - WordPress/wporg-news-2021
    - WordPress/wporg-learn
    - WordPress/wporg-pattern-directory
    - WordPress/wporg-translate
    # add more as needed

trac:
  base_url: https://core.trac.wordpress.org
  # focus on dotorg-property components, not core dev
  components:
    - "Site"
    - "Forums"
    - "Login & Authentication"

make_p2:
  sites:
    - https://make.wordpress.org/community/
    - https://make.wordpress.org/design/
    - https://make.wordpress.org/meta/
  lookback_days: 30

llm:
  provider: openrouter
  model: anthropic/claude-haiku-4.5  # cheap, fast, good enough for classification
```

## Why Python (not PHP like Scout)

- Cleaner LLM SDKs
- Built-in `sqlite3`
- Trivial to deploy as a CLI
- Mary already runs Python locally for the RSM pipeline

If you want to call this from PHP later (e.g., to wire into a wporg admin
page), shell out to it.

## License + provenance

Inspired by Automattic's internal Scout (lessbloat / davemart-in) — same
problem shape, but reimplemented from scratch for the wp.org property and the
public ecosystem. No code copied.

MIT.
