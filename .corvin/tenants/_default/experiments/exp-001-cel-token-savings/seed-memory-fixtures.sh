#!/usr/bin/env bash
# Re-seed the EXP-001 memory fixtures into the CEL memory dir so suite-v4-memory-grounded
# is reproducible. Canonical copies live in data/memory-fixtures/. Remove again with:
#   rm ~/.claude/projects/-home-shumway-projects-CorvinOS/memory/bench-cel-*.md
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMDIR="$HOME/.claude/projects/-home-shumway-projects-CorvinOS/memory"
mkdir -p "$MEMDIR"
cp "$HERE"/data/memory-fixtures/bench-cel-*.md "$MEMDIR"/
echo "seeded $(ls "$HERE"/data/memory-fixtures/bench-cel-*.md | wc -l) fixtures into $MEMDIR"
