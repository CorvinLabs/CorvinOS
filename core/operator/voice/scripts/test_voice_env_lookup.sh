#!/usr/bin/env bash
# E2E-test for voice_lib.sh OPENAI key lookup + engine detection.
#
# Regression: on 2026-05-04 the corvinOS fork was created in
# <corvinOS-root>/, while the only OPENAI .env stayed
# behind in <corvin-voice-skill>/. The Stop-Hook's
# walk-up search did not reach the sibling repo, the engine fell back
# to "none", and Claude Code went silent without a useful log line.
#
# This test rebuilds that scenario in a tempdir sandbox and asserts:
#   - canonical $VOICE_CONFIG_DIR/.env is the first candidate
#   - service.env is also picked up
#   - a key in a *sibling* repo (out of walk-up range) is correctly NOT found
#   - missing-key case logs WHICH paths were searched
#   - engine detection picks openai when a valid key is reachable

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$SCRIPT_DIR/voice_lib.sh"

if [[ ! -f "$LIB" ]]; then
  echo "FATAL: $LIB not found" >&2
  exit 1
fi

PASS=0
FAIL=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    printf '  PASS  %s\n' "$label"
    PASS=$((PASS+1))
  else
    printf '  FAIL  %s\n        expected=%q\n        actual=%q\n' "$label" "$expected" "$actual"
    FAIL=$((FAIL+1))
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    printf '  PASS  %s\n' "$label"
    PASS=$((PASS+1))
  else
    printf '  FAIL  %s\n        looking for: %q\n        in: %q\n' "$label" "$needle" "$haystack"
    FAIL=$((FAIL+1))
  fi
}

# Run a snippet that sources voice_lib.sh in a fully sandboxed env.
# Args:  $1 = sandbox HOME, $2 = sandbox CWD, $3 = CLAUDE_PLUGIN_ROOT,
#        rest = additional `KEY=value` env injections, then `--`,
#        then the bash snippet to run.
in_sandbox() {
  local home="$1"; local cwd="$2"; local plugin_root="$3"; shift 3
  local extra=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    extra+=("$1"); shift
  done
  shift  # drop the --
  env -i \
    PATH="$PATH" \
    HOME="$home" \
    PWD="$cwd" \
    CLAUDE_PLUGIN_ROOT="$plugin_root" \
    XDG_CONFIG_HOME="$home/.config" \
    "${extra[@]}" \
    bash -c "cd '$cwd' && source '$LIB' && $*"
}

mk_sandbox() {
  local root="$1"
  mkdir -p "$root/home/.config/corvin-voice"
  mkdir -p "$root/repo/operator/voice/scripts"
  mkdir -p "$root/sibling-legacy"   # the Geschwister-Repo
  cp "$LIB" "$root/repo/operator/voice/scripts/voice_lib.sh"
}

write_env() {
  local file="$1" key="$2" val="$3"
  mkdir -p "$(dirname "$file")"
  printf '%s=%s\n' "$key" "$val" > "$file"
  chmod 600 "$file"
}

#-------------------------------------------------------------------#
# Case A: nothing anywhere → lookup must fail and log the search list
#-------------------------------------------------------------------#
echo
echo "Case A: no .env anywhere"
TMP=$(mktemp -d); mk_sandbox "$TMP"
out="$(in_sandbox "$TMP/home" "$TMP/repo" "$TMP/repo/operator/voice" -- '
  voice_load_openai_key
  echo "rc=$?"
  echo "key_set=${OPENAI_API_KEY:-EMPTY}"
')"
assert_contains "lookup returns non-zero" "rc=1" "$out"
assert_contains "no key exported" "key_set=EMPTY" "$out"
log_content="$(cat "$TMP/home/.config/corvin-voice/voice.log" 2>/dev/null || true)"
assert_contains "log mentions NOT FOUND"            "load_openai_key: NOT FOUND" "$log_content"
assert_contains "log lists canonical .env path"     "$TMP/home/.config/corvin-voice/.env" "$log_content"
assert_contains "log lists service.env path"        "$TMP/home/.config/corvin-voice/service.env" "$log_content"
rm -rf "$TMP"

#-------------------------------------------------------------------#
# Case B: canonical ~/.config/corvin-voice/.env wins
#-------------------------------------------------------------------#
echo
echo "Case B: canonical \$VOICE_CONFIG_DIR/.env exists"
TMP=$(mktemp -d); mk_sandbox "$TMP"
write_env "$TMP/home/.config/corvin-voice/.env" "OPENAI_API_KEY" "sk-canonical-stub"
out="$(in_sandbox "$TMP/home" "$TMP/repo" "$TMP/repo/operator/voice" -- '
  voice_load_openai_key
  echo "rc=$?"
  echo "key=${OPENAI_API_KEY:-EMPTY}"
