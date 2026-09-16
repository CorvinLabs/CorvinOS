#!/usr/bin/env bash
#
# Point git at the VERSIONED hooks in this directory.
#
# Until 2026-09-16 the hooks lived only in .git/hooks/ — untracked, so a
# fresh clone had no ADR gate at all and no one else on the project ran
# one. `core.hooksPath` makes the tracked copies the active ones, which
# means a hook fix ships with the commit that makes it.
#
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git config core.hooksPath scripts/git-hooks
echo "✓ core.hooksPath = scripts/git-hooks"

for hook in scripts/git-hooks/*; do
  case "$hook" in *install.sh|*.md) continue ;; esac
  chmod +x "$hook"
  echo "  • $(basename "$hook")"
done

# .git/hooks is now ignored by git, but leaving stale copies there is
# confusing for anyone who looks. Name them, don't delete them.
if [ -d .git/hooks ]; then
  stale="$(find .git/hooks -maxdepth 1 -type f ! -name '*.sample' 2>/dev/null || true)"
  if [ -n "$stale" ]; then
    echo
    echo "Note: these copies in .git/hooks are now INACTIVE and can be removed:"
    printf '  %s\n' $stale
  fi
fi
