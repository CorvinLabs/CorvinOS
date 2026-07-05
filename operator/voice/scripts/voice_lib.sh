#!/usr/bin/env bash
# Common helpers for the voice plugin: config loading, defaults, paths.

VOICE_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/corvin-voice"
VOICE_CONFIG_FILE="$VOICE_CONFIG_DIR/config.json"
VOICE_LOG_FILE="$VOICE_CONFIG_DIR/voice.log"

voice_log() {
  mkdir -p "$VOICE_CONFIG_DIR"
  printf '%s [voice] %s\n' "$(date -Iseconds)" "$*" >>"$VOICE_LOG_FILE"
}

voice_ensure_config() {
  mkdir -p "$VOICE_CONFIG_DIR"
  if [[ ! -f "$VOICE_CONFIG_FILE" ]]; then
    # Detect system locale for a sensible lang_default on first run.
    local _lang
    _lang="$(python3 -c "
import os, re
supported = {'de','en','es','fr','it','pt','nl','ru','pl','uk','tr','zh'}
for v in ('LC_ALL', 'LANG', 'LANGUAGE'):
    raw = os.environ.get(v, '')
    if raw:
        m = re.match(r'([a-zA-Z]{2,3})', raw)
        if m and m.group(1).lower() in supported:
            print(m.group(1).lower()); exit(0)
print('en')
" 2>/dev/null || echo "en")"
    cat >"$VOICE_CONFIG_FILE" <<JSON
{
  "enabled": true,
  "engine": "auto",
  "lang_mode": "auto",
  "lang_default": "${_lang}",
  "summarize": true,
  "summarize_threshold": 2000,
  "summarize_max_chars": 2000,
  "summarize_include_task": true,
  "interrupt_mode": "barge",
  "earcon_enabled": true,
  "earcon_threshold_chars": 80,
  "autoupdate": true,
  "cache_enabled": true,
  "cache_cap_mb": 100,
  "duck": false,
  "duck_volume_pct": 30,
  "listen_max_seconds": 15,
  "listen_lang": "auto",
  "voice_de": "alloy",
  "voice_en": "alloy",
  "speed": 1.0,
  "openai_model": "tts-1",
  "piper_model_de": "",
  "piper_model_en": "",
  "piper_model_es": "",
  "piper_model_fr": "",
  "piper_model_it": "",
  "piper_model_pt": "",
  "piper_model_nl": "",
  "piper_model_ru": "",
  "piper_model_pl": "",
  "piper_model_uk": "",
  "piper_model_tr": "",
  "piper_model_zh": "",
  "anthropic_model": "claude-haiku-4-5"
}
JSON
  fi
}

# voice_cfg <jq-path> [default]
voice_cfg() {
  local path="$1"
  local default="${2:-}"
  voice_ensure_config
  local val
  val="$(jq -r "$path // empty" "$VOICE_CONFIG_FILE" 2>/dev/null)"
  if [[ -z "$val" || "$val" == "null" ]]; then
    printf '%s' "$default"
  else
    printf '%s' "$val"
  fi
}

voice_cfg_set() {
  local path="$1"
  local value="$2"
  voice_ensure_config
  local tmp
  tmp="$(mktemp)"
  jq "$path = $value" "$VOICE_CONFIG_FILE" >"$tmp" && mv "$tmp" "$VOICE_CONFIG_FILE"
}

