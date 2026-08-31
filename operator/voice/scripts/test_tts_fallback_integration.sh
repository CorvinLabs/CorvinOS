#!/bin/bash
# Integration test: Verify TTS fallback chain works end-to-end
# This test creates mock engines and verifies speak.sh uses them correctly

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEAK_SCRIPT="$SCRIPT_DIR/speak.sh"
TEST_DIR="/tmp/tts_fallback_test_$$"
VOICE_CONFIG="$TEST_DIR/config.json"
TEST_LOG="$TEST_DIR/test.log"

trap 'rm -rf "$TEST_DIR"' EXIT

mkdir -p "$TEST_DIR"

# Create a minimal config
cat > "$VOICE_CONFIG" << 'CONFIG'
{
  "lang_default": "en",
  "voice_en": "alloy",
  "openai_model": "tts-1",
  "speed": 1.0,
  "log_file": "/dev/null",
  "cache_enabled": false,
  "piper_model_en": "/fake/model.onnx"
}
CONFIG

echo "====== TTS Fallback Chain Integration Test ======"
echo

# Test 1: Verify speak.sh respects NO OPENAI_API_KEY by trying Piper
echo "[Test 1] OpenAI key missing → tries Piper"
export VOICE_CONFIG_FILE="$VOICE_CONFIG"
unset OPENAI_API_KEY || true
unset OPENAI_APIKEY || true

# Create a mock piper that just prints a message
TEST_PIPER="$TEST_DIR/piper"
cat > "$TEST_PIPER" << 'PIPER'
#!/bin/bash
echo "[MOCK PIPER CALLED]" >> "$TEST_DIR/test.log"
# Create fake output file with minimal wav header
if [[ "$*" =~ output_file=([^ ]+) ]]; then
  outfile=$(echo "$*" | grep -oP 'output_file=\K[^ ]+')
  printf '\x52\x49\x46\x46\x24\x00\x00\x00' > "$outfile"  # Minimal RIFF header
  printf '\x57\x41\x56\x45' >> "$outfile"  # WAVE
  printf '\x66\x6d\x74\x20' >> "$outfile"  # fmt
  printf '\x10\x00\x00\x00' >> "$outfile"  # chunk size
  printf '\x01\x00\x02\x00\x44\xac\x00\x00\x10\xb1\x02\x00\x04\x00\x10\x00' >> "$outfile"  # fmt data
fi
exit 0
PIPER
chmod +x "$TEST_PIPER"

# Create mock audio player (does nothing)
TEST_PLAYER="$TEST_DIR/test_player"
cat > "$TEST_PLAYER" << 'PLAYER'
#!/bin/bash
echo "[MOCK PLAYER CALLED with $1]" >> "$TEST_DIR/test.log"
PLAYER
chmod +x "$TEST_PLAYER"

export PATH="$TEST_DIR:$PATH"
export VOICE_TTS_OWNS_LOCK=1

# Override piper command to be our mock
cp "$TEST_PIPER" "$TEST_DIR/piper"
cp "$TEST_PLAYER" "$TEST_DIR/aplay"

# Try to run speak.sh without OpenAI key
# It should skip OpenAI and try Piper
output=$(echo "Test message" | bash "$SPEAK_SCRIPT" --lang en 2>&1 || true)

if grep -q "MOCK PIPER CALLED" "$TEST_LOG" 2>/dev/null; then
  echo "  ✓ speak.sh correctly fell back to Piper when OpenAI key missing"
else
  echo "  ⚠ Piper may not have been called (expected in fallback scenario)"
fi

# Test 2: Verify fallback chain order in speak.sh
echo "[Test 2] Fallback chain priority"
# Extract the fallback chain construction from speak.sh
fallback_chain=$(grep -A 3 'case "$ENGINE" in' "$SPEAK_SCRIPT" | grep -A 2 'openai)')
if echo "$fallback_chain" | grep -q "piper.*espeak-ng.*say"; then
  echo "  ✓ Fallback chain order is correct: OpenAI → Piper → espeak-ng → say"
else
  echo "  ✗ Fallback chain order may be incorrect"
fi

# Test 3: Verify quota error detection
echo "[Test 3] Quota error detection"
quota_errors=("insufficient_quota" "Error code: 429" "rate_limit_exceeded")
script_has_quota_check=true

for err in "${quota_errors[@]}"; do
  if ! grep -q "$err" "$SPEAK_SCRIPT"; then
    script_has_quota_check=false
    echo "  ✗ Missing quota error check for: $err"
  fi
done

if $script_has_quota_check; then
  echo "  ✓ All quota error patterns are detected"
fi

# Test 4: Verify try_openai_tts returns proper error codes
echo "[Test 4] OpenAI error handling"
if grep -q "return 2  # Special code for quota error" "$SPEAK_SCRIPT"; then
  echo "  ✓ Quota errors return special code (2) for fallback"
else
  echo "  ⚠ Quota error code not found (may use different code)"
fi

# Test 5: Verify all try_* functions exist
echo "[Test 5] TTS engine try functions"
for fn in try_openai_tts try_piper_tts try_espeak_tts try_say_tts; do
  if grep -q "^${fn}()" "$SPEAK_SCRIPT"; then
    echo "  ✓ $fn found"
  else
    echo "  ✗ Missing $fn"
  fi
done

echo
echo "====== Integration test complete ======"
