#!/bin/bash
# wporg-scout morning run — invoked by launchd at 6am daily.
# All output captured to digests/latest-run.log so failures are debuggable.
set -eu

cd "$HOME/repos/wporg-scout"
mkdir -p digests

LOG="digests/latest-run.log"
DIGEST_PATH="$HOME/Documents/Claude/Projects/WordPress.ORG Editing/wporg-scout-digest-latest.md"
DATED_NAME="digests/$(date +%Y-%m-%d).md"

if [ ! -f .venv/bin/activate ]; then
  echo "=== Run at $(date -Iseconds) ===" > "$LOG"
  echo "ERROR: .venv not found. From ~/repos/wporg-scout:" >> "$LOG"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >> "$LOG"
  exit 1
fi

source .venv/bin/activate

{
  echo "=== Run at $(date -Iseconds) ==="
  echo "PATH=$PATH"
  echo "PWD=$(pwd)"
  echo "python=$(which python)"
  echo ""
  echo "-> sync"
  python -m scout sync
  echo ""
  echo "-> classify"
  python -m scout classify
  echo ""
  echo "-> digest"
  python -m scout digest --out "$DIGEST_PATH"
  cp "$DIGEST_PATH" "$DATED_NAME"
  echo ""
  echo "DONE -- digest at $DIGEST_PATH"
} > "$LOG" 2>&1
