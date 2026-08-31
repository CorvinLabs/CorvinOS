#!/usr/bin/env bash
# autoupdate.sh — keep the plugin repo on the latest upstream RELEASE TAG.
#
# Tag-based, never branch-based: only tags matching `v*` (semver) are
# considered. Detached HEAD on the latest tag is the steady-state for a
# production install. Pushes between tags are NOT pulled in — release
# discipline lives at the tag layer.
#
# Hard skip rules (any one is enough):
#   - marker file `<repo>/.corvin/no-auto-update` exists (developer tree)
#   - voice config `autoupdate: false`
#   - working tree dirty (modified, staged, or untracked files)
#   - HEAD has commits not in the candidate tag (developer ahead of release)
#   - candidate tag is HEAD already (steady-state, no log spam)
#
# Soft skip (logged, non-fatal):
#   - git missing, fetch timeout, repo not a git tree, no tags found
#
# Usage:
#   autoupdate.sh           — synchronous: blocks until done. Used by bridge.sh.
#   autoupdate.sh --async   — fork-and-detach. Used by the SessionStart hook so
#                             it never delays Claude Code startup.
#
# Env knobs (test-only):
#   AUTOUPDATE_DRY_RUN=1    — log the planned checkout but don't run it
#   AUTOUPDATE_TAG_GLOB     — override the default `v*` tag glob

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=voice_lib.sh
source "$SCRIPT_DIR/voice_lib.sh"

if [[ "${1:-}" == "--async" ]]; then
  nohup bash "$0" </dev/null >>"$VOICE_LOG_FILE" 2>&1 &
  disown 2>/dev/null || true
  exit 0
fi

TAG_GLOB="${AUTOUPDATE_TAG_GLOB:-v*}"

# `autoupdate: false` in voice config disables this entirely. jq's `// empty`
# treats `false` as falsy and would silently fall through, so we use an
# explicit comparison.
ENABLED="$(jq -r 'if .autoupdate == false then "false" else "true" end' "$VOICE_CONFIG_FILE" 2>/dev/null || echo true)"
if [[ "$ENABLED" != "true" ]]; then
  voice_log "autoupdate: disabled in config, skipping"
  exit 0
fi

if ! command -v git >/dev/null 2>&1; then
  voice_log "autoupdate: git not installed, skipping"
  exit 0
fi

# scripts/ → voice/ → plugins/ → repo
PLUGIN_GUESS="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REPO_ROOT="$(git -C "$PLUGIN_GUESS" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" || ! -d "$REPO_ROOT/.git" ]]; then
  voice_log "autoupdate: $PLUGIN_GUESS not a git working tree, skipping"
  exit 0
fi

# Marker file — the operator's "hands off this tree" switch. Documented
# default for any developer install. Path is gitignored via .gitignore's
# `.corvin/` rule so it never accidentally lands in a release.
if [[ -f "$REPO_ROOT/.corvin/no-auto-update" ]]; then
  voice_log "autoupdate: $REPO_ROOT/.corvin/no-auto-update present, skipping (dev tree)"
  exit 0
fi

# Dirty tree → never touch. The user might have local edits — auto-checkout
# would either fail or trash uncommitted work, neither acceptable for an
# unattended hook.
DIRTY=0
git -C "$REPO_ROOT" diff --quiet --exit-code 2>/dev/null              || DIRTY=1
git -C "$REPO_ROOT" diff --cached --quiet --exit-code 2>/dev/null     || DIRTY=1
[[ -n "$(git -C "$REPO_ROOT" ls-files --others --exclude-standard 2>/dev/null)" ]] && DIRTY=1
if (( DIRTY )); then
  voice_log "autoupdate: $REPO_ROOT has local edits, skipping"
  exit 0
fi

voice_log "autoupdate: fetching tags (glob=$TAG_GLOB, repo=$REPO_ROOT)"
if ! timeout 8 git -C "$REPO_ROOT" fetch --tags --quiet --prune --prune-tags 2>/dev/null; then
  # `--prune-tags` is git 2.17+; retry without it if the host has older git.
  if ! timeout 8 git -C "$REPO_ROOT" fetch --tags --quiet 2>/dev/null; then
    voice_log "autoupdate: fetch failed or timed out, skipping"
    exit 0
  fi
fi

# Pick the highest semver-shaped tag. `--sort=-v:refname` orders v0.12.0 above
# v0.11.0 and orders v0.12.0 above v0.12.0-rc1, which is the right release-
# semantics default. Operators who want pre-releases set
# AUTOUPDATE_TAG_GLOB='v*-rc*' or similar.
LATEST_TAG="$(git -C "$REPO_ROOT" tag --list "$TAG_GLOB" --sort=-v:refname 2>/dev/null | head -1)"
if [[ -z "$LATEST_TAG" ]]; then
  voice_log "autoupdate: no tags matching $TAG_GLOB, skipping"
  exit 0
fi

LOCAL="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "")"
TAG_COMMIT="$(git -C "$REPO_ROOT" rev-parse "$LATEST_TAG^{commit}" 2>/dev/null || echo "")"
if [[ -z "$LOCAL" || -z "$TAG_COMMIT" ]]; then
  voice_log "autoupdate: could not resolve HEAD or tag commit, skipping"
  exit 0
fi

if [[ "$LOCAL" == "$TAG_COMMIT" ]]; then
  # Steady-state: detached HEAD on the latest tag. Don't log on every boot —
  # bridge.sh restart fires this on every restart and the log would spam.
  exit 0
fi

# Critical safety: if HEAD has commits not in the tag, this is a developer
# tree (or a tree on an old tag with patches on top). Either way, never
# checkout the tag — that would silently abandon those commits.
#
# `merge-base --is-ancestor HEAD TAG_COMMIT`:
#   exit 0 → HEAD is ancestor of TAG_COMMIT (HEAD strictly behind, ff-safe)
#   exit 1 → HEAD is NOT ancestor (HEAD has divergent commits, abort)
if ! git -C "$REPO_ROOT" merge-base --is-ancestor HEAD "$TAG_COMMIT" 2>/dev/null; then
  voice_log "autoupdate: HEAD has commits not in $LATEST_TAG (dev tree?), skipping"
  exit 0
fi

if [[ "${AUTOUPDATE_DRY_RUN:-0}" == "1" ]]; then
  voice_log "autoupdate: DRY_RUN — would checkout $LATEST_TAG ($(echo "$LOCAL" | cut -c1-7) → $(echo "$TAG_COMMIT" | cut -c1-7))"
  exit 0
fi

# `git checkout` to a tag puts us in detached HEAD on that tag's commit. That
# is the intended steady-state — release discipline lives at the tag layer.
# `--quiet` suppresses the detached-HEAD warning that would otherwise hit
# stdout on every restart.
if timeout 12 git -C "$REPO_ROOT" checkout --quiet "$LATEST_TAG" 2>/dev/null; then
  voice_log "autoupdate: checked out $LATEST_TAG ($(echo "$LOCAL" | cut -c1-7) → $(echo "$TAG_COMMIT" | cut -c1-7))"
else
  voice_log "autoupdate: checkout $LATEST_TAG failed, skipping"
fi

exit 0
