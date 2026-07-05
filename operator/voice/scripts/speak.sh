#!/usr/bin/env bash
# speak.sh — engine-agnostic TTS dispatcher with fallback chain.
#
# Usage:
#   speak.sh [--lang de|en] [--engine openai|piper|espeak-ng|say] [--text "..."]
#   echo "Hallo Welt" | speak.sh --lang de
#
# Reads text from --text or stdin. Picks engine via voice_detect_engine
# unless --engine is given. Tries engines in fallback order (OpenAI → Piper → espeak-ng → say).
# If OpenAI quota is exhausted, automatically tries next engine.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=voice_lib.sh
source "$SCRIPT_DIR/voice_lib.sh"

# Become session leader so kill -- "-$$" reaches our audio-player children
if [[ -z "${VOICE_TTS_OWNS_LOCK:-}" ]] && command -v setsid >/dev/null 2>&1; then
  _spk_pgid="$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')"
  if [[ "$_spk_pgid" != "$$" ]]; then
    exec setsid -w "$0" "$@"
  fi
  unset _spk_pgid
fi

LANG_OVERRIDE=""
ENGINE_OVERRIDE=""
TEXT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lang) LANG_OVERRIDE="$2"; shift 2 ;;
    --engine) ENGINE_OVERRIDE="$2"; shift 2 ;;
    --text) TEXT="$2"; shift 2 ;;
    -h|--help)
      sed -n '3,12p' "$0"
      exit 0
      ;;
    *)
      echo "speak.sh: unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$TEXT" ]]; then
  TEXT="$(cat)"
fi

# Strip trailing whitespace, collapse newlines for nicer prosody.
TEXT="$(printf '%s' "$TEXT" | tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//')"

if [[ -z "$TEXT" ]]; then
  voice_log "speak: empty text, skipping"
  exit 0
fi

# Cross-process serialize: barge any in-flight TTS (default), then take the
# lock so anything starting after us also waits or barges us in turn.
voice_tts_acquire

LANG="${LANG_OVERRIDE:-$(voice_cfg .lang_default de)}"
case "$LANG" in de|en) ;; *) LANG="de" ;; esac

ENGINE="${ENGINE_OVERRIDE:-$(voice_detect_engine)}"
voice_log "speak: engine=$ENGINE lang=$LANG chars=${#TEXT}"

# Build fallback chain: start with chosen engine, then try others if it fails
FALLBACK_CHAIN=("$ENGINE")
case "$ENGINE" in
  openai)   FALLBACK_CHAIN+=("edge-tts" "piper" "espeak-ng" "say") ;;
  edge-tts) FALLBACK_CHAIN+=("piper" "espeak-ng" "say") ;;
  piper)    FALLBACK_CHAIN+=("espeak-ng" "say") ;;
  espeak-ng) FALLBACK_CHAIN+=("say") ;;
esac

# ─── TTS fallback chain helpers ───────────────────────────────────────────────

is_quota_error() {
  local err_msg="$1"
  [[ "$err_msg" =~ insufficient_quota|"Error code: 429"|rate_limit_exceeded ]]
}

try_openai_tts() {
  local text="$1" voice="$2" model="$3" speed="$4" outfile="$5"
  local err_output

  err_output=$(mktemp --suffix=.err)
  trap 'rm -f "$err_output"' RETURN

  if OPENAI_TTS_TEXT="$text" \
     OPENAI_TTS_VOICE="$voice" \
     OPENAI_TTS_MODEL="$model" \
     OPENAI_TTS_SPEED="$speed" \
     OPENAI_TTS_OUTFILE="$outfile" \
     python3 - 2>"$err_output" <<'PY'
import os, sys
from openai import OpenAI
client = OpenAI()
resp = client.audio.speech.create(
    model=os.environ["OPENAI_TTS_MODEL"],
    voice=os.environ["OPENAI_TTS_VOICE"],
    input=os.environ["OPENAI_TTS_TEXT"],
    response_format="wav",
    speed=float(os.environ.get("OPENAI_TTS_SPEED", "1.0")),
)
with open(os.environ["OPENAI_TTS_OUTFILE"], "wb") as f:
    f.write(resp.read())
PY
  then
    [[ -s "$outfile" ]] && return 0 || return 1
  else
    local err_text
    err_text=$(cat "$err_output" 2>/dev/null)
    if is_quota_error "$err_text"; then
      voice_log "speak: OpenAI quota exhausted, will try fallback"
      return 2  # Special code for quota error
    else
      echo "[voice] OpenAI TTS error: $err_text" >&2
      voice_log "speak: OpenAI error: $err_text"
      return 1
    fi
  fi
}