# voice_persona_voice <persona-name> <lang>
#
# Reads the OpenAI-TTS voice for a cowork persona. Lookup order:
#   1. <corvin_home>/cowork/personas/<name>.json    (user override)
#   2. ~/.config/claude-cowork/personas/<name>.json  (legacy user dir)
#   3. <repo>/operator/cowork/personas/<name>.json    (bundle)
# Inside each file, prefers `tts_voice_<lang>` over the lang-agnostic
# `tts_voice` field. Empty stdout means "no per-persona voice configured;
# let speak.sh fall back to voice_<lang> from the global config".
#
# Why two-tier (lang + agnostic): some personas should sound the same
# in any language (jarvis is "British Sir" in both DE and EN — onyx for
# both); others may want a different voice per language. The lang-
# specific key wins when present.
voice_persona_voice() {
  local persona="$1"
  local lang="$2"
  [[ -z "$persona" ]] && return 0
  command -v jq >/dev/null 2>&1 || return 0

  local corvin_home="${CORVIN_HOME:-${CORVIN_HOME:-$HOME/.corvin}}"
  local repo_root
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." 2>/dev/null && pwd)"

  local candidates=(
    "$corvin_home/cowork/personas/${persona}.json"
    "$HOME/.config/claude-cowork/personas/${persona}.json"
    "$repo_root/operator/cowork/personas/${persona}.json"
  )

  local f val
  for f in "${candidates[@]}"; do
    [[ -r "$f" ]] || continue
    val="$(jq -r ".tts_voice_${lang} // .tts_voice // empty" "$f" 2>/dev/null)"
    if [[ -n "$val" && "$val" != "null" ]]; then
      printf '%s' "$val"
      return 0
    fi
  done
  return 0
}

# Build the list of .env candidate files in priority order.
#
# Order:
#   1. VOICE_CONFIG_DIR/.env                           (canonical, plugin-stable —
#                                                      survives repo forks and
#                                                      working-directory changes)
#   2. VOICE_CONFIG_DIR/service.env                    (systemd-style env file
#                                                      shared with bridge.sh)
#   3. CLAUDE_PLUGIN_ROOT/.env                         (plugin-local)
#   4. CLAUDE_PLUGIN_ROOT/../.env, ../../.env, ../../../.env
#                                                     (walk up — covers the
#                                                      case where the plugin
#                                                      lives in operator/voice
#                                                      and the user's .env
#                                                      sits in the repo root)
#   5. $PWD/.env                                       (Claude Code's CWD)
#   6. $HOME/.env                                      (user's home)
#
# Order rationale: canonical first. Repo-local .env files were the legacy
# location and caused the "voice silent after fork" bug — when corvinOS
# forked from corvin-voice-skill on 2026-05-04, the only .env with the
# OPENAI key was left behind in the legacy repo, out of every walk-up
# range. ~/.config/corvin-voice/.env is now authoritative; repo-local
# files remain as fallback for setups that prefer to keep secrets next
# to the code.
#
# Stops at the first file that contains the requested key.
_voice_env_candidates() {
  local out=()
  out+=("${VOICE_CONFIG_DIR}/.env")
  out+=("${VOICE_CONFIG_DIR}/service.env")
  local root="${CLAUDE_PLUGIN_ROOT:-}"
  if [[ -n "$root" ]]; then
    out+=("$root/.env")
    local walk="$root"
    local i
    for i in 1 2 3; do
      walk="$(dirname "$walk")"
      [[ -z "$walk" || "$walk" == "/" ]] && break
      out+=("$walk/.env")
    done
  fi
  out+=("$PWD/.env")
  out+=("$HOME/.env")
  printf '%s\n' "${out[@]}"
}

# Read a key (e.g. OPENAI_API_KEY or OPENAI_APIKEY) from a .env file.
# Strips quotes, returns empty string if not found.
_voice_read_env_value() {
  local file="$1"
  local pattern="$2"
  grep -E "^[[:space:]]*${pattern}=" "$file" 2>/dev/null \
    | tail -1 \
    | sed -E "s/^[[:space:]]*${pattern}=//; s/^\"//; s/\"$//; s/^'//; s/'$//"
}

# Normalize OpenAI key — accept both OPENAI_API_KEY (SDK convention) and
# OPENAI_APIKEY (user-typed). Falls back to scanning .env files in the
# plugin tree, repo root, CWD, and ~/.config/corvin-voice.
voice_load_openai_key() {
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    return 0
  fi
  if [[ -n "${OPENAI_APIKEY:-}" ]]; then
    export OPENAI_API_KEY="$OPENAI_APIKEY"
    return 0
  fi
  local candidate val
  local searched=()
  while IFS= read -r candidate; do
    [[ -z "$candidate" ]] && continue
    searched+=("$candidate")
    [[ ! -f "$candidate" ]] && continue
    val="$(_voice_read_env_value "$candidate" "OPENAI_API_?KEY")"
    if [[ -n "$val" ]]; then
      export OPENAI_API_KEY="$val"
      voice_log "load_openai_key: from $candidate"
      return 0
    fi
  done < <(_voice_env_candidates)
  voice_log "load_openai_key: NOT FOUND — searched ${#searched[@]} paths: ${searched[*]}"
  return 1
}

