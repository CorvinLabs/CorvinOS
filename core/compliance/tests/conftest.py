"""Shared fixtures for compliance-reports tests.

Each test runs against a sandboxed CORVIN_HOME tmpdir with a
hand-seeded audit chain — no production state touched.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve().parent
_PLUGIN = _THIS.parent
_REPO = _PLUGIN.parent.parent
_FORGE = _REPO / "operator" / "forge"

for p in (_PLUGIN, _FORGE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    (home / "global").symlink_to(home / "tenants" / "_default" / "global")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    return home


@pytest.fixture
def seed_chain(sandbox_home):
    """Helper that writes hash-chained events into the audit log."""
    from forge import paths as _forge_paths
    from forge import security_events as _security_events

    chain_path = _forge_paths.tenant_global_dir("_default") / "forge" / "audit.jsonl"
    chain_path.parent.mkdir(parents=True, exist_ok=True)

    def _add(event_type: str, *, severity: str | None = None, **details):
        _security_events.write_event(
            event_type=event_type,
            details=details,
            path=chain_path,
            severity=severity,
        )

    return _add


@pytest.fixture
def chain_path(sandbox_home):
    from forge import paths as _forge_paths
    return _forge_paths.tenant_global_dir("_default") / "forge" / "audit.jsonl"
