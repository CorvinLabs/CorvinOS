#!/usr/bin/env bash
# test_autoupdate.sh — per-subtask E2E for the tag-based autoupdate.
#
# Builds a sandbox repo with two release tags (v0.1.0, v0.2.0), points
# autoupdate.sh at it via PLUGIN_GUESS-equivalent layout, and exercises
# every documented skip path plus the happy path. Real `git`, real fs,
# no mocks (the repo IS the system under test).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOUPDATE_SH="$SCRIPT_DIR/autoupdate.sh"

GREEN='\033[1;32m'; RED='\033[1;31m'; CYAN='\033[1;36m'; NC='\033[0m'
fails=0
PASS() { printf "  ${GREEN}✓${NC} %s\n" "$*"; }
FAIL() { printf "  ${RED}✗${NC} %s\n" "$*"; fails=$((fails+1)); }
section() { printf "\n${CYAN}== %s ==${NC}\n" "$*"; }

# Build a sandbox repo whose layout matches PLUGIN_GUESS resolution:
#   <repo>/operator/voice/scripts/   ← autoupdate.sh runs from here
SANDBOX="$(mktemp -d -t corvin-autoupdate-XXXXXX)"
trap 'rm -rf "$SANDBOX"' EXIT

REPO="$SANDBOX/repo"
UPSTREAM="$SANDBOX/upstream.git"
SCRIPTS_LINK="$REPO/operator/voice/scripts"

setup_repo() {
  rm -rf "$REPO" "$UPSTREAM"
  git init --quiet --bare "$UPSTREAM"
  git init --quiet "$REPO"
  git -C "$REPO" config user.email "test@example.com"
  git -C "$REPO" config user.name  "Test"
  git -C "$REPO" config commit.gpgsign false
  mkdir -p "$SCRIPTS_LINK"
  # Symlink the real autoupdate.sh + voice_lib.sh into the sandbox so the
  # PLUGIN_GUESS walk-up (scripts → voice → plugins → repo) finds REPO.
  ln -sf "$AUTOUPDATE_SH"            "$SCRIPTS_LINK/autoupdate.sh"
  ln -sf "$SCRIPT_DIR/voice_lib.sh"  "$SCRIPTS_LINK/voice_lib.sh"
  # Hide the symlink scaffolding from `git status` so the dirty-tree check
  # sees a clean repo. Symlinks live in plugins/ purely so PLUGIN_GUESS
  # resolves correctly; they're not part of the test fixture's history.
  echo "plugins/" >> "$REPO/.git/info/exclude"
  echo "v1" > "$REPO/file.txt"
  git -C "$REPO" add file.txt
  git -C "$REPO" commit --quiet -m "v1"
  git -C "$REPO" tag v0.1.0
  git -C "$REPO" remote add origin "$UPSTREAM"
  git -C "$REPO" push --quiet origin master --tags 2>/dev/null \
    || git -C "$REPO" push --quiet origin main --tags 2>/dev/null
}

# Add a v0.2.0 commit + tag to the upstream (simulates a release that the
# local checkout has not seen yet).
add_upstream_release() {
  local tmp="$SANDBOX/upstream-clone"
  rm -rf "$tmp"
  git clone --quiet "$UPSTREAM" "$tmp"
  git -C "$tmp" config user.email "rel@example.com"
  git -C "$tmp" config user.name  "Release"
  git -C "$tmp" config commit.gpgsign false
  echo "v2" >> "$tmp/file.txt"
  git -C "$tmp" add file.txt
  git -C "$tmp" commit --quiet -m "v2 release"
  git -C "$tmp" tag v0.2.0
  git -C "$tmp" push --quiet origin HEAD --tags
  rm -rf "$tmp"
}

# Run autoupdate via the sandbox layout. voice_lib.sh resolves
# VOICE_CONFIG_DIR=$XDG_CONFIG_HOME/corvin-voice, so we point XDG there and
# read the log file at the same path.
run_au() {
  local cfg_dir="$SANDBOX/.config/corvin-voice"
  mkdir -p "$cfg_dir"
  : > "$cfg_dir/voice.log"
  XDG_CONFIG_HOME="$SANDBOX/.config" \
    HOME="$SANDBOX" \
    bash "$SCRIPTS_LINK/autoupdate.sh" "$@"
  cat "$cfg_dir/voice.log" 2>/dev/null
}

assert_log_contains() {
  local needle="$1" log="$2" desc="$3"
  if grep -qF -- "$needle" <<<"$log"; then
    PASS "$desc"
  else
    FAIL "$desc — log was: $log"
  fi
}

assert_log_not_contains() {
  local needle="$1" log="$2" desc="$3"
  if grep -qF -- "$needle" <<<"$log"; then
    FAIL "$desc — log was: $log"
  else
    PASS "$desc"
  fi
}

assert_head_at() {
  local expect="$1" desc="$2"
  local head_sha tag_sha
  head_sha="$(git -C "$REPO" rev-parse HEAD)"
  tag_sha="$(git -C "$REPO" rev-parse "$expect^{commit}")"
  if [[ "$head_sha" == "$tag_sha" ]]; then
    PASS "$desc"
  else
    FAIL "$desc — HEAD=$head_sha expected=$tag_sha ($expect)"
  fi
}

# ─────────────────────────────── case 1 ───────────────────────────────
section "case 1: behind tag → checkout latest"
setup_repo
add_upstream_release
# REPO is on v0.1.0 (master), upstream has v0.2.0
assert_head_at v0.1.0 "precondition: HEAD on v0.1.0"
log="$(run_au)"
assert_log_contains "fetching tags" "$log" "fetched tags"
assert_log_contains "checked out v0.2.0" "$log" "logged checkout"
assert_head_at v0.2.0 "HEAD now on v0.2.0"

