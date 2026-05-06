"""CLI entrypoint: `python -m scout <command>`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from scout import classify as classifier
from scout import config as config_module
from scout import db
from scout import digest as digest_module
from scout import work as work_module
from scout.sources import github, make_p2, trac

load_dotenv()  # pulls GITHUB_TOKEN, OPENROUTER_API_KEY from .env if present


def _matches_allowlist(external_id: str, allowlist: list[str]) -> bool:
    """An empty allowlist means 'allow everything'. Otherwise prefix-match."""
    if not allowlist:
        return True
    return any(external_id.startswith(prefix) for prefix in allowlist)


@click.group()
@click.option(
    "--config",
    "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to config file.",
)
@click.pass_context
def cli(ctx: click.Context, config_path: str) -> None:
    """wporg-scout — triage tool for WordPress.org work."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = config_module.load(config_path)


@cli.command()
@click.option(
    "--source",
    type=click.Choice(["github", "trac", "make_p2", "all"]),
    default="all",
    show_default=True,
    help="Which source(s) to sync.",
)
@click.pass_context
def sync(ctx: click.Context, source: str) -> None:
    """Pull open items from each configured source into the local DB."""
    cfg = ctx.obj["config"]
    counts = {"github": 0, "trac": 0, "make_p2": 0}

    with db.connect(cfg.db_path) as conn:
        if source in ("github", "all") and cfg.github_repos:
            click.echo(f"→ github: pulling from {len(cfg.github_repos)} repo(s)…")
            for item in github.fetch_all(cfg.github_repos, labels=cfg.github_labels):
                db.upsert(conn, item)
                counts["github"] += 1

        if source in ("trac", "all") and cfg.trac_components:
            click.echo(f"→ trac: pulling from {cfg.trac_base_url}…")
            for item in trac.fetch_all(
                cfg.trac_base_url,
                components=cfg.trac_components,
                statuses=cfg.trac_statuses,
                max_age_days=cfg.trac_max_age_days,
            ):
                db.upsert(conn, item)
                counts["trac"] += 1

        if source in ("make_p2", "all") and cfg.p2_sites:
            click.echo(f"→ make_p2: pulling from {len(cfg.p2_sites)} site(s)…")
            for item in make_p2.fetch_all(
                cfg.p2_sites,
                lookback_days=cfg.p2_lookback_days,
                pre_filter_patterns=cfg.p2_pre_filter_patterns,
            ):
                db.upsert(conn, item)
                counts["make_p2"] += 1

    total = sum(counts.values())
    click.echo(f"✓ synced {total} items: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


@cli.command()
@click.option(
    "--limit",
    default=100,
    show_default=True,
    help="Max number of unclassified items to process this run.",
)
@click.pass_context
def classify(ctx: click.Context, limit: int) -> None:
    """Run the LLM classifier on un-classified items.

    If `classify.allowlist` is set in config, items outside the allowlist are
    skipped — they stay in the DB but don't get an LLM call. Useful for
    focusing classification on the repos/sources you actually care about.
    """
    cfg = ctx.obj["config"]
    allowlist = cfg.classify_allowlist
    with db.connect(cfg.db_path) as conn:
        # Pull a generous batch so the post-allowlist filter still has options
        fetch_limit = limit * 5 if allowlist else limit
        rows = db.unclassified(conn, limit=fetch_limit)
        if allowlist:
            rows = [r for r in rows if _matches_allowlist(r["external_id"], allowlist)]
            rows = rows[:limit]
            click.echo(
                f"→ classify allowlist active ({len(allowlist)} prefix(es)): "
                + ", ".join(allowlist)
            )

        if not rows:
            click.echo("Nothing to classify.")
            return
        click.echo(f"→ classifying {len(rows)} item(s) with model={cfg.llm_model}…")
        for row in rows:
            labels = json.loads(row["labels_json"] or "[]")
            result = classifier.classify_item(
                title=row["title"],
                body=row["body"],
                source=row["source"],
                url=row["url"],
                labels=labels,
                model=cfg.llm_model,
            )
            db.mark_classified(
                conn, row["id"], result.label, result.reason, result.confidence
            )
            click.echo(
                f"  #{row['id']:>5} [{result.label:<16}] {row['title'][:80]}"
            )
    click.echo("✓ done")


@cli.command(name="list")
@click.option("--type", "classification", type=click.Choice(db.CLASSIFICATIONS), help="Filter by classification.")
@click.option("--source", type=click.Choice(["github", "trac", "make_p2"]), help="Filter by source.")
@click.option("--limit", default=50, show_default=True)
@click.pass_context
def list_cmd(ctx: click.Context, classification: str | None, source: str | None, limit: int) -> None:
    """Show triaged items, optionally filtered."""
    cfg = ctx.obj["config"]
    with db.connect(cfg.db_path) as conn:
        rows = db.list_items(conn, classification=classification, source=source, limit=limit)
        if not rows:
            click.echo("No items match.")
            return
        for row in rows:
            cls = row["classification"] or "(unclassified)"
            conf = row["classification_confidence"]
            conf_str = f"{conf:.2f}" if conf is not None else "—"
            click.echo(
                f"#{row['id']:>5} [{cls:<16}] [{row['source']:<8}] [{conf_str}] {row['title'][:75]}"
            )
            click.echo(f"        {row['url']}")


@cli.command()
@click.argument("item_id", type=int)
@click.pass_context
def show(ctx: click.Context, item_id: int) -> None:
    """Show full details for one item."""
    cfg = ctx.obj["config"]
    with db.connect(cfg.db_path) as conn:
        row = db.get_item(conn, item_id)
        if not row:
            click.echo(f"No item with id {item_id}", err=True)
            sys.exit(1)
        click.echo(f"#{row['id']}  [{row['classification'] or 'unclassified'}]  source={row['source']}")
        click.echo(f"Title:  {row['title']}")
        click.echo(f"URL:    {row['url']}")
        click.echo(f"State:  {row['state']}")
        click.echo(f"Author: {row['author']}")
        click.echo(f"Labels: {', '.join(json.loads(row['labels_json'] or '[]')) or '(none)'}")
        click.echo(f"Created:  {row['created_at']}")
        click.echo(f"Updated:  {row['updated_at']}")
        click.echo(f"Fetched:  {row['fetched_at']}")
        if row["classification"]:
            click.echo(f"\nClassification: {row['classification']} (confidence {row['classification_confidence']:.2f})")
            click.echo(f"Reason: {row['classification_reason']}")
        if row["body"]:
            click.echo("\n--- body ---")
            click.echo(row["body"][:2000])


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Print counts by source and classification."""
    cfg = ctx.obj["config"]
    with db.connect(cfg.db_path) as conn:
        s = db.stats(conn)
    click.echo(f"Total items: {s['total']}")
    click.echo("\nBy source:")
    for k, v in sorted(s["by_source"].items()):
        click.echo(f"  {k:<10} {v}")
    click.echo("\nBy classification:")
    for k, v in sorted(s["by_classification"].items()):
        click.echo(f"  {k:<18} {v}")


@cli.command()
@click.option(
    "--top",
    default=digest_module.TOP_PER_CATEGORY,
    show_default=True,
    help="Items to show per category.",
)
@click.option(
    "--out",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write to this file instead of stdout.",
)
@click.pass_context
def digest(ctx: click.Context, top: int, out: str | None) -> None:
    """Render the triage queue as a markdown digest."""
    cfg = ctx.obj["config"]
    with db.connect(cfg.db_path) as conn:
        text = digest_module.render(conn, top_per_category=top)
    if out:
        Path(out).write_text(text)
        click.echo(f"✓ wrote digest to {out}")
    else:
        click.echo(text)


@cli.command()
@click.argument("item_id", type=int)
@click.option(
    "--auto",
    is_flag=True,
    help="Spawn `claude` CLI in the worktree once it's set up.",
)
@click.pass_context
def work(ctx: click.Context, item_id: int, auto: bool) -> None:
    """Set up a worktree + branch + context for an item, ready for Claude Code."""
    cfg = ctx.obj["config"]
    with db.connect(cfg.db_path) as conn:
        try:
            setup = work_module.setup_worktree(conn, item_id)
        except work_module.WorkError as e:
            click.echo(f"✗ {e}", err=True)
            sys.exit(1)

    click.echo(f"✓ worktree ready: {setup.worktree_path}")
    click.echo(f"  branch:  {setup.branch}")
    click.echo(f"  context: {setup.context_path}")
    click.echo(f"  repo:    {setup.repo}")

    if auto:
        click.echo("\n→ spawning claude…")
        try:
            rc = work_module.spawn_claude(setup)
            sys.exit(rc)
        except work_module.WorkError as e:
            click.echo(f"✗ {e}", err=True)
            sys.exit(1)
    else:
        click.echo("\nNext step:")
        click.echo(f"  cd {setup.worktree_path} && claude")


if __name__ == "__main__":
    cli(obj={})