# Roadmap K — TTS-Key migration aus corvinOS-Silo nach XDG.
#
# Some operators set OPENAI_API_KEY inside <corvin_home>/voice/.env or
# <corvin_home>/global/voice/.env (legacy convention from before the XDG
# canonicalisation). When ~/.config/corvin-voice/.env doesn't have the
# key but a sibling silo file does, we copy the value into the canonical
# XDG location so future lookups stop walking down the fallback chain.
#
# Idempotent: if the canonical file already has the key, this is a no-op.
# Non-destructive: the silo copy is left in place — operators can keep
# their existing files without surprise deletes. The motivation is making
# the canonical file authoritative, not enforcing a single-location rule.
#
# Reads $CORVIN_HOME (set by the bridge / setup) or falls back to the
# walk-up convention. Returns 0 always; failures are silent (best-effort).
voice_migrate_legacy_silo_key() {
  local canonical="$VOICE_CONFIG_DIR/.env"
  # If the canonical file already carries the key, nothing to do.
  if [[ -f "$canonical" ]]; then
    local existing
    existing="$(_voice_read_env_value "$canonical" "OPENAI_API_?KEY")"
    if [[ -n "$existing" ]]; then
      return 0
    fi
  fi
  local silo_root="${CORVIN_HOME:-}"
  if [[ -z "$silo_root" ]]; then
    # Walk-up from CLAUDE_PLUGIN_ROOT looking for a .corvinOS marker.
    local walk="${CLAUDE_PLUGIN_ROOT:-$PWD}"
    while [[ -n "$walk" && "$walk" != "/" ]]; do
      if [[ -d "$walk/.corvinOS" ]]; then
        silo_root="$walk/.corvinOS"
        break
      fi
      walk="$(dirname "$walk")"
    done
  fi
  [[ -z "$silo_root" || ! -d "$silo_root" ]] && return 0
  local candidate
  for candidate in \
        "$silo_root/voice/.env" \
        "$silo_root/global/voice/.env"; do
    [[ ! -f "$candidate" ]] && continue
    local val
    val="$(_voice_read_env_value "$candidate" "OPENAI_API_?KEY")"
    [[ -z "$val" ]] && continue
    mkdir -p "$VOICE_CONFIG_DIR"
    # Append-only to be safe with existing canonical content.
    if [[ -f "$canonical" ]]; then
      printf '\nOPENAI_API_KEY=%s\n' "$val" >>"$canonical"
    else
      umask 077
      printf 'OPENAI_API_KEY=%s\n' "$val" >"$canonical"
      chmod 0600 "$canonical" 2>/dev/null || true
    fi
    voice_log "migrate_legacy_silo_key: copied OPENAI_API_KEY from $candidate to $canonical"
    return 0
  done
  return 0
}

voice_load_anthropic_key() {
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    return 0
  fi
  if [[ -n "${ANTHROPIC_APIKEY:-}" ]]; then
    export ANTHROPIC_API_KEY="$ANTHROPIC_APIKEY"
    return 0
  fi
  local candidate val
  local searched=()
  while IFS= read -r candidate; do
    [[ -z "$candidate" ]] && continue
    searched+=("$candidate")
    [[ ! -f "$candidate" ]] && continue
    val="$(_voice_read_env_value "$candidate" "ANTHROPIC_API_?KEY")"
    if [[ -n "$val" ]]; then
      export ANTHROPIC_API_KEY="$val"
      voice_log "load_anthropic_key: from $candidate"
      return 0
    fi
  done < <(_voice_env_candidates)
  voice_log "load_anthropic_key: NOT FOUND — searched ${#searched[@]} paths: ${searched[*]}"
  return 1
}

