#!/usr/bin/env bash
# Canonical invocation of the C-1 path composition guard (measuring mode).
# git lists the tracked .py files; the Python scanner spawns no process itself.
# Usage: scripts/path_composition_guard.sh [--report PATH | --check |
#        --write-baseline --reason-file PATH]
set -euo pipefail
cd "$(dirname "$0")/.."
git ls-files -z -- '*.py' | python3 scripts/path_composition_guard.py --files-from - -0 "$@"