')"
assert_contains "lookup succeeds" "rc=0" "$out"
assert_contains "key loaded"      "key=sk-canonical-stub" "$out"
rm -rf "$TMP"

#-------------------------------------------------------------------#
# Case C: OPENAI_APIKEY (without underscore) normalized to OPENAI_API_KEY
#-------------------------------------------------------------------#
echo
echo "Case C: OPENAI_APIKEY (no-underscore variant) is honored"
TMP=$(mktemp -d); mk_sandbox "$TMP"
write_env "$TMP/home/.config/corvin-voice/.env" "OPENAI_APIKEY" "sk-no-underscore"
out="$(in_sandbox "$TMP/home" "$TMP/repo" "$TMP/repo/operator/voice" -- '
  voice_load_openai_key
  echo "rc=$?"
  echo "key=${OPENAI_API_KEY:-EMPTY}"
')"
assert_contains "lookup succeeds" "rc=0" "$out"
assert_contains "key normalized"  "key=sk-no-underscore" "$out"
rm -rf "$TMP"

#-------------------------------------------------------------------#
# Case D: service.env is also a candidate
#-------------------------------------------------------------------#
echo
echo "Case D: service.env in \$VOICE_CONFIG_DIR contains the key"
TMP=$(mktemp -d); mk_sandbox "$TMP"
write_env "$TMP/home/.config/corvin-voice/service.env" "OPENAI_API_KEY" "sk-from-service-env"
out="$(in_sandbox "$TMP/home" "$TMP/repo" "$TMP/repo/operator/voice" -- '
  voice_load_openai_key
  echo "rc=$?"
  echo "key=${OPENAI_API_KEY:-EMPTY}"
')"
assert_contains "lookup succeeds via service.env" "rc=0" "$out"
assert_contains "key from service.env"            "key=sk-from-service-env" "$out"
rm -rf "$TMP"

#-------------------------------------------------------------------#
# Case E: Repo-fork regression — key in *sibling* legacy repo, NOT walk-up reachable
#-------------------------------------------------------------------#
echo
echo "Case E: Repo-fork bug — key only in sibling repo, NOT in walk-up path"
TMP=$(mktemp -d); mk_sandbox "$TMP"
# Simulate the May-2026 bug: the key lives in a sibling repo that the
# walk-up from corvinOS/operator/voice cannot reach.
write_env "$TMP/sibling-legacy/.env" "OPENAI_API_KEY" "sk-stranded-in-legacy-repo"
out="$(in_sandbox "$TMP/home" "$TMP/repo" "$TMP/repo/operator/voice" -- '
  voice_load_openai_key
  echo "rc=$?"
  echo "key=${OPENAI_API_KEY:-EMPTY}"
')"
assert_contains "sibling repo key correctly NOT found" "rc=1" "$out"
assert_contains "no key exported"                       "key=EMPTY" "$out"
# Now place the key at the canonical location AND keep the sibling .env present.
# The canonical location must win, the sibling stays unreachable.
write_env "$TMP/home/.config/corvin-voice/.env" "OPENAI_API_KEY" "sk-canonical-wins"
out="$(in_sandbox "$TMP/home" "$TMP/repo" "$TMP/repo/operator/voice" -- '
  voice_load_openai_key
  echo "rc=$?"
  echo "key=${OPENAI_API_KEY:-EMPTY}"
')"
assert_contains "canonical key wins after migration" "key=sk-canonical-wins" "$out"
rm -rf "$TMP"

#-------------------------------------------------------------------#
# Case F: voice_detect_engine returns "openai" when key + python+openai available
#-------------------------------------------------------------------#
echo
echo "Case F: engine=openai when key reachable + openai pkg installed"
if python3 -c "import openai" 2>/dev/null; then
  TMP=$(mktemp -d); mk_sandbox "$TMP"
  write_env "$TMP/home/.config/corvin-voice/.env" "OPENAI_API_KEY" "sk-engine-test"
  out="$(in_sandbox "$TMP/home" "$TMP/repo" "$TMP/repo/operator/voice" -- '
    printf "engine=%s" "$(voice_detect_engine)"
  ')"
  assert_eq "engine=openai" "engine=openai" "$out"
  rm -rf "$TMP"
else
  echo "  SKIP  python3 -c 'import openai' fails — skipping engine-openai assertion"
fi

