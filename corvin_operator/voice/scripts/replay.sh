#!/usr/bin/env bash
# replay.sh — replay the last N voice outputs from the cache, no API call.
#
# Reads ~/.cache/corvin-voice/history.jsonl (written by stop_hook.sh on each
# successful TTS), takes the last N entries, and plays each WAV from the
# cache directory. Falls back to TTS regeneration only if the cache file is
# missing — a Cache eviction would be the typical reason.
#
# Usage:
#   replay.sh           — replay the last entry
#   replay.sh 3         — replay the last 3 in chronological order
#   replay.sh --list 5  — print the last 5 entries (no playback)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=voice_lib.sh
source "$SCRIPT_DIR/voice_lib.sh"

HISTORY="${XDG_CACHE_HOME:-$HOME/.cache}/corvin-voice/history.jsonl"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/corvin-voice/tts"

LIST_ONLY=false
N=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list) LIST_ONLY=true; shift ;;
    -h|--help)
      sed -n '3,12p' "$0"
      exit 0
      ;;
    [0-9]*) N="$1"; shift ;;
    *) echo "replay: unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -s "$HISTORY" ]]; then
  echo "Voice-Replay: history is empty (no successful TTS yet)."
  exit 0
fi

# Get the last N entries.
ENTRIES="$(tail -n "$N" "$HISTORY")"

if [[ "$LIST_ONLY" == "true" ]]; then
  printf '%s\n' "$ENTRIES" | nl -ba | while IFS=$'\t' read -r idx line; do
    [[ -z "$line" ]] && continue
    ts="$(printf '%s' "$line" | jq -r '.ts // "?"')"
    lang="$(printf '%s' "$line" | jq -r '.lang // "?"')"
    chars="$(printf '%s' "$line" | jq -r '.chars // 0')"
    snippet="$(printf '%s' "$line" | jq -r '.text // ""' | tr '\n' ' ' | cut -c1-80)"
    printf '%s. [%s] %s | %s chars | %s\n' "$idx" "$ts" "$lang" "$chars" "$snippet"
  done
  exit 0
fi

PLAYER="$(voice_audio_player)"
if [[ -z "$PLAYER" ]]; then
  echo "[voice] No audio player found." >&2
  exit 1
fi

# Replay each entry in chronological order. Use the cache file when present.
echo "$ENTRIES" | while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  CACHE_KEY="$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("cache_key",""))')"
  TEXT="$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("text",""))')"
  LANG_E="$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("lang","de"))')"
  WAV="$CACHE_DIR/${CACHE_KEY}.wav"

  if [[ -s "$WAV" ]]; then
    voice_log "replay: cache HIT key=${CACHE_KEY:0:12}"
    case "$PLAYER" in
      aplay)  aplay -q "$WAV" ;;
      paplay) paplay "$WAV" ;;
      ffplay) ffplay -nodisp -autoexit -loglevel quiet "$WAV" ;;
      mpv)    mpv --really-quiet --no-video "$WAV" ;;
      play)   play -q "$WAV" ;;
      mpg123) mpg123 -q "$WAV" ;;
    esac
  else
    # Cache miss (evicted). Regenerate via speak.sh — that re-populates the cache.
    voice_log "replay: cache MISS key=${CACHE_KEY:0:12}, regenerating"
    "$SCRIPT_DIR/speak.sh" --lang "$LANG_E" --text "$TEXT"
  fi
done
