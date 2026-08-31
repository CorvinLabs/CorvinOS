#!/usr/bin/env bash
# Start an isolated console/gateway backend for Playwright E2E on :8799 with a
# throwaway CORVIN_HOME, so production data on :8765 is never touched.
# Used as a webServer entry by playwright.isolated.config.ts.
set -uo pipefail
# repo root = five levels up from web-next/scripts/
R=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)
export CORVIN_HOME="${CORVIN_E2E_HOME:-/tmp/corvin-e2e-home}"
mkdir -p "$CORVIN_HOME/tenants/_default/global"
export PYTHONPATH="$R/core/console:$R/core/gateway:$R/core/license:$R/core/compliance:$R/operator/forge:$R/operator/skill-forge"
PORT="${CORVIN_E2E_PORT:-8799}"
exec "$R/core/console/.venv/bin/python" -m uvicorn corvin_gateway.app:app \
  --host 127.0.0.1 --port "$PORT" --log-level info