try_edge_tts() {
  local text="$1" lang="$2" player="$3"

  if ! python3 -c "import edge_tts" 2>/dev/null; then
    voice_log "speak: edge-tts package not installed"
    return 1
  fi

  local tmp_mp3
  tmp_mp3="$(mktemp --suffix=.mp3)"

  local voice
  case "$lang" in
    de) voice="de-DE-KatjaNeural" ;;
    en) voice="en-US-AriaNeural" ;;
    *)  voice="en-US-AriaNeural" ;;
  esac

  if ! EDGE_TTS_TEXT="$text" EDGE_TTS_VOICE="$voice" EDGE_TTS_OUTFILE="$tmp_mp3" \
       python3 - 2>/dev/null <<'PY'
import asyncio, edge_tts, os
async def main():
    tts = edge_tts.Communicate(os.environ["EDGE_TTS_TEXT"], os.environ["EDGE_TTS_VOICE"])
    await tts.save(os.environ["EDGE_TTS_OUTFILE"])
asyncio.run(main())
PY
  then
    rm -f "$tmp_mp3"
    voice_log "speak: edge-tts synthesis failed"
    return 1
  fi

  if [[ ! -s "$tmp_mp3" ]]; then
    rm -f "$tmp_mp3"
    voice_log "speak: edge-tts produced empty output"
    return 1
  fi

  # edge-tts outputs MP3 — try players that handle MP3 directly; fall back to
  # ffmpeg→WAV conversion for aplay/paplay environments.
  voice_duck_begin
  local rc=0
  if command -v ffplay >/dev/null 2>&1; then
    ffplay -nodisp -autoexit -loglevel quiet "$tmp_mp3" || rc=$?
  elif command -v mpv >/dev/null 2>&1; then
    mpv --really-quiet --no-video "$tmp_mp3" || rc=$?
  elif command -v mpg123 >/dev/null 2>&1; then
    mpg123 -q "$tmp_mp3" || rc=$?
  elif command -v play >/dev/null 2>&1; then
    play -q "$tmp_mp3" || rc=$?
  elif command -v ffmpeg >/dev/null 2>&1; then
    local tmp_wav
    tmp_wav="$(mktemp --suffix=.wav)"
    if ffmpeg -y -loglevel quiet -i "$tmp_mp3" "$tmp_wav" 2>/dev/null; then
      case "$player" in
        aplay)  aplay -q "$tmp_wav" || rc=$? ;;
        paplay) paplay "$tmp_wav" || rc=$? ;;
        *)      aplay -q "$tmp_wav" 2>/dev/null || paplay "$tmp_wav" || rc=$? ;;
      esac
    else
      rc=1
    fi
    rm -f "$tmp_wav"
  else
    voice_log "speak: edge-tts: no MP3-capable player found (ffplay/mpv/mpg123/play)"
    rc=1
  fi
  voice_duck_end
  rm -f "$tmp_mp3"
  return $rc
}

try_piper_tts() {
  local text="$1" lang="$2" model="$3" player="$4"

  if [[ -z "$model" ]]; then
    voice_log "speak: No piper model configured for lang=$lang"
    return 1
  fi

  local tmp_wav
  tmp_wav="$(mktemp --suffix=.wav)"
  trap 'rm -f "$tmp_wav"' RETURN

  if ! printf '%s' "$text" | piper --model "$model" --output_file "$tmp_wav" >/dev/null 2>&1; then
    voice_log "speak: Piper TTS failed"
    return 1
  fi

  voice_duck_begin
  trap 'voice_duck_end' RETURN
  case "$player" in
    aplay)  aplay -q "$tmp_wav" ;;
    paplay) paplay "$tmp_wav" ;;
    ffplay) ffplay -nodisp -autoexit -loglevel quiet "$tmp_wav" ;;
    *)      "$player" "$tmp_wav" ;;
  esac
  voice_duck_end
  return 0
}

