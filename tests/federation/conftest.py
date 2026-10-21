"""Fixtures for Federation (CONCEPT-0097 Phase 1) E2E tests.

Mirrors ``tests/forge_bundle/conftest.py``'s ``tmp_corvin_home`` pattern —
tests never touch the live install's ``~/.corvin`` or its audit chain.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest

TENANT_ID = "_default"


@pytest.fixture(autouse=True)
def _a2a_member_tier(monkeypatch):
    """``a2a.network`` is member-only (gate 28ad07b54). These suites exercise the federation
    PROTOCOL between two instances, not the licence verdict (which has its own tests): without this
    pin every catalog refresh answered 502 and the whole directory was red from the day the gate
    landed (measured 2026-10-21). Mirrors ``corvin_operator/bridges/shared/conftest.py``."""
    try:
        from corvin_operator.license import capability_api
    except ImportError:  # licensing absent: the gate refuses on its own (fail-closed)
        yield
        return
    monkeypatch.setattr(capability_api, "active_tier", lambda **_k: "member")
    try:
        import a2a_license_gate  # type: ignore[import-not-found]
        a2a_license_gate.reset_allow_cache()
    except Exception:  # noqa: BLE001 — the cache is a convenience
        pass
    yield


@pytest.fixture
def tmp_corvin_home(tmp_path: Path) -> Generator[Path, None, None]:
    home = tmp_path / "corvin_home"
    (home / "tenants" / TENANT_ID / "global" / "federation").mkdir(parents=True, exist_ok=True)

    orig_env = {k: os.environ.get(k) for k in (
        "CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "CORVIN_FORCE_SCOPE",
    )}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT_ID
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    os.environ["CORVIN_FORCE_SCOPE"] = "user"
    try:
        yield home
    finally:
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
