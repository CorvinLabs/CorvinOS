#!/usr/bin/env bash
# bootstrap.sh — set up the license-gate plugin venv.
#
# Idempotent. The license-gate is OPT-IN. Apache-core operators who
# never bootstrap this plugin see zero blocking behaviour — the
# gateway's app.py wraps `from corvin_license import ...` in
# try/except. Without a license.jwt installed, the plugin reports
# tier=free and disables nothing; it only fires when an installed
# token is expired/revoked AND grace has elapsed.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[bootstrap] creating venv at $(pwd)/${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

echo "[bootstrap] upgrading pip"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip

echo "[bootstrap] installing FastAPI + PyJWT + cryptography + pytest"
"${VENV_DIR}/bin/pip" install --quiet \
  fastapi httpx uvicorn "PyJWT[crypto]" cryptography pytest

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import fastapi, httpx, uvicorn, jwt, cryptography
print(f'  fastapi       {fastapi.__version__}')
print(f'  httpx         {httpx.__version__}')
print(f'  uvicorn       {uvicorn.__version__}')
print(f'  PyJWT         {jwt.__version__}')
print(f'  cryptography  {cryptography.__version__}')
"

echo "[bootstrap] ok"