voice_detect_engine() {
  local override="${VOICE_ENGINE:-$(voice_cfg .engine auto)}"
  if [[ "$override" != "auto" ]]; then
    printf '%s' "$override"
    return
  fi
  voice_load_openai_key || true
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
      voice_log "detect_engine: openai key found but python3 missing — falling through"
    elif ! python3 -c "import openai" 2>/dev/null; then
      voice_log "detect_engine: openai key found but \`pip install openai\` missing — falling through"
    else
      printf 'openai'
      return
    fi
  else
    voice_log "detect_engine: no OPENAI_API_KEY found in any candidate — falling through"
  fi
  # edge-tts connects to tts.microsoft.com — skip on deployments that enforce
  # local-only egress (EU_PRODUCTION / CORVIN_TTS_LOCAL_ONLY=1).
  if [[ "${CORVIN_TTS_LOCAL_ONLY:-0}" != "1" ]] && python3 -c "import edge_tts" 2>/dev/null; then
    voice_log "detect_engine: chose edge-tts (no openai, edge-tts available; cloud egress to tts.microsoft.com)"
    printf 'edge-tts'
    return
  fi
  if command -v piper >/dev/null 2>&1; then
    voice_log "detect_engine: chose piper (no openai/edge-tts)"
    printf 'piper'
    return
  fi
  if command -v espeak-ng >/dev/null 2>&1; then
    voice_log "detect_engine: chose espeak-ng (no openai/piper)"
    printf 'espeak-ng'
    return
  fi
  if command -v say >/dev/null 2>&1; then
    voice_log "detect_engine: chose say (macOS)"
    printf 'say'
    return
  fi
  voice_log "detect_engine: NONE — install one of: openai key + pip install openai, piper, or espeak-ng"
  printf 'none'
}

VOICE_PIDFILE="$VOICE_CONFIG_DIR/current.pgid"
VOICE_LOCKFILE="$VOICE_CONFIG_DIR/tts.lock"

# voice_tts_acquire: serialize TTS so two speak.sh runs never overlap on
# the same audio sink. Honors the same `interrupt_mode` setting as the
# stop_hook so behavior is consistent everywhere TTS gets emitted.
#
# Modes:
#   barge (default) — kill any in-flight TTS first, then acquire.
#   queue           — block on the lock until the prior TTS exits cleanly.
#
# Idempotent: when VOICE_TTS_OWNS_LOCK is already set in the environment
# (the stop_hook pipeline does this), this is a no-op so the inner speak.sh
# does not kill the very pipeline that wraps it. The lock is held on FD 9
# for the calling process's lifetime; the kernel releases it on exit, so
# no manual unlock is needed.
voice_tts_acquire() {
  [[ -n "${VOICE_TTS_OWNS_LOCK:-}" ]] && return 0
  if ! command -v flock >/dev/null 2>&1; then
    voice_log "tts_acquire: flock not available, no serialization"
    return 0
  fi
  mkdir -p "$VOICE_CONFIG_DIR"

  local mode
  mode="$(voice_cfg .interrupt_mode barge)"
  case "$mode" in
    barge|queue) ;;
    *)
      voice_log "tts_acquire: unknown interrupt_mode=$mode, treating as barge"
      mode="barge"
      ;;
  esac

  if [[ "$mode" == "barge" ]]; then
    voice_kill_current_tts
  fi

  exec 9>"$VOICE_LOCKFILE"
  flock -x 9
  # Record our own PGID so the next caller can barge us. speak.sh re-execs
  # under setsid when not already a session leader, so $$ == PGID here.
  printf '%s' "$$" > "$VOICE_PIDFILE"
  export VOICE_TTS_OWNS_LOCK=1
}

