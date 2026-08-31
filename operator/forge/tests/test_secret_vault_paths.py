"""Per-subtask E2E for forge.secret_vault path resolution.

Phase 7 closure: only CORVIN_SECRET_VAULT is honoured. The legacy
CORVIN_SECRET_VAULT alias is silently ignored — kept here as a
regression guard against accidental re-introduction.
"""
from __future__ import annotations

import importlib
import io
import os
import sys
import tempfile
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
    for k in ("CORVIN_SECRET_VAULT", "XDG_CONFIG_HOME"):
        os.environ.pop(k, None)


def _fresh_secret_vault():
    sys.path.insert(0, str(REPO / "operator" / "forge"))
    for mod in ("forge.secret_vault", "forge"):
        sys.modules.pop(mod, None)
    try:
        return importlib.import_module("forge.secret_vault")
    finally:
        sys.path.pop(0)


def test_corvin_env_wins_no_warning() -> None:
    print("\n[CORVIN_SECRET_VAULT wins, no warning]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "vault.json"
        os.environ["CORVIN_SECRET_VAULT"] = str(path)
        buf = io.StringIO()
        with redirect_stderr(buf):
            sv = _fresh_secret_vault()
            p = sv.default_vault_path()
        t("path == CORVIN value", p == path, detail=f"got {p}")
        t("no deprecation log",
          "deprecation" not in buf.getvalue().lower(),
          detail=f"stderr={buf.getvalue()!r}")
    _clear_env()


def test_legacy_env_silently_ignored() -> None:
    print("\n[CORVIN_SECRET_VAULT — Phase 7 closure complete]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        vault = Path(td) / "vault.json"
        os.environ["CORVIN_SECRET_VAULT"] = str(vault)
        buf = io.StringIO()
        with redirect_stderr(buf):
            sv = _fresh_secret_vault()
            p = sv.default_vault_path()
        # Phase 7: CORVIN_SECRET_VAULT is the canonical env var.
        t("CORVIN_SECRET_VAULT honored", p == vault, detail=f"got {p}")
        t("no deprecation log",
          "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_corvin_wins_when_both_set() -> None:
    print("\n[both env set → CORVIN wins, legacy is dead]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        new = Path(td) / "new.json"
        os.environ["CORVIN_SECRET_VAULT"] = str(new)
        buf = io.StringIO()
        with redirect_stderr(buf):
            sv = _fresh_secret_vault()
            p = sv.default_vault_path()
        t("path == CORVIN value", p == new, detail=f"got {p}")
        t("no deprecation log",
          "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_xdg_default_when_no_env() -> None:
    print("\n[no env → XDG_CONFIG_HOME default]")
    _clear_env()
    with tempfile.TemporaryDirectory() as td:
        os.environ["XDG_CONFIG_HOME"] = td
        buf = io.StringIO()
        with redirect_stderr(buf):
            sv = _fresh_secret_vault()
            p = sv.default_vault_path()
        t("path under XDG/corvin-voice",
          p == Path(td) / "corvin-voice" / "secrets.json",
          detail=f"got {p}")
        t("no deprecation log",
          "deprecation" not in buf.getvalue().lower())
    _clear_env()


def test_path_expansion() -> None:
    print("\n[~ and $VAR are expanded in env-var value]")
    _clear_env()
    home = os.environ.get("HOME", "/root")
    os.environ["CORVIN_SECRET_VAULT"] = "~/corvin-vault.json"
    sv = _fresh_secret_vault()
    p = sv.default_vault_path()
    t("~ expanded", str(p) == f"{home}/corvin-vault.json")
    _clear_env()


def main() -> int:
    test_corvin_env_wins_no_warning()
    test_legacy_env_silently_ignored()
    test_corvin_wins_when_both_set()
    test_xdg_default_when_no_env()
    test_path_expansion()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
