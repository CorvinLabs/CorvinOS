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
_FORGE = _REPO / "corvin_operator" / "forge"
_SHARED = _REPO / "corvin_operator" / "bridges" / "shared"

for p in (_PLUGIN, _FORGE, _SHARED):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    (home / "global").symlink_to(home / "tenants" / "_default" / "global")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    _provision_instance_key(monkeypatch)
    return home


def _provision_instance_key(monkeypatch) -> None:
    """Give the sandbox install the instance key a real install already has.

    Every audit record is signed with the instance key (ADR-0153 M3). On a
    fresh home the FIRST signed write mints that key and records
    ``instance.key_rotated`` from a deferred thread (the writer's lock is held
    while signing; d0e049466 binds that record to the raising chain). The tests
    here count and index the records they seed, so that extra record — whose
    chain position depends on thread timing — made them fail, or pass by luck.
    The key is provisioned up front, without its creation record, so the chain
    holds exactly what each test seeds.
    """
    import instance_identity  # noqa: PLC0415 - bridges/shared, on sys.path above

    with monkeypatch.context() as m:
        m.setattr(instance_identity, "_audit_ibc", lambda *_a, **_k: None)
        instance_identity.ensure_instance_key()


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
