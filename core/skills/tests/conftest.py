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

import sys
from pathlib import Path

import pytest

# The core hash-chained audit writer lives at operator/bridges/shared/audit.py
# and is imported by BARE module name (``operator`` shadows the stdlib module),
# so the directory must be on sys.path. Without it these tests fail standalone
# with "core audit writer unavailable" and pass only when another suite in the
# same session happens to add the path first (2026-09-07 round-2 review).
_SHARED = Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


@pytest.fixture(autouse=True)
def _isolated_audit_chain(monkeypatch, tmp_path: Path):
    home = tmp_path / "corvin-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv(
        "VOICE_AUDIT_PATH", str(home / "global" / "forge" / "audit.jsonl")
    )
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    yield