# ─────────────────────────────── case 2 ───────────────────────────────
section "case 2: idempotent (already on latest tag)"
log="$(run_au)"
# Already on v0.2.0 → silent steady-state, no log line about checkout.
assert_log_not_contains "checked out" "$log" "no spurious checkout"
assert_head_at v0.2.0 "HEAD still on v0.2.0"

# ─────────────────────────────── case 3 ───────────────────────────────
section "case 3: marker file blocks update"
setup_repo
add_upstream_release
mkdir -p "$REPO/.corvin"
: > "$REPO/.corvin/no-auto-update"
log="$(run_au)"
assert_log_contains "no-auto-update" "$log" "marker logged"
assert_log_not_contains "checked out" "$log" "no checkout"
assert_head_at v0.1.0 "HEAD unchanged"

# ─────────────────────────────── case 4 ───────────────────────────────
section "case 4: dirty tree (untracked file) blocks update"
setup_repo
add_upstream_release
echo "scratch" > "$REPO/scratch.txt"
log="$(run_au)"
assert_log_contains "local edits" "$log" "dirty logged"
assert_log_not_contains "checked out" "$log" "no checkout"
assert_head_at v0.1.0 "HEAD unchanged"

# ─────────────────────────────── case 5 ───────────────────────────────
section "case 5: dirty tree (modified tracked file) blocks update"
setup_repo
add_upstream_release
echo "edit" >> "$REPO/file.txt"
log="$(run_au)"
assert_log_contains "local edits" "$log" "dirty (modified) logged"
assert_head_at v0.1.0 "HEAD unchanged"

# ─────────────────────────────── case 6 ───────────────────────────────
section "case 6: HEAD ahead of latest tag (dev tree) blocks update"
setup_repo
add_upstream_release
# Add a local commit on top of v0.2.0 first, so HEAD is genuinely ahead of
# every tag (including the upstream-only v0.2.0).
git -C "$REPO" fetch --quiet --tags origin 2>/dev/null
git -C "$REPO" checkout --quiet v0.2.0
echo "local-dev" >> "$REPO/file.txt"
git -C "$REPO" add file.txt
git -C "$REPO" -c user.email=dev@x -c user.name=Dev commit --quiet -m "local dev work"
ahead_sha="$(git -C "$REPO" rev-parse HEAD)"
log="$(run_au)"
assert_log_contains "HEAD has commits not in" "$log" "ahead-of-tag logged"
assert_log_not_contains "checked out v" "$log" "no checkout"
new_sha="$(git -C "$REPO" rev-parse HEAD)"
[[ "$ahead_sha" == "$new_sha" ]] && PASS "HEAD unchanged (dev commit preserved)" \
  || FAIL "HEAD changed: $ahead_sha → $new_sha"

# ─────────────────────────────── case 7 ───────────────────────────────
section "case 7: autoupdate disabled in voice config"
setup_repo
add_upstream_release
mkdir -p "$SANDBOX/.config/corvin-voice"
echo '{"autoupdate": false}' > "$SANDBOX/.config/corvin-voice/config.json"
log="$(run_au)"
assert_log_contains "disabled in config" "$log" "config disable logged"
assert_log_not_contains "checked out" "$log" "no checkout"
assert_head_at v0.1.0 "HEAD unchanged"
rm -f "$SANDBOX/.config/corvin-voice/config.json"
# Make sure the config-disable file is gone for subsequent cases — run_au's
# voice.log truncate doesn't touch config.json.

# ─────────────────────────────── case 8 ───────────────────────────────
section "case 8: no tags upstream → skip silently"
setup_repo
git -C "$REPO" tag -d v0.1.0 >/dev/null 2>&1 || true
git -C "$REPO" push --delete origin v0.1.0 2>/dev/null || true
log="$(run_au)"
assert_log_contains "no tags matching" "$log" "no-tags logged"
# HEAD shouldn't change either way.

# ─────────────────────────────── case 9 ───────────────────────────────
section "case 9: dry-run logs but doesn't checkout"
setup_repo
add_upstream_release
AUTOUPDATE_DRY_RUN=1 log="$(AUTOUPDATE_DRY_RUN=1 run_au)"
assert_log_contains "DRY_RUN" "$log" "dry-run logged"
assert_log_contains "would checkout v0.2.0" "$log" "dry-run shows target tag"
assert_head_at v0.1.0 "HEAD unchanged in dry-run"

# ─────────────────────────────── case 10 ──────────────────────────────
section "case 10: --async returns immediately + spawns background"
setup_repo
add_upstream_release
t0="$(date +%s)"
run_au --async >/dev/null
t1="$(date +%s)"
elapsed=$((t1 - t0))
[[ "$elapsed" -lt 3 ]] && PASS "async returned in ${elapsed}s (<3s)" \
  || FAIL "async took ${elapsed}s (expected <3s)"
# Wait for the background to finish (a few seconds is plenty for a local repo).
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  head_now="$(git -C "$REPO" rev-parse HEAD)"
  tag_target="$(git -C "$REPO" rev-parse v0.2.0^{commit} 2>/dev/null || echo "")"
  [[ "$head_now" == "$tag_target" ]] && break
done
assert_head_at v0.2.0 "async eventually checked out v0.2.0"

# ─────────────────────────────── summary ──────────────────────────────
echo
if (( fails == 0 )); then
  printf "${GREEN}all autoupdate cases passed${NC}\n"
  exit 0
else
  printf "${RED}%d autoupdate case(s) failed${NC}\n" "$fails"
  exit 1
fi
