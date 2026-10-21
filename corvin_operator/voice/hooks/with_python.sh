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
if command -v python3 >/dev/null 2>&1; then
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
exec "$_py" "$here/$script" "$@"
