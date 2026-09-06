"""Cross-tenant isolation tests — audit trail and learning events (Final Review Finding 1-3).

Verifies:
1. Audit trail isolation: emit() respects tenant_id parameter
2. Learning events isolation: EventStore uses tenant-scoped paths
3. Emission routing: emit() passes tenant_id through all code paths

Contracts these tests are written against (drift fixed 2026-09-03):

* ``core.tenants.validate_tenant_id`` accepts ``^[a-z0-9_]{1,64}$`` — tenant
  ids are ``tenant_a``-shaped, never hyphenated.
* ``core.paths.tenant_*`` resolve under ``Path.home()/.corvin`` (NOT
  ``CORVIN_HOME`` — a known divergence from ``forge.paths.corvin_home()``,
  flagged in the 2026-09-03 review). The tests therefore point ``HOME`` at a
  scratch directory and clear the ``VOICE_AUDIT_PATH`` / ``FORGE_AUDIT_PATH``
  overrides that the repo-root conftest installs, so the tenant-scoped path
  logic is what gets exercised.
* ``EventStore(tenant_id)`` is bound to ONE tenant; ``write_event(event,
  tenant_id)`` / ``read_events(tenant_id=...)`` reject any other tenant.
* ``EventEmitter(event_store)`` takes a store, not a path + tenant.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_persistence import EventStore
from core.learning.event_schema import LearningEvent, LearningEventType


@pytest.fixture
def scratch_home(tmp_path: Path, monkeypatch) -> Path:
    """``Path.home()`` → tmp; no audit-path env override; CORVIN_HOME aligned."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CORVIN_HOME", str(home / ".corvin"))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_AUDIT_PATH", raising=False)
    return home / ".corvin"


def _events(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().strip().split("\n") if l.strip()]


class TestAuditPathTenantAwareness:
    """Test 1: Audit path construction respects tenant_id."""

    def test_audit_path_default_tenant(self, scratch_home: Path) -> None:
        from core.awpkg.awpkg.audit import _audit_path

        path = _audit_path()
        assert path == scratch_home / "tenants" / "_default" / "audit.jsonl"

    def test_audit_path_custom_tenant(self, scratch_home: Path) -> None:
        from core.awpkg.awpkg.audit import _audit_path

        path = _audit_path("tenant_acme")
        assert path == scratch_home / "tenants" / "tenant_acme" / "audit.jsonl"

    def test_audit_path_isolation(self, scratch_home: Path) -> None:
        from core.awpkg.awpkg.audit import _audit_path

        paths = {_audit_path(t) for t in ("_default", "tenant_acme", "tenant_beta")}
        assert len(paths) == 3

    def test_hyphenated_tenant_id_matches_canonical_rule(self, scratch_home: Path) -> None:
        """core.tenants agrees with forge.tenants (^[a-z0-9_][a-z0-9_-]{0,62}$):
        a hyphen is fine after the first char; a leading hyphen, uppercase or
        traversal are not."""
        from core.tenants import validate_tenant_id

        assert validate_tenant_id("tenant-acme") == "tenant-acme"
        for bad in ("-acme", "Tenant", "a/b", "..", "x" * 64):
            with pytest.raises(ValueError):
                validate_tenant_id(bad)


class TestAuditEmissionWithTenantId:
    """Test 2: emit() routes events to the correct tenant audit file."""

    def test_emit_writes_to_tenant_specific_path(self, scratch_home: Path) -> None:
        from core.awpkg.awpkg.audit import emit

        emit("test.event_a", tenant_id="tenant_a", data="test_a")
        emit("test.event_b", tenant_id="tenant_b", data="test_b")

        file_a = scratch_home / "tenants" / "tenant_a" / "audit.jsonl"
        file_b = scratch_home / "tenants" / "tenant_b" / "audit.jsonl"
        assert file_a.exists(), f"Tenant A audit file not found: {file_a}"
        assert file_b.exists(), f"Tenant B audit file not found: {file_b}"

        events_a, events_b = _events(file_a), _events(file_b)
        assert len(events_a) == 1 and len(events_b) == 1
        assert events_a[0]["event_type"] == "test.event_a"
        assert events_b[0]["event_type"] == "test.event_b"

    def test_emit_default_tenant(self, scratch_home: Path) -> None:
        from core.awpkg.awpkg.audit import emit

        emit("test.default", package="test")
        default_file = scratch_home / "tenants" / "_default" / "audit.jsonl"
        assert default_file.exists()
        events = _events(default_file)
        assert len(events) == 1 and events[0]["event_type"] == "test.default"

    def test_emit_cross_tenant_isolation(self, scratch_home: Path) -> None:
        from core.awpkg.awpkg.audit import emit

        for i in range(5):
            emit(f"test.event_{i}", tenant_id="tenant_x", seq=i)
            emit(f"test.event_{i}", tenant_id="tenant_y", seq=i)

        events_x = _events(scratch_home / "tenants" / "tenant_x" / "audit.jsonl")
        events_y = _events(scratch_home / "tenants" / "tenant_y" / "audit.jsonl")
        assert len(events_x) == 5 and len(events_y) == 5
        assert all(e["event_type"].startswith("test.event_") for e in events_x + events_y)