#-------------------------------------------------------------------#
# Case G: engine=none with full diagnostic logging when nothing usable
#-------------------------------------------------------------------#
echo
echo "Case G: engine=none + diagnostic when no key and no fallback engine"
# We can't reliably test "none" if the host has piper/espeak/say in PATH.
# Strip PATH down to a known-minimal directory so command -v finds nothing.
TMP=$(mktemp -d); mk_sandbox "$TMP"
mkdir -p "$TMP/empty-bin"
out="$(env -i HOME="$TMP/home" PWD="$TMP/repo" PATH="$TMP/empty-bin:/usr/bin:/bin" \
  XDG_CONFIG_HOME="$TMP/home/.config" \
  CLAUDE_PLUGIN_ROOT="$TMP/repo/operator/voice" \
  bash -c "cd '$TMP/repo' && source '$LIB' && \
    if command -v piper >/dev/null 2>&1 || command -v espeak-ng >/dev/null 2>&1 || command -v say >/dev/null 2>&1; then
      echo SKIP_HOST_HAS_FALLBACK
    else
      printf 'engine=%s' \"\$(voice_detect_engine)\"
    fi
  ")"
case "$out" in
  SKIP_HOST_HAS_FALLBACK)
    echo "  SKIP  host has piper/espeak/say in /usr/bin — cannot strip them"
    ;;
  *)
    assert_eq "engine=none" "engine=none" "$out"
    log_content="$(cat "$TMP/home/.config/corvin-voice/voice.log" 2>/dev/null || true)"
    assert_contains "log mentions NONE diagnostic" "detect_engine: NONE" "$log_content"
    ;;
esac
rm -rf "$TMP"

#-------------------------------------------------------------------#
# Case K: TTS-key migration aus corvinOS-Silo nach XDG (Roadmap K)
#-------------------------------------------------------------------#
echo
echo "Case K: voice_migrate_legacy_silo_key picks up legacy <corvin_home>/voice/.env"
TMP=$(mktemp -d); mk_sandbox "$TMP"
SILO="$TMP/silo"
mkdir -p "$SILO/voice"
echo "OPENAI_API_KEY=sk-from-silo" > "$SILO/voice/.env"
# Canonical XDG file initially missing — migration must create it.
out="$(env -i HOME="$TMP/home" PWD="$TMP/repo" PATH="/usr/bin:/bin" \
  XDG_CONFIG_HOME="$TMP/home/.config" \
  CLAUDE_PLUGIN_ROOT="$TMP/repo/operator/voice" \
  CORVIN_HOME="$SILO" \
  bash -c "cd '$TMP/repo' && source '$LIB' && voice_migrate_legacy_silo_key && \
    cat '$TMP/home/.config/corvin-voice/.env' 2>/dev/null")"
assert_contains "canonical .env now contains migrated key" "sk-from-silo" "$out"

# Idempotency: running again does not append a second copy.
env -i HOME="$TMP/home" PWD="$TMP/repo" PATH="/usr/bin:/bin" \
  XDG_CONFIG_HOME="$TMP/home/.config" \
  CLAUDE_PLUGIN_ROOT="$TMP/repo/operator/voice" \
  CORVIN_HOME="$SILO" \
  bash -c "cd '$TMP/repo' && source '$LIB' && voice_migrate_legacy_silo_key" >/dev/null
key_count=$(grep -c "^OPENAI_API_KEY=" "$TMP/home/.config/corvin-voice/.env")
assert_eq "second run does not duplicate the line" "1" "$key_count"

# Negative control: existing canonical key wins, no migration when canonical
# already has a value.
TMP2=$(mktemp -d); mk_sandbox "$TMP2"
SILO2="$TMP2/silo"
mkdir -p "$SILO2/voice"
echo "OPENAI_API_KEY=sk-from-silo-newer" > "$SILO2/voice/.env"
mkdir -p "$TMP2/home/.config/corvin-voice"
echo "OPENAI_API_KEY=sk-canonical" > "$TMP2/home/.config/corvin-voice/.env"
env -i HOME="$TMP2/home" PWD="$TMP2/repo" PATH="/usr/bin:/bin" \
  XDG_CONFIG_HOME="$TMP2/home/.config" \
  CLAUDE_PLUGIN_ROOT="$TMP2/repo/operator/voice" \
  CORVIN_HOME="$SILO2" \
  bash -c "cd '$TMP2/repo' && source '$LIB' && voice_migrate_legacy_silo_key" >/dev/null
canonical_after="$(cat $TMP2/home/.config/corvin-voice/.env)"
assert_contains "existing canonical key not overwritten" "sk-canonical" "$canonical_after"
key_count2=$(grep -c "^OPENAI_API_KEY=" "$TMP2/home/.config/corvin-voice/.env")
assert_eq "no second silo append" "1" "$key_count2"
rm -rf "$TMP" "$TMP2"

#-------------------------------------------------------------------#
# Summary
#-------------------------------------------------------------------#
echo
echo "----------------------------------------"
echo "voice env-lookup tests: $PASS passed, $FAIL failed"
echo "----------------------------------------"
exit $(( FAIL > 0 ? 1 : 0 ))
