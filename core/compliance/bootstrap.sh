#!/usr/bin/env bash
# bootstrap.sh — set up the compliance-reports plugin venv.
#
# Idempotent. Opt-in. Provides PDF generation for EU AI Act Art. 50
# evidence, GDPR Art. 30 RoPA, and Audit-Chain Integrity Attestation.
# Apache-2.0 (free baseline reports must be free — transparency is
# never a paywall).

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

echo "[bootstrap] installing reportlab + pytest"
"${VENV_DIR}/bin/python" -m pip install --quiet \
  "reportlab>=4.0" pytest

echo "[bootstrap] versions:"
"${VENV_DIR}/bin/python" -c "
import reportlab
print(f'  reportlab  {reportlab.Version}')
"

echo "[bootstrap] ok"
