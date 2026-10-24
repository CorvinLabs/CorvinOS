#!/usr/bin/env bash
# Start an isolated console/gateway backend for Playwright E2E on :8799 with a
# throwaway CORVIN_HOME, so production data on :8765 is never touched.
# Used as a webServer entry by playwright.isolated.config.ts.
set -uo pipefail
# repo root = five levels up from web-next/scripts/
R=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)
export CORVIN_HOME="${CORVIN_E2E_HOME:-/tmp/corvin-e2e-home}"
mkdir -p "$CORVIN_HOME/tenants/_default/global"
# Everything else the backend can write stays inside the throwaway root too:
# XDG config, HOME, an ABSOLUTE audit anchor key (a relative one is refused),
# and FORGE_ROOT unset (the boot tripwire refuses an audit-chain redirect).
SANDBOX_ROOT="$(dirname "$CORVIN_HOME")/$(basename "$CORVIN_HOME")-env"
mkdir -p "$SANDBOX_ROOT/xdg" "$SANDBOX_ROOT/user-home"
export XDG_CONFIG_HOME="$SANDBOX_ROOT/xdg"
export HOME="$SANDBOX_ROOT/user-home"
export CORVIN_AUDIT_ANCHOR_KEY="$CORVIN_HOME/audit-anchor.key"
export CORVIN_TELEMETRY_OPTIN=false
unset FORGE_ROOT
export PYTHONPATH="$R/core/console:$R/core/gateway:$R/core/license:$R/core/compliance:$R/corvin_operator/forge:$R/corvin_operator/skill-forge"
PORT="${CORVIN_E2E_PORT:-8799}"
# Tool Forge's PROJECT scope walks up from the cwd to a git checkout, so started
# from web-next/ this backend read (and could write) the live checkout's
# .corvin/forge/tools. The throwaway home is no git checkout. The KB projector
# needs no flag here: kb_projection refuses the operator's KB for any
# non-canonical CORVIN_HOME (ADV-10, 2026-10-08).
cd "$CORVIN_HOME" || exit 1
exec "$R/core/console/.venv/bin/python" -m uvicorn corvin_gateway.app:app \
  --host 127.0.0.1 --port "$PORT" --log-level info
