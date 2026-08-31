#!/usr/bin/env bash
# Reproducible launcher for the token-savings A/B benchmark.
# Sets the same env the console gateway uses, then runs the real benchmark.
# All args pass through, e.g.:  ./run_benchmark.sh --n 20
#                               ./run_benchmark.sh --dry-run
set -euo pipefail

# Resolve the repo root from this script's location (benchmark/token-savings/).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

export CORVIN_HOME="${CORVIN_HOME:-$REPO/.corvin}"
export PYTHONPATH="$REPO/core/console:$REPO/core/gateway:$REPO/core/license:$REPO/core/compliance:$REPO/operator/forge:$REPO/operator/skill-forge:$REPO/operator/bridges/shared:$REPO/operator:$REPO"

# Prefer the console venv (has the console + worker deps); fall back to python3.
PY="$REPO/core/console/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "python: $PY"
echo "CORVIN_HOME: $CORVIN_HOME"
exec "$PY" "$HERE/run_benchmark.py" "$@"
