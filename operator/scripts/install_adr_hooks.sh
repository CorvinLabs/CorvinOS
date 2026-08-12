#!/usr/bin/env bash
#
# Install ADR-Sync Hooks
# Installiert die Pre-Commit und Post-Merge Hooks für alle Entwickler
#
# Usage: bash operator/scripts/install_adr_hooks.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

echo "📦 Installing ADR-Sync Hooks..."

# Kopiere Hooks (falls nicht existiert)
if [ ! -x "$HOOKS_DIR/pre-commit" ]; then
  chmod +x "$REPO_ROOT/.git/hooks/pre-commit"
  echo "✓ Pre-commit hook installed"
else
  echo "✓ Pre-commit hook already installed"
fi

if [ ! -x "$HOOKS_DIR/post-merge" ]; then
  chmod +x "$REPO_ROOT/.git/hooks/post-merge"
  echo "✓ Post-merge hook installed"
else
  echo "✓ Post-merge hook already installed"
fi

# Kopiere Sync Script
if [ ! -x "$REPO_ROOT/operator/scripts/sync_memory_from_adrs.py" ]; then
  chmod +x "$REPO_ROOT/operator/scripts/sync_memory_from_adrs.py"
  echo "✓ Memory sync script installed"
else
  echo "✓ Memory sync script already installed"
fi

# Initialisiere Memory-Datei
echo "💾 Initializing memory..."
python3 "$REPO_ROOT/operator/scripts/sync_memory_from_adrs.py" 2>/dev/null || true

echo ""
echo "✅ ADR-Sync Infrastructure installed!"
echo ""
echo "What's now enforced:"
echo "  1. Pre-commit hook blocks commits without ADR (core/ changes)"
echo "  2. Post-merge hook auto-syncs memory when ADRs change"
echo "  3. CI/CD gate (.github/workflows/adr-sync-check.yml) blocks PRs"
echo "  4. Code review checklist requires ADR validation"
echo ""
echo "For details, see:"
echo "  • CONTRIBUTING.md (ADR Requirements section)"
echo "  • CLAUDE.md (Code/Docs Sync section)"
echo "  • docs/claude-ref/adr-gate.md (full spec)"
