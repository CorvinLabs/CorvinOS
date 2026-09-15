#!/usr/bin/env bash
# Rebuild the console frontend and PROVE the running host serves the new bundle.
#
# "Build succeeded" only means new code was added — never that the old code is
# gone, and never that the host is handing it out. This script therefore does not
# stop at the build: it re-reads index.html over HTTP from the live console and
# fails if the hashes on the wire are not the ones just produced.
#
# Usage:
#   scripts/console-deploy.sh                 # clean rebuild + verify
#   scripts/console-deploy.sh --fast          # incremental (watcher path)
#   scripts/console-deploy.sh --marker 'Foo'  # additionally assert a string is bundled
#
# Exit codes: 0 live · 1 build failed · 2 served bundle != built bundle
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_NEXT="$REPO_ROOT/core/console/corvin_console/web-next"
CONSOLE_URL="${CORVIN_CONSOLE_URL:-http://127.0.0.1:8765}"

FAST=0
MARKER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fast) FAST=1; shift ;;
    --marker) MARKER="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

cd "$WEB_NEXT" || { echo "web-next not found: $WEB_NEXT" >&2; exit 1; }

# Serialise deploys. Two concurrent runs USED TO DELETE dist/ outright and
# leave it deleted (console 404, observed 2026-09-15 00:46): B's opening
# `rm -rf dist.next` removes A's finished staging dir, then A's
# `mv dist dist.prev` succeeds and its `mv dist.next dist` fails with
# nothing to put back. There is no `set -e`, so A carried on and exited
# through the "no entry bundle" branch having destroyed the live build.
#
# Concurrency is the normal case here, not an edge case: the watcher
# service, the PostToolUse hook and a hand-run deploy all call this script,
# and the hook's `pgrep` guard is itself a race. An exclusive lock makes the
# second caller wait for the first instead of interleaving with it.
exec 9>".console-deploy.lock"
if ! flock -w 300 9; then
  echo "another console-deploy is holding the lock (>300s) — giving up" >&2
  exit 1
fi

if [ "$FAST" -eq 0 ]; then
  # A plain rebuild is not sufficient: a stale esbuild pre-bundle can keep
  # serving a module that no longer exists in source.
  rm -rf node_modules/.vite/
fi

# Build into a STAGING directory rather than over dist/.
#
# vite empties its outDir before writing, so building straight into dist/ leaves
# the console serving nothing for the length of the build — measured at ~13s,
# during which /console/ hands back no bundle at all. This host runs the
# operator's live chat; a redeploy must not take it down. Staging plus a
# directory swap shrinks that window to two renames.
STAGE="dist.next"
rm -rf "$STAGE"

# --fast trades terser for esbuild minification (~40s -> ~24s). The output is
# functionally identical and still type-checked; only the compression is looser,
# which matters for a shipped release and not for a local redeploy loop.
BUILD_LOG="$(mktemp)"
if [ "$FAST" -eq 1 ]; then
  BUILD_CMD=(sh -c "./node_modules/.bin/tsc -b && ./node_modules/.bin/vite build --minify esbuild --outDir $STAGE")
else
  BUILD_CMD=(sh -c "./node_modules/.bin/tsc -b && ./node_modules/.bin/vite build --outDir $STAGE")
fi
if ! "${BUILD_CMD[@]}" >"$BUILD_LOG" 2>&1; then
  echo "BUILD FAILED" >&2
  tail -30 "$BUILD_LOG" >&2
  rm -f "$BUILD_LOG"
  rm -rf "$STAGE"
  exit 1
fi
rm -f "$BUILD_LOG"

if [ ! -f "$STAGE/index.html" ]; then
  echo "staging build produced no index.html" >&2
  rm -rf "$STAGE"
  exit 1
fi

# Swap. dist.prev is kept as the rollback copy until the next deploy.
#
# The window between the two `mv`s is the only moment dist/ does not exist.
# If the second one fails there, the console is left with NO build and every
# /console/ request 404s until someone notices — so roll the previous build
# back in rather than exiting with the site down.
rm -rf dist.prev
[ -d dist ] && mv dist dist.prev
if ! mv "$STAGE" dist; then
  echo "swap failed: could not move $STAGE into place" >&2
  if [ -d dist.prev ] && [ ! -d dist ]; then
    mv dist.prev dist && echo "rolled back to the previous build — console still serving" >&2
  fi
  exit 1
fi

BUILT="$(grep -o 'assets/index-[^"]*\.js' dist/index.html | head -1)"
if [ -z "$BUILT" ]; then
  echo "no entry bundle in dist/index.html — build produced nothing usable" >&2
  exit 1
fi

if [ -n "$MARKER" ]; then
  if ! grep -rq -- "$MARKER" dist/assets/; then
    echo "marker not in bundle: $MARKER" >&2
    exit 2
  fi
fi

# The proof that matters: what the HOST hands out, not what is on disk.
SERVED="$(curl -fsS --max-time 10 "$CONSOLE_URL/console/" 2>/dev/null \
          | grep -o 'assets/index-[^"]*\.js' | head -1)"

if [ -z "$SERVED" ]; then
  echo "built $BUILT — console at $CONSOLE_URL is not reachable, cannot verify" >&2
  exit 2
fi

if [ "$SERVED" != "$BUILT" ]; then
  echo "STALE: built $BUILT but $CONSOLE_URL serves $SERVED" >&2
  echo "If the console booted while dist/ was absent it registered the 503" >&2
  echo "fallback route and only a restart recovers it:" >&2
  echo "  systemctl --user restart corvin-webui" >&2
  exit 2
fi

echo "LIVE $BUILT"
