#!/usr/bin/env bash
# Stop-hook: invoked when Claude Code finishes a turn. Reads the transcript,
# extracts the last assistant prose, optionally summarizes it, then plays
# it via the TTS dispatcher.
#
# All side effects are best-effort and logged. The hook MUST never block
# Claude Code on TTS errors — it forks the actual playback and exits 0.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SCRIPTS_DIR="$PLUGIN_DIR/scripts"
# shellcheck source=../scripts/voice_lib.sh
source "$SCRIPTS_DIR/voice_lib.sh"

voice_ensure_config

# Recursion guard: when summarize.py invokes `claude -p ...` to use the user's
# Max-subscription OAuth as the LLM backend, that subshell-Claude itself
# triggers its own stop-hook on completion. Without this guard, every voice
# summary would itself be summarized → endless loop.
if [[ -n "${VOICE_HOOK_RECURSION:-}" ]]; then
  voice_log "stop_hook: recursion guard hit, skipping"
  exit 0
fi

ENABLED="$(voice_cfg .enabled true)"
if [[ "$ENABLED" != "true" ]]; then
  voice_log "stop_hook: voice disabled, skipping"
  exit 0
fi

# Stop-hook receives a JSON envelope on stdin.
INPUT="$(cat)"
TRANSCRIPT_PATH="$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)"

if [[ -z "$TRANSCRIPT_PATH" || ! -f "$TRANSCRIPT_PATH" ]]; then
  voice_log "stop_hook: no transcript_path found in input"
  exit 0
fi

RAW="$(python3 "$SCRIPTS_DIR/extract_last_assistant.py" "$TRANSCRIPT_PATH")"
if [[ -z "$RAW" ]]; then
  # Race-Fix: Stop-hook fired SOMETIMES innerhalb derselben Sekunde wie
  # Claude Code den finalen Text-Block in die JSONL flusht. Latest assistant
  # ist dann ein vorheriger tool_use-Eintrag (kein Text) → leer. 300ms
  # Retry deckt diesen Race ab. Wenn auch nach Retry leer: wirklich kein
  # Text (z.B. Hook fired mitten in einer mehrteiligen tool-Sequenz).
  sleep 0.3
  RAW="$(python3 "$SCRIPTS_DIR/extract_last_assistant.py" "$TRANSCRIPT_PATH")"
  if [[ -z "$RAW" ]]; then
    voice_log "stop_hook: no assistant text extracted (after 300ms retry)"
    exit 0
  fi
  voice_log "stop_hook: extracted on retry (initial read raced with transcript flush)"
fi

# Staleness-Guard: wenn der User schon eine NEUERE Frage geschrieben hat,
# bevor dieser Hook lief, wäre das Vorlesen jetzt verwirrend (alte Antwort
# zu neuer Frage). Genau dieser Race war der „warum wurde was anderes
# vorgelesen?"-Bug. Skip und log, damit man das im Log sieht.
if python3 "$SCRIPTS_DIR/transcript_is_stale.py" "$TRANSCRIPT_PATH"; then
  voice_log "stop_hook: STALE — newer user msg arrived before pipeline ran, skipping"
  exit 0
fi

# Original user prompt for this turn — used so the spoken output starts
# with a one-line task reminder ("Zu deiner Frage: ..."). Keeps the
# listener anchored when several turns play back-to-back.
INCLUDE_TASK="$(voice_cfg .summarize_include_task true)"
TASK=""
if [[ "$INCLUDE_TASK" == "true" ]]; then
  TASK="$(python3 "$SCRIPTS_DIR/extract_last_user.py" "$TRANSCRIPT_PATH")"
fi

# Trivial-skip: short, non-list, non-question answers get a 200ms earcon
# instead of a full TTS round-trip. Saves API cost and latency for "Ok.",
# "Fertig.", "Verstanden." and similar acknowledgments.
EARCON_ENABLED="$(voice_cfg .earcon_enabled true)"
EARCON_THRESHOLD="$(voice_cfg .earcon_threshold_chars 80)"
if [[ "$EARCON_ENABLED" == "true" ]]; then
  SHAPE="$(printf '%s' "$RAW" | python3 "$SCRIPTS_DIR/earcon.py" classify --threshold "$EARCON_THRESHOLD")"
  if [[ "$SHAPE" == "trivial" ]]; then
    voice_log "stop_hook: trivial response (chars=${#RAW}), playing done earcon"
    INTERRUPT_MODE="$(voice_cfg .interrupt_mode barge)"
    case "$INTERRUPT_MODE" in
      barge) voice_kill_current_tts ;;
      queue) voice_wait_current_tts ;;
    esac
    SCRIPTS_DIR="$SCRIPTS_DIR" \
    VOICE_LOG_FILE="$VOICE_LOG_FILE" \
    VOICE_PIDFILE="$VOICE_PIDFILE" \
    VOICE_TTS_OWNS_LOCK=1 \
    setsid bash -c '
      trap '\''rm -f "$VOICE_PIDFILE"'\'' EXIT
      python3 "$SCRIPTS_DIR/earcon.py" play done >>"$VOICE_LOG_FILE" 2>&1
    ' </dev/null >/dev/null 2>&1 &
    EARCON_PGID=$!
    printf '%s' "$EARCON_PGID" > "$VOICE_PIDFILE"
    disown "$EARCON_PGID" 2>/dev/null || true
    exit 0
  fi
