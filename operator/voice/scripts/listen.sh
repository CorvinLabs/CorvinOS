#!/usr/bin/env bash
# listen.sh — record a short voice prompt from the default mic and transcribe it.
#
# Usage: listen.sh [--seconds N] [--lang de|en|auto] [--audio-only PATH]
#
# Default: record 15 seconds from the system default capture device, write a
# WAV to a temp file, send it to OpenAI Whisper-1, print the transcript on
# stdout. With --audio-only, write the WAV but skip transcription (used by the
# E2E test to feed an existing WAV through the pipeline).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=voice_lib.sh
source "$SCRIPT_DIR/voice_lib.sh"

SECONDS_CAP="$(voice_cfg .listen_max_seconds 15)"
LANG_HINT="$(voice_cfg .listen_lang auto)"
INPUT_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seconds)    SECONDS_CAP="$2"; shift 2 ;;
    --lang)       LANG_HINT="$2"; shift 2 ;;
    --input)      INPUT_OVERRIDE="$2"; shift 2 ;;  # for testing: skip recording, use this WAV
    -h|--help)
      sed -n '3,9p' "$0"
      exit 0
      ;;
    *) echo "listen.sh: unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -n "$INPUT_OVERRIDE" ]]; then
  WAV="$INPUT_OVERRIDE"
  if [[ ! -s "$WAV" ]]; then
    echo "[listen] input file not found or empty: $WAV" >&2
    exit 1
  fi
  voice_log "listen: using override input $WAV"
else
  if ! command -v arecord >/dev/null 2>&1; then
    echo "[listen] arecord not found. Install alsa-utils or set --input PATH." >&2
    exit 1
  fi
  WAV="$(mktemp --suffix=.wav)"
  trap 'rm -f "$WAV"' EXIT
  voice_log "listen: recording ${SECONDS_CAP}s to $WAV"
  echo "[listen] Recording ${SECONDS_CAP}s — speak now…" >&2
  arecord -q -f S16_LE -r 16000 -c 1 -d "$SECONDS_CAP" "$WAV" 2>/dev/null
  if [[ ! -s "$WAV" ]]; then
    echo "[listen] recording produced empty file" >&2
    exit 1
  fi
  voice_log "listen: recorded $(stat -c %s "$WAV") bytes"
fi

voice_load_openai_key || true
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "[listen] OPENAI_API_KEY not set." >&2
  exit 1
fi

TRANSCRIPT="$(python3 "$SCRIPT_DIR/transcribe.py" "$WAV" --lang "$LANG_HINT")"
RC=$?
if (( RC != 0 )); then
  exit $RC
fi

# Try clipboard so the user can paste it as the next prompt. Best-effort.
if command -v wl-copy >/dev/null 2>&1; then
  printf '%s' "$TRANSCRIPT" | wl-copy 2>/dev/null && voice_log "listen: copied to clipboard (wl-copy)"
elif command -v xclip >/dev/null 2>&1; then
  printf '%s' "$TRANSCRIPT" | xclip -selection clipboard 2>/dev/null && voice_log "listen: copied to clipboard (xclip)"
elif command -v pbcopy >/dev/null 2>&1; then
  printf '%s' "$TRANSCRIPT" | pbcopy 2>/dev/null && voice_log "listen: copied to clipboard (pbcopy)"
fi

printf '%s\n' "$TRANSCRIPT"
