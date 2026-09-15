"""Per-subtask E2E for Phase-1 compat resolver of AGENTS_SKIP_LIVE.

Covers CORVIN_AGENTS_SKIP_LIVE canonical + CORVIN_AGENTS_SKIP_LIVE
legacy alias + a smoke run of the test module under both env shapes.
"""
from __future__ import annotations

import importlib
import io
import os
import sys
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
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
    for k in ("CORVIN_AGENTS_SKIP_LIVE", "CORVIN_AGENTS_SKIP_LIVE"):
        os.environ.pop(k, None)


def _fresh_skip_live_resolver():
    """Re-import the test_engines_e2e module fresh and return its
    _resolve_skip_live function so each test starts with a clean
    process state for the deprecation-print path."""
    mod_path = REPO / "operator" / "bridges" / "shared" / "agents"
    sys.path.insert(0, str(mod_path.parent))
    sys.path.insert(0, str(mod_path))
    for mod in ("test_engines_e2e", "agents", "agents.claude_code",
                "agents.codex_cli"):
        sys.modules.pop(mod, None)
    try:
        m = importlib.import_module("agents.test_engines_e2e")
        return m._resolve_skip_live
    finally:
        sys.path.pop(0)
        sys.path.pop(0)


def test_corvin_skip_wins_no_warning() -> None:
    print("\n[CORVIN_AGENTS_SKIP_LIVE=1 → skip, no warning]")
    _clear_env()
    os.environ["CORVIN_AGENTS_SKIP_LIVE"] = "1"
    buf = io.StringIO()
    with redirect_stderr(buf):
        resolver = _fresh_skip_live_resolver()
        skip = resolver()
    t("skip == True", skip is True)
    t("no deprecation log",
      "deprecation" not in buf.getvalue().lower(),
      detail=f"stderr={buf.getvalue()!r}")
    _clear_env()


def test_legacy_skip_with_warning() -> None:
    print("\n[CORVIN_AGENTS_SKIP_LIVE=1 → skip]")
    _clear_env()
    os.environ["CORVIN_AGENTS_SKIP_LIVE"] = "1"
    buf = io.StringIO()
    with redirect_stderr(buf):
        resolver = _fresh_skip_live_resolver()
        skip = resolver()
    t("skip == True", skip is True)
    _clear_env()


def test_corvin_beats_legacy() -> None:
    print("\n[both env set, CORVIN=1 wins, no warning]")
    _clear_env()
    os.environ["CORVIN_AGENTS_SKIP_LIVE"] = "1"
    os.environ["CORVIN_AGENTS_SKIP_LIVE"] = "1"
    buf = io.StringIO()
    with redirect_stderr(buf):
        resolver = _fresh_skip_live_resolver()
        skip = resolver()
    t("skip == True", skip is True)
    t("no deprecation (legacy never read)",
      "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_unset_returns_false() -> None:
    print("\n[no env → skip == False]")
    _clear_env()
    resolver = _fresh_skip_live_resolver()
    t("skip == False", resolver() is False)
    _clear_env()


def test_non_one_value_returns_false() -> None:
    print("\n[CORVIN set to non-'1' → skip == False]")
    _clear_env()
    os.environ["CORVIN_AGENTS_SKIP_LIVE"] = "true"  # not '1', not honoured
    resolver = _fresh_skip_live_resolver()
    t("skip == False", resolver() is False)
    _clear_env()


def main() -> int:
    test_corvin_skip_wins_no_warning()
    test_legacy_skip_with_warning()
    test_corvin_beats_legacy()
    test_unset_returns_false()
    test_non_one_value_returns_false()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