fi

# Pre-strip: drop only code blocks and table separators. Keep headings,
# bullets, bold — the summarizer needs the structural shape to decide
# how to verbalize a list vs. plain prose.
PRE="$(printf '%s' "$RAW" | python3 "$SCRIPTS_DIR/strip_for_tts.py" --mode code-only)"
if [[ -z "$PRE" ]]; then
  voice_log "stop_hook: pre-stripped text empty"
  exit 0
fi

LANG_MODE="$(voice_cfg .lang_mode auto)"
LANG_DEFAULT="$(voice_cfg .lang_default de)"
case "$LANG_MODE" in
  de|en) LANG="$LANG_MODE" ;;
  auto)  LANG="$(printf '%s' "$PRE" | python3 "$SCRIPTS_DIR/detect_lang.py" --default "$LANG_DEFAULT")" ;;
  *)     LANG="$LANG_DEFAULT" ;;
esac

# i18n — full BCP-47 output-language pin (any language). Read from the
# bridge-wide profile; empty string when the user hasn't picked one,
# which keeps the legacy `--lang de|en` behaviour untouched. When set
# to `de` or `en` we also leave summarize.py on its native prompt so the
# argv shape stays byte-identical to the pre-i18n path. For any other
# code (zh-Hans, ja, ar, ...) summarize.py appends an OUTPUT LANGUAGE
# directive after the SELF-CHECK block.
SHARED_DIR="$(cd "$PLUGIN_DIR/bridges/shared" 2>/dev/null && pwd || true)"
OUTPUT_LANGUAGE=""
if [[ -n "$SHARED_DIR" ]]; then
  OUTPUT_LANGUAGE="$(SHARED_DIR="$SHARED_DIR" python3 -c '
import os, sys
sys.path.insert(0, os.environ["SHARED_DIR"])
try:
    import profile as p, i18n
    raw = p.load().get("display_language", "")
    code = i18n.normalise(raw)
    sys.stdout.write(code)
except Exception:
    sys.stdout.write("")
' 2>/dev/null || true)"
fi

THRESHOLD="$(voice_cfg .summarize_threshold 800)"
SUMMARIZE="$(voice_cfg .summarize true)"
MAX_CHARS="$(voice_cfg .summarize_max_chars 800)"
MODEL="$(voice_cfg .anthropic_model claude-haiku-4-5)"

# Voice mode: persistent default (`.voice_mode` in config: auto|full|summary)
# combined with a per-turn override taken from what the user said in this
# turn ("lies vollständig vor" → full, "fass zusammen" → summary). The
# override always wins; otherwise the default applies.
#   full    → never summarize, read the whole answer
#   summary → always summarize, regardless of length
#   auto    → current threshold-based behaviour
VOICE_MODE_DEFAULT="$(voice_cfg .voice_mode auto)"
VOICE_MODE_OVERRIDE=""
if [[ -n "$TASK" ]]; then
  VOICE_MODE_OVERRIDE="$(printf '%s' "$TASK" | python3 "$SCRIPTS_DIR/detect_voice_intent.py" 2>/dev/null || true)"
fi
EFFECTIVE_VOICE_MODE="${VOICE_MODE_OVERRIDE:-$VOICE_MODE_DEFAULT}"
case "$EFFECTIVE_VOICE_MODE" in
  full)
    SUMMARIZE="false"
    voice_log "stop_hook: voice_mode=full (override='${VOICE_MODE_OVERRIDE}' default='${VOICE_MODE_DEFAULT}') — skipping summarize"
    ;;
  summary)
    SUMMARIZE="true"
    THRESHOLD=0
    voice_log "stop_hook: voice_mode=summary (override='${VOICE_MODE_OVERRIDE}' default='${VOICE_MODE_DEFAULT}') — forcing summarize"
    ;;
  auto|"")
    voice_log "stop_hook: voice_mode=auto (override='${VOICE_MODE_OVERRIDE}')"
    ;;
  *)
    voice_log "stop_hook: unknown voice_mode='$EFFECTIVE_VOICE_MODE', treating as auto"
    ;;
