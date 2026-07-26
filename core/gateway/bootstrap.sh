#!/usr/bin/env bash
# bootstrap.sh — set up the gateway venv with FastAPI + pydantic + httpx + uvicorn.
#
# Idempotent: a second run upgrades pip and reinstalls the deps in place.
# Phase 2.1's bearer-token module runs on pure stdlib and does NOT need
# the venv — only Phase 2.2+ (FastAPI app, TestClient tests, eventual
# uvicorn boot) does.
#
# Why a venv instead of system pip
# --------------------------------
# Ubuntu 24.04 / Debian 12+ ship Python under PEP-668 (externally
# managed). System-wide `pip install` requires --break-system-packages
# which is undesirable. Distro-packaged python3-fastapi exists but
# pins to fastapi 0.101 + pydantic 1.x, which lags the upstream
# v2 contract this plugin uses. Per-plugin venv keeps the dependency
# graph hermetic and isolated from anything the rest of Corvin
# touches.
#
# Usage
# -----
#   bash core/gateway/bootstrap.sh
#
# Test
# ----
#   core/gateway/.venv/bin/python core/gateway/tests/test_app.py

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

# A DIRECTORY IS NOT A VENV. `[ ! -d "${VENV_DIR}" ]` skipped creation for a
# half-built venv — one whose bin/python exists but has no pip, which is exactly
# what `python3 -m venv` leaves behind on Debian/Ubuntu when the python3-venv
# package is missing. The very next line then died on
# "${VENV_DIR}/bin/pip: No such file or directory" under `set -e`, so this repair
# script could never repair the state it exists to repair. Found 2026-07-26:
# core/gateway/.venv held nothing but an editable corvin_gateway install, which
# made 16 suites of the mandatory run-all-tests.sh gate fail on
# "No module named 'fastapi'".
_venv_usable() {
  [ -x "${VENV_DIR}/bin/python" ] \
    && "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1
}

if ! _venv_usable; then
  # Only remove something that actually looks like a venv, never an unrelated
  # directory that happens to carry the name.
  if [ -d "${VENV_DIR}" ] \
     && { [ -f "${VENV_DIR}/pyvenv.cfg" ] || [ -d "${VENV_DIR}/bin" ]; }; then
    echo "[bootstrap] ${VENV_DIR} exists but has no working pip — recreating"
    rm -rf "${VENV_DIR}"
  fi
  echo "[bootstrap] creating venv at $(pwd)/${VENV_DIR}"
  python3 -m venv "${VENV_DIR}" 2>/dev/null \
    || uv venv "${VENV_DIR}" 2>/dev/null \
    || { echo "[bootstrap] FATAL: cannot create a venv (need python3-venv or uv)"; exit 1; }
  _venv_usable \
    || "${VENV_DIR}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 \
    || true
  _venv_usable || {
    echo "[bootstrap] FATAL: no usable pip in ${VENV_DIR} — install python3-venv or uv"
    exit 1
  }
fi

echo "[bootstrap] upgrading pip"
"${VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip

echo "[bootstrap] installing FastAPI stack + PyYAML + JWT/crypto + grpcio"
"${VENV_DIR}/bin/python" -m pip install --quiet \
  fastapi pydantic httpx uvicorn pyyaml "PyJWT[crypto]" cryptography \
  grpcio grpcio-tools

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import fastapi, pydantic, httpx, uvicorn, yaml, jwt, cryptography, grpc
print(f'  fastapi      {fastapi.__version__}')
print(f'  pydantic     {pydantic.VERSION}')
print(f'  httpx        {httpx.__version__}')
print(f'  uvicorn      {uvicorn.__version__}')
print(f'  pyyaml       {yaml.__version__}')
print(f'  PyJWT        {jwt.__version__}')
print(f'  cryptography {cryptography.__version__}')
print(f'  grpcio       {grpc.__version__}')
"

# Generate gRPC stubs from the proto (idempotent — protoc rewrites
# corvin_pb2*.py from corvin.proto each run).
if [ -f corvin_gateway/grpc/corvin.proto ]; then
  echo "[bootstrap] generating gRPC stubs"
  (cd corvin_gateway/grpc && \
   ../../"${VENV_DIR}"/bin/python -m grpc_tools.protoc \
     -I. --python_out=. --grpc_python_out=. corvin.proto)
  # grpcio-tools emits `import corvin_pb2` without the package
  # prefix; patch it to a relative import so the package layout
  # works without messing with sys.path.
  if grep -q '^import corvin_pb2' corvin_gateway/grpc/corvin_pb2_grpc.py; then
    sed -i 's|^import corvin_pb2|from . import corvin_pb2|' \
      corvin_gateway/grpc/corvin_pb2_grpc.py
  fi
fi

echo "[bootstrap] ok"
