"""Sandbox for every personal-KB test: CORVIN_HOME, XDG, HOME, anchor key in a temp dir, FORGE_ROOT unset.

The scrypt cost is lowered for speed; wraps store their own parameters, so production cost is
unaffected (a wrap written here records n=2^10). ``test_crypto`` checks the production default.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "corvin_operator" / "forge"), str(_REPO / "corvin_operator" / "bridges" / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / "corvin"
    monkeypatch.setenv("CORVIN_HOME", str(root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    from core.personal_kb import capabilities, crypto, limits, session

    monkeypatch.setattr(crypto, "KDF_PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
    limits._reset_for_tests()
    session._reset_for_tests()
    capabilities._set_for_tests(None)
    yield root
    session._reset_for_tests()
    limits._reset_for_tests()


PW = "Lantern-Orchard-77-blue"
PW2 = "Quiet-Harbor-River-2026!"


def chain_records(root: Path) -> list[dict]:
    import json

    p = root / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_chain(root: Path) -> bool:
    from forge.security_events import verify_chain as _v  # type: ignore

    p = root / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    res = _v(p)
    return bool(res[0] if isinstance(res, tuple) else res)
