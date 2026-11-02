#!/bin/sh
# with_python.sh — run a hook script with a Python 3 that exists on THIS system.
#
#   sh ${CLAUDE_PLUGIN_ROOT}/hooks/with_python.sh path_gate.py   (stdin passes through)
#
# hooks.json ran every hook as `python3 …`. A fresh system installed per the
# README has NO python3 on PATH: the installer brings uv's Python only inside
# the corvinos tool venv. Claude Code then got exit 127 ("python3: command not
# found") from every hook — a non-blocking hook error — and the fail-closed
# L10 path gate, the web-trust gate and the elevation gate never ran at all
# (measured on a fresh Ubuntu 24.04, 2026-10-21, round 2).
#
# Resolution: python3 on PATH → CORVIN_PYTHON → the corvinos tool venv
# (uv tool dir, the uv default dir) → `python` if it is a Python 3.
here="$(cd "$(dirname "$0")" && pwd)"
script="$1"; shift
_py=""
# Windows: %LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe exists on a fresh
# Windows 11 even without any Python — the App Execution Alias stub, which
# only prints "Python was not found ... Microsoft Store" and exits 9009.
# `command -v` finds it, so every hook (incl. the fail-closed L10 path gate)
# failed instead of falling through to the corvinos tool venv. Only that
# alias is probed; every other python3 is taken as before.
_py3_ok=0
if command -v python3 >/dev/null 2>&1; then
    _py3_ok=1
    case "$(command -v python3)" in
        *[Ww]indows[Aa]pps*) python3 -c 'import sys' >/dev/null 2>&1 || _py3_ok=0 ;;
    esac
fi
if [ "$_py3_ok" = 1 ]; then
    _py="python3"
elif [ -n "${CORVIN_PYTHON:-}" ] && [ -x "${CORVIN_PYTHON}" ]; then
    _py="${CORVIN_PYTHON}"
else
    for _d in "$(uv tool dir 2>/dev/null)/corvinos/bin" \
              "${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools/corvinos/bin" \
              "${APPDATA:-}/uv/tools/corvinos/Scripts"; do
        if [ -n "$_d" ] && [ -x "$_d/python3" ]; then _py="$_d/python3"; break; fi
        if [ -n "$_d" ] && [ -x "$_d/python.exe" ]; then _py="$_d/python.exe"; break; fi
    done
    if [ -z "$_py" ] && command -v python >/dev/null 2>&1 \
       && python -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' 2>/dev/null; then
        _py="python"
    fi
fi
if [ -z "$_py" ]; then
    echo "corvin hooks: no Python 3 found (python3, CORVIN_PYTHON, the corvinos tool venv) — $script did not run" >&2
    exit 1
fi
# Windows (Git Bash / MSYS): run the hook in Python UTF-8 mode. Claude Code
# sends the hook payload as UTF-8; under the ANSI code page (cp1252 on a German
# Windows) a non-ASCII path in it is mis-decoded and a deny reason containing
# one raises UnicodeEncodeError on stderr -> exit 1, which Claude Code treats as
# a NON-blocking hook error (the denied call would proceed). No-op elsewhere.
case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) PYTHONUTF8="${PYTHONUTF8:-1}"; export PYTHONUTF8 ;;
esac
exec "$_py" "$here/$script" "$@"
