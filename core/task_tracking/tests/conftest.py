"""Hermetic CORVIN_HOME for every task-tracking test.

Without it these tests resolved ``_default`` against the operator's live
``~/.corvin`` — the migration tests read the live ``task_registry.json`` and
wrote into the live ``tasks.db`` — and the chain writes landed in the live
tenant audit chain.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_corvin_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "corvin_home"))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    from core.task_tracking import service

    service.chain_writer = service._default_chain_writer
    yield
    service.chain_writer = service._default_chain_writer
