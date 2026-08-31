#!/usr/bin/env bash
# test_setup_hermes_pib.sh — regression guard for the Hermes install bundle.
#
# Covers the two confirmed release-blocking bugs in setup-hermes-pib.sh:
#
#   1. RAM gate must NOT depend on `bc`. `bc` is absent on Git-Bash (the only
#      way to run this on Windows) and on minimal containers; when it was
#      missing the old `(( $(echo "$ram < 6.0" | bc -l) ))` evaluated false and
#      a 4 GB box wrongly pulled qwen3:8b (~5 GB) that Ollama couldn't serve.
#      This test extracts the REAL select_model_for_ram from the script and
#      runs it with `bc` stripped from PATH — so re-introducing bc fails here.
#
#   2. The RAM threshold must stay in lock-step with the Python SSOT
#      hermes_bootstrap.py::select_model_for_ram (< 6 GB → qwen3:1.7b,
#      ≥ 6 GB → qwen3:8b). Drift between the two is a silent config bug.
#
# Self-contained: no pytest, no network. Exits non-zero on any failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/setup-hermes-pib.sh"

fails=0
note() { printf '  %s\n' "$*"; }
check() { # <ram> <expected-model>
  local ram="$1" exp="$2" got
  got="$(run_gate "$ram")"
  if [[ "$got" == "$exp" ]]; then
    note "ok    ram=${ram} -> ${got}"
  else
    note "FAIL  ram=${ram} -> ${got} (want ${exp})"
    fails=$((fails + 1))
  fi
}

# Extract the real select_model_for_ram function body from the script (no
# duplication → the test exercises the shipped code, not a copy) and invoke it
# in a bash whose PATH deliberately EXCLUDES `bc`.
run_gate() {
  local ram="$1"
  # A minimal PATH that keeps awk/coreutils but drops any dir that only exists
  # to provide bc. We simply build a PATH from awk's dir + /usr/bin + /bin and
  # then shadow `bc` with a stub that always fails, proving independence.
  awk_dir="$(dirname "$(command -v awk)")"
  local stub_dir="$TMP_STUB"
  PATH="${stub_dir}:${awk_dir}:/usr/bin:/bin" bash -c '
    set -uo pipefail
    '"$FUNC_SRC"'
    select_model_for_ram "'"$ram"'"
  '
}

# --- setup: pull the function source out of the shipped script ---------------
if [[ ! -f "$TARGET" ]]; then
  echo "FAIL: cannot find $TARGET"; exit 1
fi
FUNC_SRC="$(awk '/^select_model_for_ram\(\) \{/{f=1} f{print} f&&/^\}/{exit}' "$TARGET")"
if [[ -z "$FUNC_SRC" ]]; then
  echo "FAIL: could not extract select_model_for_ram from $TARGET"; exit 1
fi

# stub dir with a `bc` that always errors — if the function still needs bc the
# gate will misbehave and the assertions below catch it.
TMP_STUB="$(mktemp -d)"
trap 'rm -rf "$TMP_STUB"' EXIT
printf '#!/bin/sh\nexit 127\n' > "$TMP_STUB/bc"
chmod +x "$TMP_STUB/bc"

echo "== RAM gate (bc deliberately broken) =="
check 3.9  qwen3:1.7b   # 4 GB box — the exact dead-fallback incident
check 5.9  qwen3:1.7b   # just below the 6 GB boundary
check 6.0  qwen3:8b     # boundary: SSOT says < 6 is fast, so 6.0 → 8b
check 6.1  qwen3:8b
check 16.0 qwen3:8b
check 4    qwen3:1.7b   # integer input

# --- SSOT parity: threshold matches hermes_bootstrap.py ----------------------
echo "== SSOT parity with hermes_bootstrap.py =="
BOOT="$(find "$SCRIPT_DIR/.." -name hermes_bootstrap.py 2>/dev/null | head -1)"
if [[ -n "$BOOT" ]] && command -v python3 >/dev/null 2>&1; then
  py_boundary="$(python3 - "$BOOT" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("hb", sys.argv[1])
hb = importlib.util.module_from_spec(spec); spec.loader.exec_module(hb)
# Print the model just-below and at the 6.0 boundary
print(hb.select_model_for_ram(5.9), hb.select_model_for_ram(6.0))
PY
)"
  read -r py_below py_at <<<"$py_boundary"
  sh_below="$(run_gate 5.9)"; sh_at="$(run_gate 6.0)"
  if [[ "$py_below" == "$sh_below" && "$py_at" == "$sh_at" ]]; then
    note "ok    parity: <6→${sh_below}  ≥6→${sh_at}  (matches Python SSOT)"
  else
    note "FAIL  drift: bash(<6=${sh_below},≥6=${sh_at}) vs python(<6=${py_below},≥6=${py_at})"
    fails=$((fails + 1))
  fi
else
  note "skip  hermes_bootstrap.py not found or no python3 — parity check skipped"
fi

echo
if (( fails == 0 )); then
  echo "PASS: setup-hermes-pib RAM gate is bc-free and SSOT-aligned"
  exit 0
else
  echo "FAIL: ${fails} assertion(s) failed"
  exit 1
fi
