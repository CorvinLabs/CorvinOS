#!/usr/bin/env bash
# bootstrap.sh — set up the console-UI plugin venv.
#
# Idempotent. The console plugin is OPT-IN; single-operator
# deployments that never bootstrap this tree see zero changes to
# the gateway's behaviour (the gateway's app.py wraps
# `from corvin_console import ...` in a try/except).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[bootstrap] creating venv at $(pwd)/${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

echo "[bootstrap] upgrading pip"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip

echo "[bootstrap] installing core dependencies"
# uvicorn[standard] pulls websockets + wsproto + httptools + watchfiles +
# uvloop — WebSocket support is REQUIRED for the ADR-0037 web-chat
# channel (routes/chat.py). Without it the WS upgrade fails with a
# silent 403 and the messenger UI shows "WebSocket not connected".
"${VENV_DIR}/bin/pip" install --quiet \
  fastapi pydantic httpx "uvicorn[standard]" itsdangerous python-multipart \
  pyyaml "PyJWT[crypto]" cryptography pytest

echo "[bootstrap] installing voice dependencies (TTS/STT, no API key required for edge-tts)"
# edge-tts: Microsoft Edge TTS, no API key, pure-Python — universal TTS fallback.
# openai + anthropic: SDK for cloud TTS/STT and Claude engine.
"${VENV_DIR}/bin/pip" install --quiet "edge-tts>=6.1.8" "openai>=1.0" "anthropic>=0.25"

# pywhispercpp: local STT (whisper.cpp binding), no API key, works offline.
# ADR-0185 M1: replaces faster-whisper as the canonical local STT engine —
# it ships genuine win32/win_amd64 wheels (no `av`/torch/ctranslate2 dep),
# so this install step is IDENTICAL on Linux, macOS, and Windows now; no
# more platform branch here (the previous faster-whisper-only branch
# silently left Windows without a local STT fallback at all).
echo "[bootstrap] installing pywhispercpp (local STT, all platforms)"
"${VENV_DIR}/bin/pip" install --quiet "pywhispercpp>=1.5.0" \
  && echo "[bootstrap]   → pywhispercpp installed (offline voice input ready)" \
  || echo "[bootstrap]   ! pywhispercpp install failed — STT will use OpenAI Whisper (API key needed)"

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import fastapi, pydantic, httpx, uvicorn, itsdangerous, yaml, jwt, cryptography
print(f'  fastapi       {fastapi.__version__}')
print(f'  pydantic      {pydantic.VERSION}')
print(f'  httpx         {httpx.__version__}')
print(f'  uvicorn       {uvicorn.__version__}')
print(f'  itsdangerous  {itsdangerous.__version__}')
print(f'  pyyaml        {yaml.__version__}')
print(f'  PyJWT         {jwt.__version__}')
print(f'  cryptography  {cryptography.__version__}')
"
"${VENV_DIR}/bin/python" -c "
import importlib.util
for pkg, mod in [('edge-tts','edge_tts'),('openai','openai'),('pywhispercpp','pywhispercpp')]:
    found = importlib.util.find_spec(mod) is not None
    print(f'  {pkg:20s} {\"✓\" if found else \"✗ (not installed)\"}')
"

# ── ADR-0037 web-next frontend (optional) ────────────────────────────
#
# If web-next/package.json exists AND `npm` is on PATH AND the operator
# hasn't opted out (CORVIN_SKIP_WEBNEXT_BUILD=1), build the new SPA.
# Failure here is non-fatal — the backend falls back to legacy web/.
WEBNEXT_DIR="corvin_console/web-next"
if [ -f "${WEBNEXT_DIR}/package.json" ] \
   && [ -z "${CORVIN_SKIP_WEBNEXT_BUILD:-}" ] \
   && command -v npm >/dev/null 2>&1; then
  echo "[bootstrap] building web-next/ frontend (ADR-0037)"
  (
    cd "${WEBNEXT_DIR}"
    # Prefer `npm ci` when a lockfile exists for reproducibility.
    if [ -f "package-lock.json" ]; then
      npm ci --silent
    else
      npm install --silent
    fi
    npm run build --silent
  ) && echo "[bootstrap]   → web-next/dist/ built" \
    || echo "[bootstrap]   ! web-next build failed — backend will fall back to legacy web/"
else
  echo "[bootstrap] skipping web-next build (no package.json, no npm, or opt-out)"
fi

echo "[bootstrap] ok"
