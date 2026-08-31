#!/usr/bin/env bash
# E2E-test for voice_lib.sh::voice_persona_voice — per-persona TTS voice
# resolver used by speak.sh when CORVIN_CALLER_PERSONA is set.
#
# Asserts:
#   - jarvis.json (bundle) carries tts_voice=echo → resolver returns "echo"
#     for both DE and EN
#   - empty persona / unknown persona → empty (graceful no-op fallback to
#     voice_cfg .voice_<lang>)
#   - lang-specific tts_voice_<lang> beats the lang-agnostic tts_voice
#     (used by personas that want different voices per language)
#   - user-override persona JSON (~/.config/claude-cowork/personas/...)
#     beats the bundle entry — operator can swap voices without forking
#     the bundle persona
#   - missing jq → graceful no-op (some hosts run TTS without jq)

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

TMP="$(mktemp -d -t voice-persona-voice-test.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

# Sandbox VOICE_CONFIG_DIR + CORVIN_HOME + HOME so a real user setup
# cannot leak into the test result.
export VOICE_CONFIG_DIR="$TMP/voice-config"
export CORVIN_HOME="$TMP/corvin"
export HOME="$TMP/home"
mkdir -p "$VOICE_CONFIG_DIR" "$CORVIN_HOME/cowork/personas" \
         "$HOME/.config/claude-cowork/personas"

# shellcheck disable=SC1090
. "$LIB"

# ── 1. User-scope persona with tts_voice ────────────────────────────
# (jarvis.json was removed from bundle in f1e3246; test via user scope)
echo "Case A: user-scope persona with tts_voice"
cat >"$CORVIN_HOME/cowork/personas/test-tts-persona.json" <<'JSON'
{ "name": "test-tts-persona", "tts_voice": "echo" }
JSON
out_de="$(voice_persona_voice "test-tts-persona" "de")"
assert_eq "test-tts-persona DE → echo (user scope)" "echo" "$out_de"
out_en="$(voice_persona_voice "test-tts-persona" "en")"
assert_eq "test-tts-persona EN → echo (user scope)" "echo" "$out_en"
rm "$CORVIN_HOME/cowork/personas/test-tts-persona.json"

# ── 2. Empty persona → graceful empty ───────────────────────────────
echo
echo "Case B: empty / missing args"
out_empty="$(voice_persona_voice "" "de")"
assert_eq "empty persona → empty"     "" "$out_empty"
out_unknown="$(voice_persona_voice "totally-made-up-persona-9000" "de")"
assert_eq "unknown persona → empty"   "" "$out_unknown"

# ── 3. Lang-specific tts_voice_<lang> beats lang-agnostic tts_voice ─
echo
echo "Case C: lang-specific override beats lang-agnostic"
cat >"$CORVIN_HOME/cowork/personas/bilingual.json" <<'JSON'
{
  "name": "bilingual",
  "tts_voice": "alloy",
  "tts_voice_de": "fable",
  "tts_voice_en": "echo"
}
JSON
out_de="$(voice_persona_voice "bilingual" "de")"
assert_eq "bilingual DE → fable (lang-specific wins)"  "fable" "$out_de"
out_en="$(voice_persona_voice "bilingual" "en")"
assert_eq "bilingual EN → echo (lang-specific wins)"   "echo"  "$out_en"
out_fr="$(voice_persona_voice "bilingual" "fr")"
assert_eq "bilingual FR → alloy (lang-agnostic fallback)" "alloy" "$out_fr"

# ── 4. User-override persona shadows another override ───────────────
# (jarvis.json removed from bundle in f1e3246; test user-scope priority)
echo
echo "Case D: user override shadows legacy dir"
cat >"$CORVIN_HOME/cowork/personas/test-priority.json" <<'JSON'
{ "name": "test-priority", "tts_voice": "nova" }
JSON
out="$(voice_persona_voice "test-priority" "de")"
assert_eq "user-override → nova (corvin_home wins)" "nova" "$out"

# Remove corvin_home override; legacy claude-cowork dir is the next candidate.
rm "$CORVIN_HOME/cowork/personas/test-priority.json"
cat >"$HOME/.config/claude-cowork/personas/test-priority.json" <<'JSON'
{ "name": "test-priority", "tts_voice": "shimmer" }
JSON
out="$(voice_persona_voice "test-priority" "de")"
assert_eq "legacy ~/.config user-override → shimmer (legacy dir)" "shimmer" "$out"

# Both gone → empty (no bundle persona with tts_voice for this name)
rm "$HOME/.config/claude-cowork/personas/test-priority.json"
out="$(voice_persona_voice "test-priority" "de")"
assert_eq "all overrides removed → empty (no bundle persona)" "" "$out"

# ── 5. Persona without tts_voice → empty (let global config decide) ─
echo
echo "Case E: persona without tts_voice falls through to global config"
# coder.json doesn't carry tts_voice today.
out="$(voice_persona_voice "coder" "de")"
assert_eq "coder → empty (no tts_voice in JSON)" "" "$out"

# ── 6. Missing jq → graceful empty (don't break TTS) ────────────────
echo
echo "Case F: missing jq → graceful no-op"
# Shadow jq with a non-existent name in PATH.
PATH_BACKUP="$PATH"
export PATH="/nonexistent-only-for-test"
out="$(voice_persona_voice "assistant" "de")"
assert_eq "no jq on PATH → empty (graceful)" "" "$out"
export PATH="$PATH_BACKUP"

# ── Summary ─────────────────────────────────────────────────────────
echo
echo "── Summary ─────────────────────────────────────────────"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
