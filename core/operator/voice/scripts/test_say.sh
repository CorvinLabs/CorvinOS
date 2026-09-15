#!/usr/bin/env bash
# test_say.sh — smoke-test for the standalone TTS helper used by the
# WhatsApp /welcome path.
#
# Provider chain: OpenAI (needs key) → edge-tts (keyless, needs internet) →
# Piper (keyless, offline, needs a downloaded model) → silent skip.
#
# Load-bearing invariant WITHOUT OPENAI_API_KEY: exit 0, and stdout is
# non-empty IFF a real audio file was written. The keyless edge/Piper tiers
# now produce audio with no key, so "empty stdout" only holds when NO tier is
# reachable (offline + no Piper model). The caller (whatsapp/daemon.js) treats
# empty stdout as "voice disabled, fall through to text-only".
#
# Usage args: <out_path> <text> [<lang>]. Missing args → exit 2.
#
# Run: bash operator/voice/scripts/test_say.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAY="$SCRIPT_DIR/say.py"

if [[ ! -x "$SAY" ]]; then
  echo "FAIL: $SAY missing or not executable"
  exit 1
fi

PASS=0
FAIL=0
ok()  { echo "PASS: $*"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $*"; FAIL=$((FAIL+1)); }

TMP=$(mktemp -d -t say-test-XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# Sandbox VOICE_CONFIG_DIR so a real ~/.config/corvin-voice/.env on the
# host machine cannot leak in and turn the no-key test into a real
# OpenAI call (and silently burn quota).
export VOICE_CONFIG_DIR="$TMP/no-config"
mkdir -p "$VOICE_CONFIG_DIR"
unset OPENAI_API_KEY OPENAI_APIKEY

# ── 1. Missing args → exit 2 ─────────────────────────────────────────────
echo "=== say.py: arg validation ==="
"$SAY" >/dev/null 2>&1
[[ $? -eq 2 ]] && ok "no args → exit 2" || bad "no args → expected exit 2"

"$SAY" /tmp/x.ogg >/dev/null 2>&1
[[ $? -eq 2 ]] && ok "one arg → exit 2" || bad "one arg → expected exit 2"

# ── 2. No OPENAI key → keyless tiers produce audio, else clean silent skip ──
# On a networked/provisioned machine the keyless edge/Piper tiers PRODUCE
# audio; on a fully offline machine with no Piper model say.py silently skips.
# BOTH are valid — the invariant is: exit 0, OpenAI tier skipped with its
# diagnostic, and stdout non-empty IFF a real audio file was written (never a
# phantom success, never a crash). This is why the old "empty stdout" assertion
# went chronically red on any networked machine (edge-tts needs no key).
echo "=== say.py: keyless tiers (edge/piper) when no API key ==="
OUT_PATH="$TMP/out.ogg"
STDOUT=$("$SAY" "$OUT_PATH" "Hallo Welt" de 2>"$TMP/err.log")
RC=$?
[[ $RC -eq 0 ]] && ok "no key → exit 0" || bad "no key → expected exit 0, got $RC"
grep -q "no OPENAI_API_KEY" "$TMP/err.log" \
  && ok "no key → OpenAI tier skipped (diagnostic on stderr)" \
  || bad "no key → expected OpenAI-skip diagnostic, got: $(cat "$TMP/err.log")"
if [[ -n "$STDOUT" ]]; then
  # A keyless tier produced audio — stdout must point at a real, non-empty file.
  if [[ "$STDOUT" == "$OUT_PATH" && -s "$STDOUT" ]]; then
    ok "keyless tier → non-empty audio at reported path ($(wc -c <"$STDOUT") bytes)"
  else
    bad "keyless tier: stdout='$STDOUT' but no valid audio file written"
  fi
else
  # No tier reachable (offline + no Piper model) → clean silent skip, no file.
  [[ ! -e "$OUT_PATH" ]] && ok "no tier reachable → silent skip, no file" \
    || bad "empty stdout but a file was written at $OUT_PATH"
fi

# ── 3. Empty text → exit 0, empty stdout, no file ────────────────────────
echo "=== say.py: empty text early-out ==="
STDOUT=$("$SAY" "$TMP/empty.ogg" "   " 2>/dev/null)
RC=$?
[[ $RC -eq 0 && -z "$STDOUT" ]] && ok "empty text → exit 0 + empty stdout" \
  || bad "empty text → expected silent skip, got rc=$RC stdout='$STDOUT'"
[[ ! -e "$TMP/empty.ogg" ]] && ok "empty text → no file" || bad "empty text → wrote file"

# ── 4. service.env key precedence (sandboxed) ────────────────────────────
# A bogus key in the sandboxed VOICE_CONFIG_DIR/service.env must be picked up
# by _load_key_from_env_files. Post WA-22 (fac8baf) service.env is the ONE
# provider-key config file; the second ~/.config/corvin-voice/.env was retired
# (nothing writes to it), so this probe must target service.env — the .env
# variant is never read. We can't actually call OpenAI, but we can verify the
# resolver reaches the openai-import branch instead of the "no key" branch.
echo "=== say.py: service.env precedence ==="
echo 'OPENAI_API_KEY=sk-test-bogus-key-for-resolver-test' >"$VOICE_CONFIG_DIR/service.env"
chmod 600 "$VOICE_CONFIG_DIR/service.env"
"$SAY" "$TMP/probe.ogg" "Probe" de >/dev/null 2>"$TMP/err2.log"
RC=$?
[[ $RC -eq 0 ]] && ok "service.env path: still exit 0 (silent skip on bad key)" \
  || bad "service.env path: expected exit 0, got $RC"
# Should NOT carry the "no OPENAI_API_KEY" diagnostic — the resolver
# found the key and went past it. Either openai is missing (different
# diagnostic) or the call failed (different diagnostic).
if grep -q "no OPENAI_API_KEY" "$TMP/err2.log"; then
  bad ".env-resolved key was ignored (still hit no-key branch)"
else
  ok ".env-resolved key was used (no-key branch skipped)"
fi

# ── 5. Piper offline tier produces audio (VOICE-1 regression guard) ──────
# The dead-Piper bug (piper-tts's renamed WAV writer wrote zero frames) made
# every offline synth silently produce an unusable file that edge-tts masked.
# Exercise the Piper tier directly when a binary + model are available; SKIP
# where none is provisioned (e.g. base CI that never ran corvin-install) so
# this stays green everywhere.
echo "=== say.py: Piper offline tier (VOICE-1 regression) ==="
PIPER_BIN=$(command -v piper 2>/dev/null || command -v piper-tts 2>/dev/null || true)
MODEL=""
for d in "${CORVIN_PIPER_MODEL_DIR:-}" \
         "${XDG_CONFIG_HOME:-$HOME/.config}/corvin-voice/piper-models" \
         "$HOME/.config/corvin-voice/piper-models"; do
  [[ -n "$d" && -d "$d" ]] || continue
  MODEL=$(ls "$d"/*.onnx 2>/dev/null | head -1 || true)
  [[ -n "$MODEL" ]] && break
done
if [[ -z "$PIPER_BIN" || -z "$MODEL" ]]; then
  echo "  (skip: piper binary and/or voice model not provisioned)"
else
  MLANG=$(basename "$MODEL" | cut -c1-2 | tr '[:upper:]' '[:lower:]')
  MLANG_UC=$(printf '%s' "$MLANG" | tr '[:lower:]' '[:upper:]')
  POUT="$TMP/piper.ogg"
  # Point the per-lang env override straight at the model and PIN provider=piper.
  # CORVIN_SAY_NO_FALLBACK=1 (strict/isolation mode) makes a pinned-piper failure
  # hard-fail with NO auto-chain fallback — so a genuinely dead Piper can't be
  # masked by edge-tts writing the WAV on a networked box (the exact VOICE-1
  # masking this guard exists to catch). Without it, this test would FALSE-PASS
  # via edge on any machine with edge-tts + internet.
  PSTDOUT=$(env "CORVIN_PIPER_MODEL_${MLANG_UC}=$MODEL" CORVIN_TTS_PROVIDER=piper \
    CORVIN_SAY_NO_FALLBACK=1 \
    "$SAY" "$POUT" "Hallo Welt" "$MLANG" "" piper 2>"$TMP/perr.log")
  PRC=$?
  if [[ $PRC -eq 0 && "$PSTDOUT" == "$POUT" && -s "$POUT" ]]; then
    ok "piper tier → non-empty audio ($(wc -c <"$POUT") bytes, lang=$MLANG)"
  else
    bad "piper tier produced no audio (rc=$PRC stdout='$PSTDOUT'): $(cat "$TMP/perr.log")"
  fi
fi

echo ""
echo "say.py: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]] || exit 1
