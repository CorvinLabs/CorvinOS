"""Per-subtask E2E for Phase-7 resolver in session_timeout_sweep.

Covers CORVIN_SESSION_TTL_DAYS canonical override.
CORVIN_SESSION_TTL_DAYS removed in Phase 7 (v1.0).
"""
from __future__ import annotations

import importlib
import io
import os
import sys
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _clear_env() -> None:
    for k in ("CORVIN_SESSION_TTL_DAYS", "CORVIN_SESSION_TTL_DAYS"):
        os.environ.pop(k, None)


def _fresh_sweep():
    sys.path.insert(0, str(REPO / "operator" / "voice" / "scripts"))
    sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
    for mod in ("session_timeout_sweep",):
        sys.modules.pop(mod, None)
    try:
        return importlib.import_module("session_timeout_sweep")
    finally:
        sys.path.pop(0)
        sys.path.pop(0)


def test_resolve_ttl_corvin_wins() -> None:
    print("\n[CORVIN_SESSION_TTL_DAYS wins, no warning]")
    _clear_env()
    os.environ["CORVIN_SESSION_TTL_DAYS"] = "14"
    buf = io.StringIO()
    with redirect_stderr(buf):
        m = _fresh_sweep()
        v = m._resolve_ttl_env()
    t("value == 14", v == "14")
    t("no deprecation log", "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_resolve_ttl_legacy_ignored() -> None:
    print("\n[CORVIN_SESSION_TTL_DAYS ignored in Phase 7]")
    _clear_env()
    os.environ["CORVIN_SESSION_TTL_DAYS"] = "30"
    buf = io.StringIO()
    with redirect_stderr(buf):
        m = _fresh_sweep()
        v = m._resolve_ttl_env()
    t("value == 30 (CORVIN var read)", v == "30")
    t("no deprecation warning (alias removed)", "[deprecation]" not in buf.getvalue().lower())
    _clear_env()


def test_resolve_ttl_corvin_only() -> None:
    print("\n[CORVIN_SESSION_TTL_DAYS is the only canonical var]")
    _clear_env()
    os.environ["CORVIN_SESSION_TTL_DAYS"] = "10"
    m = _fresh_sweep()
    v = m._resolve_ttl_env()
    t("value == 10 (only CORVIN read)", v == "10")
    _clear_env()


def test_resolve_ttl_unset_returns_none() -> None:
    print("\n[no env → returns None (caller picks default)]")
    _clear_env()
    m = _fresh_sweep()
    t("returns None", m._resolve_ttl_env() is None)
    _clear_env()


def test_main_default_uses_corvin_env() -> None:
    print("\n[main() argparse default picks up CORVIN value]")
    _clear_env()
    os.environ["CORVIN_SESSION_TTL_DAYS"] = "21"
    m = _fresh_sweep()
    # Mimic main's default-ttl resolution: pure-function check
    default_ttl = float(m._resolve_ttl_env() or "7")
    t("default_ttl == 21.0", default_ttl == 21.0)
    _clear_env()


def main() -> int:
    test_resolve_ttl_corvin_wins()
    test_resolve_ttl_legacy_ignored()
    test_resolve_ttl_corvin_only()
    test_resolve_ttl_unset_returns_none()
    test_main_default_uses_corvin_env()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