try_espeak_tts() {
  local text="$1" lang="$2"
  local esv

  case "$lang" in
    de) esv="de" ;;
    en) esv="en-us" ;;
    *)  esv="$lang" ;;
  esac

  voice_duck_begin
  trap 'voice_duck_end' RETURN
  printf '%s' "$text" | espeak-ng -v "$esv" --stdin
  local rc=$?
  voice_duck_end
  return $rc
}

try_say_tts() {
  local text="$1"
  local sv

  case "$LANG" in
    de) sv="Anna" ;;
    en) sv="Samantha" ;;
    *)  sv="" ;;
  esac

  voice_duck_begin
  trap 'voice_duck_end' RETURN
  if [[ -n "$sv" ]]; then
    say -v "$sv" -- "$text"
  else
    say -- "$text"
  fi
  local rc=$?
  voice_duck_end
  return $rc
}

# ─── Try each engine in fallback chain ──────────────────────────────────────

PLAYER="$(voice_audio_player)"
if [[ -z "$PLAYER" ]]; then
  echo "[voice] No audio player found (aplay/paplay/ffplay/mpv/mpg123)." >&2
  exit 1
fi

for engine in "${FALLBACK_CHAIN[@]}"; do
  case "$engine" in
    none)
      echo "[voice] No TTS engine available. Install one of: openai, piper, espeak-ng, say." >&2
      exit 1
      ;;

    openai)
      voice_log "speak: trying OpenAI TTS"
      voice_load_openai_key || true
      if [[ -z "${OPENAI_API_KEY:-}" ]]; then
        voice_log "speak: OPENAI_API_KEY not set, skipping to next engine"
        continue
      fi

      # Per-persona voice override
      PERSONA="${CORVIN_CALLER_PERSONA:-${CORVIN_CALLER_PERSONA:-$(voice_cfg .voice_persona '')}}"
      PERSONA_VOICE="$(voice_persona_voice "$PERSONA" "$LANG")"
      if [[ -n "$PERSONA_VOICE" ]]; then
        VOICE="$PERSONA_VOICE"
      else
        VOICE="$(voice_cfg .voice_${LANG} alloy)"
      fi
      MODEL="$(voice_cfg .openai_model tts-1)"
      SPEED="$(voice_cfg .speed 1.0)"

      # Cache lookup before API call
      CACHE_ENABLED="$(voice_cfg .cache_enabled true)"
      CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/corvin-voice/tts"
      if [[ "$CACHE_ENABLED" == "true" ]]; then
        mkdir -p "$CACHE_DIR"
        CACHE_KEY="$(printf '%s|%s|%s|%s' "$TEXT" "$VOICE" "$MODEL" "$SPEED" | sha256sum | cut -d' ' -f1)"
        CACHE_FILE="$CACHE_DIR/${CACHE_KEY}.wav"
        if [[ -s "$CACHE_FILE" ]]; then
          voice_log "speak: cache HIT key=${CACHE_KEY:0:12}"
          touch "$CACHE_FILE"
          # Play and exit success
          voice_duck_begin
          trap 'voice_duck_end' EXIT
          case "$PLAYER" in
            aplay)  aplay -q "$CACHE_FILE" ;;
            paplay) paplay "$CACHE_FILE" ;;
            ffplay) ffplay -nodisp -autoexit -loglevel quiet "$CACHE_FILE" ;;
            mpv)    mpv --really-quiet --no-video "$CACHE_FILE" ;;
            play)   play -q "$CACHE_FILE" ;;
            mpg123) mpg123 -q "$CACHE_FILE" ;;
          esac
          voice_duck_end
          trap - EXIT
          exit 0
        fi
      fi

      # Not in cache — call API
      AUDIO_FILE="$(mktemp --suffix=.wav)"
      if try_openai_tts "$TEXT" "$VOICE" "$MODEL" "$SPEED" "$AUDIO_FILE"; then
        # Success — handle cache and play
        if [[ "$CACHE_ENABLED" == "true" ]]; then
          mkdir -p "$CACHE_DIR"
          mv "$AUDIO_FILE" "$CACHE_FILE"
          AUDIO_FILE="$CACHE_FILE"
          # LRU eviction
          CACHE_CAP_MB="$(voice_cfg .cache_cap_mb 100)"
          CACHE_CAP_BYTES=$((CACHE_CAP_MB * 1024 * 1024))
          CACHE_BYTES=$(du -sb "$CACHE_DIR" 2>/dev/null | cut -f1)
          if (( CACHE_BYTES > CACHE_CAP_BYTES )); then
            voice_log "speak: cache evicting old entries (${CACHE_BYTES}B > ${CACHE_CAP_BYTES}B)"
            TARGET_BYTES=$((CACHE_CAP_BYTES * 80 / 100))
            while IFS= read -r -d '' line; do
              (( CACHE_BYTES <= TARGET_BYTES )) && break
              file="${line#* }"
              sz=$(stat -c %s "$file" 2>/dev/null || echo 0)
              rm -f "$file"
              CACHE_BYTES=$((CACHE_BYTES - sz))
            done < <(find "$CACHE_DIR" -type f -name '*.wav' -printf '%T@ %p\0' | sort -zn)
          fi
        fi
        voice_duck_begin
        trap 'voice_duck_end' EXIT
        case "$PLAYER" in
          aplay)  aplay -q "$AUDIO_FILE" ;;
          paplay) paplay "$AUDIO_FILE" ;;
          ffplay) ffplay -nodisp -autoexit -loglevel quiet "$AUDIO_FILE" ;;
          mpv)    mpv --really-quiet --no-video "$AUDIO_FILE" ;;
          play)   play -q "$AUDIO_FILE" ;;
          mpg123) mpg123 -q "$AUDIO_FILE" ;;
        esac
        voice_duck_end
        trap - EXIT
        rm -f "$AUDIO_FILE"
        exit 0
      else
        rc=$?
        rm -f "$AUDIO_FILE"
        # If quota error (rc=2), try fallback; otherwise try next engine
        [[ $rc -eq 2 ]] && continue || continue
      fi
      ;;

    edge-tts)
      voice_log "speak: trying Edge TTS"
      if try_edge_tts "$TEXT" "$LANG" "$PLAYER"; then
        exit 0
      fi
      ;;

    piper)
      voice_log "speak: trying Piper TTS"
      MODEL="$(voice_cfg ".piper_model_${LANG}" "")"
      if [[ -z "$MODEL" ]]; then
        voice_log "speak: No piper model configured for lang=$LANG"
        continue
      fi
      if try_piper_tts "$TEXT" "$LANG" "$MODEL" "$PLAYER"; then
        exit 0
      fi
      ;;

    espeak-ng)
      voice_log "speak: trying espeak-ng TTS"
      if command -v espeak-ng >/dev/null 2>&1; then
        if try_espeak_tts "$TEXT" "$LANG"; then
          exit 0
        fi
      else
        voice_log "speak: espeak-ng not found"
      fi
      ;;

    say)
      voice_log "speak: trying say (macOS) TTS"
      if command -v say >/dev/null 2>&1; then
        if try_say_tts "$TEXT"; then
          exit 0
        fi
      else
        voice_log "speak: say command not found (macOS only)"
      fi
      ;;

    *)
      echo "[voice] unsupported engine: $engine" >&2
      continue
      ;;
  esac
done

# All engines failed
echo "[voice] All TTS engines failed. Check logs: $(voice_cfg .log_file /dev/null)" >&2
voice_log "speak: all TTS engines failed for lang=$LANG"
exit 1
