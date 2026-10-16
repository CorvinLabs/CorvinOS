#!/usr/bin/env bash
# Start a FRESH-INSTALL console/gateway backend for the plugin-lifecycle E2E.
#
# "Fresh" here means what a new operator has, not what this dev machine has:
#   * the code is a file export of the working tree (CORVIN_FRESH_SOURCE=head: of the
#     last commit) in an empty directory — and (the point) NO sibling ../Corvin-Marketplace
#     checkout, so bootstrap._marketplace_root() has nothing local and the
#     plugin SOURCE must come from GitHub (bootstrap.ensure_marketplace_source)
#     and the INDEX from raw.githubusercontent.com (routes/marketplace.py);
#   * CORVIN_HOME / XDG_CONFIG_HOME / the audit anchor key are empty
#     throwaway paths, so nothing touches the live install or its audit chain;
#   * CORVIN_MARKETPLACE_ROOT / CORVIN_MARKETPLACE_INDEX are UNSET — setting either
#     would short-circuit the GitHub path this test exists to prove.
#
# The SPA is the built bundle of the working tree (dist/ is untracked, so the
# archive has none) copied in; it is served at /console/ by the backend itself.
#
# Env: CORVIN_FRESH_ROOT (default /tmp/corvin-fresh-install), CORVIN_FRESH_PORT (8841).
set -uo pipefail
R=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)
ROOT="${CORVIN_FRESH_ROOT:-/tmp/corvin-fresh-install}"
PORT="${CORVIN_FRESH_PORT:-8841}"
WEB=core/console/corvin_console/web-next

# Never delete anything that is not a throwaway directory of ours.
case "$ROOT" in
  /tmp/corvin-fresh-install*|/tmp/corvin-e2e-fresh*) ;;
  *) echo "refusing CORVIN_FRESH_ROOT=$ROOT (must live under /tmp/corvin-fresh-install* or /tmp/corvin-e2e-fresh*)" >&2; exit 1 ;;
esac
rm -rf "$ROOT"
mkdir -p "$ROOT/CorvinOS" "$ROOT/home/tenants/_default/global" "$ROOT/xdg"
# Source: the WORKING TREE (tracked + untracked, not ignored) so the E2E can verify a change
# before it is committed; CORVIN_FRESH_SOURCE=head exports the last commit instead (CI).
# Either way it is a plain file export: no .git, and no sibling Corvin-Marketplace.
if [ "${CORVIN_FRESH_SOURCE:-worktree}" = "head" ]; then
  ( cd "$R" && git archive HEAD ) | tar -x -C "$ROOT/CorvinOS" || exit 1
else
  ( cd "$R" && git ls-files -z --cached --others --exclude-standard \
      | grep -zv '^corvin_decisions$' | while IFS= read -r -d '' f; do [ -e "$f" ] && printf '%s\0' "$f"; done \
      | tar --null -T - -cf - ) | tar -x -C "$ROOT/CorvinOS" || exit 1
fi
if [ ! -f "$R/$WEB/dist/index.html" ]; then
  echo "no built SPA at $R/$WEB/dist — run scripts/console-deploy.sh first" >&2
  exit 1
fi
mkdir -p "$ROOT/CorvinOS/$WEB"
cp -r "$R/$WEB/dist" "$ROOT/CorvinOS/$WEB/dist"

A="$ROOT/CorvinOS"
unset CORVIN_MARKETPLACE_ROOT CORVIN_MARKETPLACE_INDEX ANTHROPIC_API_KEY
# Pre-push check ONLY (opt-in): CORVIN_FRESH_MARKETPLACE_REF=<git ref of the Corvin-Marketplace
# checkout next to this repo> takes the plugin SOURCE from a `git archive` of that commit
# instead of GitHub, so a plugin change can be proven on a fresh install before it is
# pushed. Everything else stays fresh (empty home, index from GitHub). The release gate
# runs WITHOUT it.
if [ -n "${CORVIN_FRESH_MARKETPLACE_REF:-}" ]; then
  MP_SRC="$(cd "$R/.." && pwd)/Corvin-Marketplace"
  mkdir -p "$ROOT/marketplace"
  ( cd "$MP_SRC" && git archive "$CORVIN_FRESH_MARKETPLACE_REF" plugins ) | tar -x -C "$ROOT/marketplace" || exit 1
  export CORVIN_MARKETPLACE_ROOT="$ROOT/marketplace/plugins/buildin"
  echo "fresh backend: plugin source from Corvin-Marketplace@$CORVIN_FRESH_MARKETPLACE_REF (pre-push mode)" >&2
fi
export CORVIN_HOME="$ROOT/home"
export XDG_CONFIG_HOME="$ROOT/xdg"
export CORVIN_AUDIT_ANCHOR_KEY="$ROOT/home/audit-anchor.key"
# FORGE_ROOT stays UNSET: the boot tripwire refuses any redirect of the audit chain
# (audit_path_not_redirected), so the chain resolves from CORVIN_HOME alone.
unset FORGE_ROOT
export CORVIN_TENANT_ID=_default
export PYTHONPATH="$A:$A/core/console:$A/core/gateway:$A/core/license:$A/core/compliance:$A/core/plugins:$A/corvin_operator/forge:$A/corvin_operator/skill-forge"
# cwd = the throwaway home: not a git checkout, and no legacy in-repo index.
cd "$CORVIN_HOME" || exit 1
exec "$R/core/console/.venv/bin/python" -m uvicorn corvin_gateway.app:app \
  --host 127.0.0.1 --port "$PORT" --log-level info
