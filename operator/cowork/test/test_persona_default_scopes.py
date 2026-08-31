"""E2E: every persona has the right forge_default_scope (or none)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PERSONAS_DIR = REPO / "operator" / "cowork" / "personas"

EXPECTED = {
    "coder":         "project",
    "research":      "session",
    "assistant":     "session",
    "inbox":         "user",
    "homeassistant": "user",
    # forge: NOT set (caller-dependent)
    # browser/jarvis/local-coder/orchestrator-haiku removed in f1e3246
}

PASS = 0; FAIL = 0


def t(label, ok, *, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — '+detail) if detail else ''}")
    if ok: PASS += 1
    else: FAIL += 1


def main() -> int:
    print("[persona forge_default_scope contract]")
    for name, expected_scope in EXPECTED.items():
        f = PERSONAS_DIR / f"{name}.json"
        if not f.is_file():
            t(f"{name}.json exists", False, detail=str(f))
            continue
        data = json.loads(f.read_text())
        actual = data.get("forge_default_scope")
        t(f"{name}: forge_default_scope == {expected_scope!r}",
          actual == expected_scope,
          detail=f"got {actual!r}")
    # forge persona must NOT carry a default scope
    forge_data = json.loads((PERSONAS_DIR / "forge.json").read_text())
    t("forge persona has no forge_default_scope",
      "forge_default_scope" not in forge_data,
      detail=f"got {forge_data.get('forge_default_scope')!r}")
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