class TestEventStorePathIsolation:
    """Test 3: EventStore uses tenant-scoped paths, not hardcoded global paths."""

    def test_eventstore_uses_tenant_learning_dir(self, scratch_home: Path) -> None:
        store = EventStore("tenant_test")
        assert store.tenant_id == "tenant_test"
        assert store.events_dir == scratch_home / "tenants" / "tenant_test" / "learning" / "events"
        assert "global" not in store.events_dir.parts, "Store should not use 'global' path"

    def test_eventstore_rejects_leading_hyphen_tenant(self, scratch_home: Path) -> None:
        with pytest.raises(ValueError):
            EventStore("-tenant")
        assert "tenant-test" in EventStore("tenant-test").events_dir.parts

    def test_eventstore_different_tenants_different_dirs(self, scratch_home: Path) -> None:
        store_a, store_b = EventStore("tenant_a"), EventStore("tenant_b")
        assert store_a.events_dir != store_b.events_dir
        assert "tenant_a" in store_a.events_dir.parts
        assert "tenant_b" in store_b.events_dir.parts

    @pytest.mark.asyncio
    async def test_eventstore_write_reads_tenant_scoped(self, scratch_home: Path, tmp_path: Path, monkeypatch) -> None:
        # ``write_event`` commits to the CORE audit writer first (ADR-0232/0233,
        # fail-closed) and verifies the commit through ``audit.audit_path()`` —
        # which honours the ``VOICE_AUDIT_PATH`` override the repo conftest sets.
        # Keep that override for this test: the learning-event DIRECTORY is what
        # is under test here, the chain location is the writer's contract.
        monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
        store_a, store_b = EventStore("tenant_a"), EventStore("tenant_b")

        def _ev(tid: str, n: int, skill: str, etype: LearningEventType) -> LearningEvent:
            return LearningEvent(
                event_type=etype,
                tenant_id=tid,
                instance_id=f"test-{n}",
                user_id=f"user{n}",
                skill_name=skill,
                session_id=f"session-{n}",
                timestamp_utc=datetime.utcnow(),
                event_id=f"event-{n}",
                payload={"score": 0.95},
            )

        # The core writer commits only for the CONTEXT tenant (CORVIN_TENANT_ID)
        # and the store verifies the commit — so each tenant writes in its own
        # context, exactly as two tenant processes would.
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_a")
        await store_a.write_event(_ev("tenant_a", 1, "skill-a", LearningEventType.CONFIDENCE_SCORE), "tenant_a")
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_b")
        await store_b.write_event(_ev("tenant_b", 2, "skill-b", LearningEventType.USER_FEEDBACK), "tenant_b")

        events_a = await store_a.read_events(tenant_id="tenant_a", limit=10)
        events_b = await store_b.read_events(tenant_id="tenant_b", limit=10)
        assert len(events_a) == 1 and events_a[0].skill_name == "skill-a"
        assert len(events_b) == 1 and events_b[0].skill_name == "skill-b"

        # a store bound to tenant_a must refuse tenant_b's data (fail-closed)
        with pytest.raises(ValueError):
            await store_a.read_events(tenant_id="tenant_b", limit=10)

        dir_a = scratch_home / "tenants" / "tenant_a" / "learning" / "events"
        dir_b = scratch_home / "tenants" / "tenant_b" / "learning" / "events"
        assert dir_a.exists() and dir_b.exists() and dir_a != dir_b


class TestEventEmitterTenantIntegration:
    """Test 4: EventEmitter is bound to the tenant of the store it wraps."""

    def test_emitter_wraps_tenant_bound_store(self, scratch_home: Path) -> None:
        emitter_a = EventEmitter(EventStore("tenant_a"))
        emitter_b = EventEmitter(EventStore("tenant_b"))
        try:
            assert emitter_a.store.tenant_id == "tenant_a"
            assert emitter_b.store.tenant_id == "tenant_b"
            assert emitter_a.store.events_dir != emitter_b.store.events_dir
        finally:
            for em in (emitter_a, emitter_b):
                stop = getattr(em, "stop", None)
                if callable(stop):
                    stop()

    def test_emitter_rejects_a_path(self, scratch_home: Path) -> None:
        with pytest.raises(TypeError):
            EventEmitter(scratch_home)  # type: ignore[arg-type]


class TestAuditChainHashingPerTenant:
    """Test 5: Audit chain hash integrity is maintained per tenant (no cross-contamination)."""

    def test_audit_chain_separate_per_tenant(self, scratch_home: Path) -> None:
        from core.awpkg.awpkg.audit import emit

        for i in range(3):
            emit("test.event", tenant_id="tenant_x", seq=i)
            emit("test.event", tenant_id="tenant_y", seq=i)

        events_x = _events(scratch_home / "tenants" / "tenant_x" / "audit.jsonl")
        events_y = _events(scratch_home / "tenants" / "tenant_y" / "audit.jsonl")
        assert len(events_x) == 3 and len(events_y) == 3

        chain_x = [e["hash"] for e in events_x]
        chain_y = [e["hash"] for e in events_y]
        assert chain_x != chain_y  # independent chains

        for chain in (events_x, events_y):
            for i, event in enumerate(chain):
                if i == 0:
                    assert not event.get("prev_hash"), "first record must have no predecessor"
                else:
                    assert event["prev_hash"] == chain[i - 1]["hash"]
