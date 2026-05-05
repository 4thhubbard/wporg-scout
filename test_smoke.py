"""Smoke test you can run on your machine after install.

Hits ONE small repo, classifies a couple items with rules-based (no API key
needed), prints the result. If this works, the full pipeline will work.

    python3 test_smoke.py
"""

from scout import db, classify
from scout.sources import github


def main() -> None:
    print("[1/3] testing imports + DB roundtrip…")
    with db.connect(":memory:") as conn:
        item = db.Item(
            source="github",
            external_id="test/repo#1",
            title="Test",
            body="hello",
            url="https://example.com",
        )
        rid = db.upsert(conn, item)
        db.mark_classified(conn, rid, "content-fix", "test", 0.9)
        assert db.get_item(conn, rid)["classification"] == "content-fix"
    print("     ✓ imports + db OK")

    print("[2/3] testing rules-based classifier…")
    result = classify.classify_item(
        title="Typo on requirements page",
        body="small wording fix",
        source="github",
        url="x",
        labels=[],
        model="rules",
    )
    print(f"     ✓ classified as: {result.label} (reason: {result.reason})")

    print("[3/3] testing live GitHub fetch (1 small repo)…")
    try:
        items = list(github.fetch_repo_issues("WordPress/wporg-pattern-directory"))
        print(f"     ✓ fetched {len(items)} open issues")
        for item in items[:3]:
            print(f"       - {item.external_id}: {item.title[:80]}")
    except Exception as e:
        print(f"     ! GitHub fetch failed: {e}")
        print("       (check GITHUB_TOKEN env var if rate-limited)")

    print("\nIf all three steps passed, you're ready. Next:")
    print("  cp config.example.yaml config.yaml && edit it")
    print("  cp .env.example .env && fill in tokens")
    print("  python3 -m scout sync")
    print("  python3 -m scout classify")
    print("  python3 -m scout list --type ux-issue")


if __name__ == "__main__":
    main()
