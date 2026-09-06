"""Isolation for the skills-package tests (ACP registry + learning emitter).

``core.learning.event_store.EventStore.write_event`` commits a content-free
record to the CORE hash-chained audit writer BEFORE it appends to disk
(ADR-0314, audit-first). A learning test that constructs an ``EventStore`` on a
``tmp_path`` would therefore still write to the operator's LIVE audit chain
unless the writer is redirected — the exact test-contaminates-live-state class
the repo-root ``conftest.py`` tripwire exists for.

Every test here runs with the chain, the corvin home and the process tenant
pointed at a throw-away directory. A test that needs a specific root sets its
own ``monkeypatch.setenv`` afterwards and wins.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_audit_chain(monkeypatch, tmp_path: Path):
    home = tmp_path / "corvin-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    yield
