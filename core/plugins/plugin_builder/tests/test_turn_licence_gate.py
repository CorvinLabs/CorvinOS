"""ADR-0701 G4 — ``/plugin-builder`` is a member capability, fail-closed.

The conftest pins ``member`` for the rest of this suite; every test here
re-pins the tier (or breaks the licensing module) explicitly. The gate,
its audit record and its verdict are the real ``require_capability``.

Regression (adversarial review 2026-09-28): ``turn.py`` imported the gate at
module level and, on ``ImportError``, bound a no-op ``require_capability`` —
so any process that could not load the licensing module ran the builder for
free. ``test_unimportable_licensing_module_denies`` fails on that code.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from plugin_builder import session_store, turn

_REPO = Path(__file__).resolve().parents[4]
TENANT, KEY = "_default", "licence-gate-key"


@pytest.fixture(autouse=True)
def _clean_session():
    session_store.clear(TENANT, KEY)
    yield
    session_store.clear(TENANT, KEY)


def _pin(monkeypatch, tier: str) -> None:
    import corvin_operator.license.capability_api as ca
    monkeypatch.setattr(ca, "active_tier", lambda **_k: tier)


def test_free_tier_is_refused_and_starts_nothing(monkeypatch):
    _pin(monkeypatch, "free")
    reply = turn.command("", tenant_id=TENANT, session_key=KEY)
    assert "member seat" in reply
    assert session_store.get(TENANT, KEY) is None


def test_member_tier_starts_the_interview(monkeypatch):
    _pin(monkeypatch, "member")
    reply = turn.command("", tenant_id=TENANT, session_key=KEY)
    assert "Plugin-Builder" in reply and "member seat" not in reply
    assert session_store.get(TENANT, KEY) is not None


def test_enforcement_error_denies(monkeypatch):
    import corvin_operator.license.capability_api as ca

    def _boom(*_a, **_k):
        raise RuntimeError("tier store unreadable")

    monkeypatch.setattr(ca, "require_capability", _boom)
    reply = turn.command("", tenant_id=TENANT, session_key=KEY)
    assert "refused" in reply
    assert session_store.get(TENANT, KEY) is None


def test_unimportable_licensing_module_denies(tmp_path):
    """A fresh interpreter in which the licensing module cannot be imported
    AT ALL — the exact condition the removed module-level fallback covered."""
    probe = r"""
import importlib.abc, sys
class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "corvin_operator.license.capability_api":
            raise ImportError("blocked by test")
        return None
sys.meta_path.insert(0, _Block())
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from plugin_builder import session_store, turn
reply = turn.command("", tenant_id="_default", session_key="probe")
started = session_store.get("_default", "probe") is not None
print("STARTED" if started else "DENIED")
print(reply)
"""
    env = {**os.environ, "CORVIN_HOME": str(tmp_path / "home"),
           "VOICE_AUDIT_PATH": str(tmp_path / "audit.jsonl")}
    out = subprocess.run(
        [sys.executable, "-c", probe, str(_REPO), str(_REPO / "core" / "plugins")],
        env=env, capture_output=True, text=True, timeout=120,
    )
    lines = out.stdout.strip().splitlines()
    assert lines, out.stderr[-2000:]
    assert lines[0] == "DENIED", out.stdout + out.stderr[-2000:]
    assert "licensing module could not be loaded" in out.stdout