# Send TERM/KILL to the entire process group of any currently-running TTS job.
# Used in "barge" interrupt mode so a new turn replaces the old voice immediately.
voice_kill_current_tts() {
  [[ -f "$VOICE_PIDFILE" ]] || return 0
  local pgid
  pgid="$(cat "$VOICE_PIDFILE" 2>/dev/null)"
  if [[ -z "$pgid" || ! "$pgid" =~ ^[0-9]+$ ]]; then
    rm -f "$VOICE_PIDFILE"
    return 0
  fi
  if kill -0 -- "-$pgid" 2>/dev/null; then
    voice_log "kill_current: TERM pgid=$pgid"
    kill -TERM -- "-$pgid" 2>/dev/null
    # Give the player ~150ms to exit cleanly, then SIGKILL the rest.
    local i=0
    while (( i < 5 )) && kill -0 -- "-$pgid" 2>/dev/null; do
      sleep 0.05
      i=$((i+1))
    done
    if kill -0 -- "-$pgid" 2>/dev/null; then
      voice_log "kill_current: KILL pgid=$pgid"
      kill -KILL -- "-$pgid" 2>/dev/null
    fi
  fi
  rm -f "$VOICE_PIDFILE"
}

# Block until the currently-running TTS job (if any) has exited.
# Used in "queue" interrupt mode.
voice_wait_current_tts() {
  [[ -f "$VOICE_PIDFILE" ]] || return 0
  local pgid
  pgid="$(cat "$VOICE_PIDFILE" 2>/dev/null)"
  [[ -z "$pgid" || ! "$pgid" =~ ^[0-9]+$ ]] && { rm -f "$VOICE_PIDFILE"; return 0; }
  voice_log "wait_current: waiting for pgid=$pgid"
  while kill -0 -- "-$pgid" 2>/dev/null; do
    sleep 0.2
  done
  rm -f "$VOICE_PIDFILE"
}

# Duck other audio sinks while TTS is playing.
# voice_duck_begin writes a state file with original volumes and lowers them
# to duck_volume_pct. voice_duck_end restores. No-op if pactl is missing,
# duck is disabled in config, or there are no other sink-inputs.
VOICE_DUCK_STATE="$VOICE_CONFIG_DIR/duck_state"

voice_duck_begin() {
  [[ "$(voice_cfg .duck false)" == "true" ]] || return 0
  command -v pactl >/dev/null 2>&1 || { voice_log "duck: pactl not found, skipping"; return 0; }
  local pct
  pct="$(voice_cfg .duck_volume_pct 30)"
  rm -f "$VOICE_DUCK_STATE"
  # Snapshot all currently-playing sink-inputs and their volumes.
  local got_any=0
  while IFS=$'\t' read -r id _; do
    [[ -z "$id" ]] && continue
    # Get current volume as a percentage (e.g. "65%").
    local vol
    vol="$(pactl get-sink-input-volume "$id" 2>/dev/null | grep -oE '[0-9]+%' | head -1)"
    [[ -z "$vol" ]] && continue
    printf '%s\t%s\n' "$id" "$vol" >> "$VOICE_DUCK_STATE"
    pactl set-sink-input-volume "$id" "${pct}%" 2>/dev/null && got_any=1
  done < <(pactl list sink-inputs short 2>/dev/null)
  if (( got_any )); then
    voice_log "duck: lowered $(wc -l < "$VOICE_DUCK_STATE") stream(s) to ${pct}%"
  fi
}

voice_duck_end() {
  [[ -f "$VOICE_DUCK_STATE" ]] || return 0
  command -v pactl >/dev/null 2>&1 || { rm -f "$VOICE_DUCK_STATE"; return 0; }
  while IFS=$'\t' read -r id vol; do
    [[ -z "$id" ]] && continue
    pactl set-sink-input-volume "$id" "$vol" 2>/dev/null || true
  done < "$VOICE_DUCK_STATE"
  voice_log "duck: restored $(wc -l < "$VOICE_DUCK_STATE") stream(s)"
  rm -f "$VOICE_DUCK_STATE"
}

voice_audio_player() {
  for c in aplay paplay ffplay mpv play mpg123; do
    if command -v "$c" >/dev/null 2>&1; then
      printf '%s' "$c"
      return
    fi
  done
  printf ''
}