esac

VOICE_FOR_KEY="$(voice_cfg .voice_${LANG} alloy)"
MODEL_FOR_KEY="$(voice_cfg .openai_model tts-1)"
SPEED_FOR_KEY="$(voice_cfg .speed 1.0)"
voice_load_anthropic_key || true

# Persona-tinted voice style: env wins (adapter already set
# CORVIN_CALLER_PERSONA for the inner subprocess) → config fallback
# (.voice_persona, e.g. for users who want a fixed tone independent of
# the cowork chat layer) → empty (= neutral).
PERSONA="${CORVIN_CALLER_PERSONA:-$(voice_cfg .voice_persona "")}"

# Layer-12 listener-profile block: pulled from the bridge-wide profile at
# ~/.config/corvin-voice/profile.json via bridges/shared/profile.py. Empty
# string when no voice_audience_* fields are set — backward-compat for
# users who never touched the new commands.
#
# When the user has set `display_language` (i18n), we render the audience
# block in that locale's pivot — `de` keeps the German rendering, every
# other code (en, zh-Hans, ja, …) hits the English block. The actual
# output-language pin is the OUTPUT LANGUAGE directive, set separately
# above.
AUDIENCE_LANG="${OUTPUT_LANGUAGE:-$LANG}"
AUDIENCE=""
if [[ -n "$SHARED_DIR" ]]; then
  AUDIENCE="$(LANG_FOR_AUDIENCE="$AUDIENCE_LANG" SHARED_DIR="$SHARED_DIR" python3 -c '
import os, sys
sys.path.insert(0, os.environ["SHARED_DIR"])
try:
    import profile as p
    sys.stdout.write(p.for_tts_audience(os.environ.get("LANG_FOR_AUDIENCE","de")))
except Exception:
    sys.stdout.write("")
' 2>/dev/null || true)"
fi

# Interrupt-mode: barge (default) kills any in-flight TTS — INCLUDING any
# summarize.py / claude -p subprocesses still running for an earlier turn.
# This is the key fix: previously we only killed the speak.sh process, so a
# slow CLI-summarize from an older turn would still finish and play "stale"
# audio after the user had moved on. Now the entire heavy-lift (summarize +
# strip + speak + history) lives inside one setsid process group, the PGID
# is recorded BEFORE summarize starts, and a new turn can wipe the whole
# pipeline in one TERM.
INTERRUPT_MODE="$(voice_cfg .interrupt_mode barge)"
case "$INTERRUPT_MODE" in
  barge) voice_kill_current_tts ;;
  queue) voice_wait_current_tts ;;
  *)     voice_log "stop_hook: unknown interrupt_mode=$INTERRUPT_MODE, treating as barge"
         voice_kill_current_tts ;;
esac

