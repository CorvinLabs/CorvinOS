#!/usr/bin/env bash
# voice_cli.sh — backend for the voice plugin's slash commands.
#
# Usage:
#   voice_cli.sh on|off
#   voice_cli.sh status
#   voice_cli.sh test [de|en|both]
#   voice_cli.sh speak <text>
#   voice_cli.sh lang auto|de|en
#   voice_cli.sh mode auto|full|summary
#   voice_cli.sh config [show|path|edit]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=voice_lib.sh
source "$SCRIPT_DIR/voice_lib.sh"

voice_ensure_config

cmd="${1:-status}"
shift || true

case "$cmd" in
  on)
    voice_cfg_set ".enabled" "true"
    engine="$(voice_detect_engine)"
    echo "Voice-Mode: AKTIV. Engine: $engine"
    echo "Config: $VOICE_CONFIG_FILE"
    ;;

  off)
    voice_cfg_set ".enabled" "false"
    echo "Voice-Mode: DEAKTIVIERT"
    ;;

  status)
    voice_load_openai_key || true
    enabled="$(voice_cfg .enabled true)"
    engine="$(voice_detect_engine)"
    lang_mode="$(voice_cfg .lang_mode auto)"
    lang_default="$(voice_cfg .lang_default de)"
    voice_de="$(voice_cfg .voice_de alloy)"
    voice_en="$(voice_cfg .voice_en alloy)"
    summarize="$(voice_cfg .summarize true)"
    threshold="$(voice_cfg .summarize_threshold 800)"
    voice_mode="$(voice_cfg .voice_mode auto)"
    max_chars="$(voice_cfg .summarize_max_chars 4096)"
    player="$(voice_audio_player)"
    cat <<EOF
🎙  Claude Voice — Status
─────────────────────────────────────────
Enabled        : $enabled
Engine (auto)  : $engine
Audio player   : ${player:-(none)}
Lang mode      : $lang_mode  (default $lang_default)
Voice DE / EN  : $voice_de / $voice_en
Summarize      : $summarize  (threshold $threshold chars, max $max_chars)
Voice mode     : $voice_mode  (auto | full | summary)
Config file    : $VOICE_CONFIG_FILE
Log file       : $VOICE_LOG_FILE
─────────────────────────────────────────
Verfügbare Tools:
  openai-sdk   : $(python3 -c "import openai" 2>/dev/null && echo yes || echo no)
  anthropic-sdk: $(python3 -c "import anthropic" 2>/dev/null && echo yes || echo no)
  OPENAI_API_KEY    : $([[ -n "${OPENAI_API_KEY:-}" ]] && echo set || echo unset)
  ANTHROPIC_API_KEY : $([[ -n "${ANTHROPIC_API_KEY:-}" ]] && echo set || echo unset)
  piper        : $(command -v piper >/dev/null 2>&1 && echo yes || echo no)
  espeak-ng    : $(command -v espeak-ng >/dev/null 2>&1 && echo yes || echo no)
EOF
    ;;

  test)
    arg="${1:-both}"
    case "$arg" in
      de)
        "$SCRIPT_DIR/speak.sh" --lang de --text "Hallo, das ist ein deutscher Test der Sprachausgabe."
        ;;
      en)
        "$SCRIPT_DIR/speak.sh" --lang en --text "Hello, this is an English test of the voice output."
        ;;
      both|"")
        "$SCRIPT_DIR/speak.sh" --lang de --text "Hallo, das ist ein deutscher Test."
        "$SCRIPT_DIR/speak.sh" --lang en --text "And this is the English test."
        ;;
      *)
        echo "test: unknown lang '$arg' (use de|en|both)" >&2
        exit 2
        ;;
    esac
    echo "Test fertig."
    ;;

  speak)
    text="$*"
    if [[ -z "$text" ]]; then
      echo "speak: no text given" >&2
      exit 2
    fi
    lang="$(printf '%s' "$text" | python3 "$SCRIPT_DIR/detect_lang.py" --default "$(voice_cfg .lang_default de)")"
    "$SCRIPT_DIR/speak.sh" --lang "$lang" --text "$text"
    echo "Vorgelesen ($lang)."
    ;;

  lang)
    arg="${1:-}"
    case "$arg" in
      auto|de|en)
        voice_cfg_set ".lang_mode" "\"$arg\""
        echo "Sprach-Modus: $arg"
        ;;
      *)
        echo "lang: missing or invalid value (auto|de|en)" >&2
        exit 2
        ;;
    esac
    ;;

  mode)
    arg="${1:-}"
    case "$arg" in
      auto)
        voice_cfg_set ".voice_mode" "\"auto\""
        echo "Voice-Modus: auto (Schwellenwert-basiert: ab \$summarize_threshold Zeichen wird zusammengefasst)"
        ;;
      full)
        voice_cfg_set ".voice_mode" "\"full\""
        echo "Voice-Modus: full — jede Antwort wird vollständig vorgelesen, keine Zusammenfassung."
        ;;
      summary)
        voice_cfg_set ".voice_mode" "\"summary\""
        echo "Voice-Modus: summary — jede Antwort wird zusammengefasst, unabhängig von der Länge."
        ;;
      ""|show)
        cur="$(voice_cfg .voice_mode auto)"
        echo "Voice-Modus: $cur"
        ;;
      *)
        echo "mode: missing or invalid value (auto|full|summary)" >&2
        exit 2
        ;;
    esac
    ;;

  config)
    sub="${1:-show}"
    case "$sub" in
      show)
        cat "$VOICE_CONFIG_FILE"
        ;;
      path)
        echo "$VOICE_CONFIG_FILE"
        ;;
      edit)
        echo "Bearbeite manuell: $VOICE_CONFIG_FILE"
        ;;
      *)
        echo "config: unknown subcommand '$sub'" >&2
        exit 2
        ;;
    esac
    ;;

  *)
    echo "voice_cli.sh: unknown command '$cmd'" >&2
    echo "Use: on|off|status|test|speak|lang|mode|config" >&2
    exit 2
    ;;
esac
