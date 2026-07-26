#!/usr/bin/env bash
# bootstrap.sh — set up the corvin-compute venv.
#
# Phase 13.1 ships a stdlib-only venv. Phase 13.8 adds sklearn + numpy
# for the Bayesian strategy; operators on disk-constrained hosts can
# opt out via `CORVIN_COMPUTE_MINIMAL=1`.
#
# Why a venv at all
# -----------------
# Same reasoning as core/gateway: Ubuntu 24.04 ships Python
# under PEP-668 (externally managed). The minimum subset is stdlib-only,
# so a venv is only structurally needed once sklearn lands — but we
# create it from 13.1 so the run-all-tests skip-gate has a single
# stable detection path (`.venv/bin/python`).
#
# Usage
# -----
#   bash core/compute/bootstrap.sh
#   CORVIN_COMPUTE_MINIMAL=1 bash core/compute/bootstrap.sh

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

if [[ "${CORVIN_COMPUTE_MINIMAL:-0}" == "1" ]]; then
  echo "[bootstrap] minimal install (no sklearn) — Bayesian strategy disabled"
  if [ -s requirements-minimal.txt ]; then
    "${VENV_DIR}/bin/python" -m pip install --quiet -r requirements-minimal.txt
  fi
else
  echo "[bootstrap] full install (sklearn + numpy for Bayesian)"
  if [ -s requirements.txt ]; then
    "${VENV_DIR}/bin/python" -m pip install --quiet -r requirements.txt
  fi
fi

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import sys
print(f'  python      {sys.version.split()[0]}')
try:
    import sklearn
    print(f'  scikit-learn {sklearn.__version__}')
except ImportError:
    print('  scikit-learn (not installed — minimal mode)')
try:
    import numpy
    print(f'  numpy        {numpy.__version__}')
except ImportError:
    print('  numpy        (not installed — minimal mode)')
"

echo "[bootstrap] ok"