# Launch the entire summarize+speak+history pipeline in its own session.
# All the heavy work happens inside the setsid bash so it's killable as a
# unit. The parent shell (this hook) records the PGID synchronously and
# exits within milliseconds.
PRE="$PRE" \
TASK="$TASK" \
LANG="$LANG" \
OUTPUT_LANGUAGE="$OUTPUT_LANGUAGE" \
SCRIPTS_DIR="$SCRIPTS_DIR" \
VOICE_LOG_FILE="$VOICE_LOG_FILE" \
VOICE_PIDFILE="$VOICE_PIDFILE" \
VOICE_CONFIG_DIR="$VOICE_CONFIG_DIR" \
THRESHOLD="$THRESHOLD" \
SUMMARIZE="$SUMMARIZE" \
MAX_CHARS="$MAX_CHARS" \
MODEL="$MODEL" \
PERSONA="$PERSONA" \
AUDIENCE="$AUDIENCE" \
VOICE_FOR_KEY="$VOICE_FOR_KEY" \
MODEL_FOR_KEY="$MODEL_FOR_KEY" \
SPEED_FOR_KEY="$SPEED_FOR_KEY" \
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
VOICE_TTS_OWNS_LOCK=1 \
setsid bash -c '
  trap '\''rm -f "$VOICE_PIDFILE"'\'' EXIT

  TASK_ARGS=()
  if [[ -n "$TASK" ]]; then
    TASK_ARGS=(--task "$TASK")
  fi
  PERSONA_ARGS=()
  if [[ -n "$PERSONA" ]]; then
    PERSONA_ARGS=(--persona "$PERSONA")
  fi
  AUDIENCE_ARGS=()
  if [[ -n "$AUDIENCE" ]]; then
    AUDIENCE_ARGS=(--audience "$AUDIENCE")
  fi
  OUTPUT_LANG_ARGS=()
  if [[ -n "$OUTPUT_LANGUAGE" ]]; then
    OUTPUT_LANG_ARGS=(--output-language "$OUTPUT_LANGUAGE")
  fi

  # 1. Summarize (long path: 5–20s when using claude CLI) or pass-through.
  if [[ "$SUMMARIZE" == "true" && ${#PRE} -gt $THRESHOLD ]]; then
    printf "%s [voice] subshell: summarizing (chars=%d, task_chars=%d, persona=%s, audience=%s, output_language=%s)\n" "$(date -Iseconds)" "${#PRE}" "${#TASK}" "${PERSONA:-none}" "$([[ -n "$AUDIENCE" ]] && echo "yes(${#AUDIENCE}c)" || echo "no")" "${OUTPUT_LANGUAGE:-none}" >>"$VOICE_LOG_FILE"
    SUMMARY=$(printf "%s" "$PRE" | python3 "$SCRIPTS_DIR/summarize.py" --lang "$LANG" --max-chars "$MAX_CHARS" --model "$MODEL" "${TASK_ARGS[@]}" "${PERSONA_ARGS[@]}" "${AUDIENCE_ARGS[@]}" "${OUTPUT_LANG_ARGS[@]}")
    SPEAK_TEXT=$(printf "%s" "$SUMMARY" | python3 "$SCRIPTS_DIR/strip_for_tts.py" --mode full)
  else
    BODY=$(printf "%s" "$PRE" | python3 "$SCRIPTS_DIR/strip_for_tts.py" --mode full)
    if [[ -n "$TASK" ]]; then
      # Short answers skip the LLM. Prefix a clipped task line synchronously
      # so the listener still hears WHICH question this answer belongs to.
      PREFIX=$(TASK="$TASK" LANG="$LANG" python3 -c "
import os, re, sys
t = re.sub(r\"\\s+\", \" \", os.environ[\"TASK\"]).strip()
if len(t) > 120:
    t = t[:120].rsplit(\" \", 1)[0] + \"…\"
lang = os.environ[\"LANG\"]
print((f\"Zu deiner Frage: {t}. \" if lang == \"de\" else f\"On your question: {t}. \"), end=\"\")
")
      SPEAK_TEXT="${PREFIX}${BODY}"
    else
      SPEAK_TEXT="$BODY"
    fi
  fi

  if [[ -z "$SPEAK_TEXT" ]]; then
    printf "%s [voice] subshell: speak_text empty, exiting\n" "$(date -Iseconds)" >>"$VOICE_LOG_FILE"
    exit 0
  fi

  # 2. Compute cache key for history (so /voice-replay can play directly from cache).
  CACHE_KEY=$(printf "%s|%s|%s|%s" "$SPEAK_TEXT" "$VOICE_FOR_KEY" "$MODEL_FOR_KEY" "$SPEED_FOR_KEY" | sha256sum | cut -d" " -f1)

  # 3. Speak. On success, append history.
  if "$SCRIPTS_DIR/speak.sh" --lang "$LANG" --text "$SPEAK_TEXT" >>"$VOICE_LOG_FILE" 2>&1; then
    HISTORY_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/corvin-voice"
    mkdir -p "$HISTORY_DIR"
    SPEAK_TEXT="$SPEAK_TEXT" CACHE_KEY="$CACHE_KEY" LANG="$LANG" HISTORY_DIR="$HISTORY_DIR" python3 -c "
import json, os, time
entry = {
    \"ts\": time.strftime(\"%Y-%m-%dT%H:%M:%S%z\"),
    \"lang\": os.environ[\"LANG\"],
    \"chars\": len(os.environ[\"SPEAK_TEXT\"]),
    \"text\": os.environ[\"SPEAK_TEXT\"],
    \"cache_key\": os.environ[\"CACHE_KEY\"],
}
with open(os.path.join(os.environ[\"HISTORY_DIR\"], \"history.jsonl\"), \"a\") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + \"\n\")
"
  fi
' </dev/null >/dev/null 2>&1 &
TTS_PGID=$!
printf '%s' "$TTS_PGID" > "$VOICE_PIDFILE"
voice_log "stop_hook: launched pipeline pgid=$TTS_PGID (chars=${#PRE}, summarize=$SUMMARIZE)"
disown "$TTS_PGID" 2>/dev/null || true

exit 0
