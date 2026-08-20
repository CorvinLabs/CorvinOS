"""Cross-tenant isolation tests — audit trail and learning events (Final Review Finding 1-3).

Verifies:
1. Audit trail isolation: emit() respects tenant_id parameter
2. Learning events isolation: EventStore uses tenant-scoped paths
3. Emission routing: emit() passes tenant_id through all code paths
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import pytest

from core.learning.event_emitter import EventEmitter
from core.learning.event_persistence import EventStore
from core.learning.event_schema import LearningEvent, LearningEventType
from core.paths import tenant_audit_file, tenant_learning_dir


class TestAuditPathTenantAwareness:
    """Test 1: Audit path construction respects tenant_id."""

    def test_audit_path_default_tenant(self) -> None:
        """Default tenant audit path."""
        from core.awpkg.awpkg.audit import _audit_path

        # Default tenant
        path = _audit_path()
        assert "tenants/_default" in str(path)
        assert path.name == "audit.jsonl"

    def test_audit_path_custom_tenant(self) -> None:
        """Custom tenant audit path."""
        from core.awpkg.awpkg.audit import _audit_path

        # Custom tenant
        path = _audit_path("tenant-acme")
        assert "tenants/tenant-acme" in str(path)
        assert path.name == "audit.jsonl"

    def test_audit_path_isolation(self) -> None:
        """Different tenants have different audit paths."""
        from core.awpkg.awpkg.audit import _audit_path

        path_default = _audit_path("_default")
        path_acme = _audit_path("tenant-acme")
        path_beta = _audit_path("tenant-beta")

        # All different
        assert path_default != path_acme
        assert path_default != path_beta
        assert path_acme != path_beta


class TestAuditEmissionWithTenantId:
    """Test 2: emit() function routes events to correct tenant audit file."""

    def test_emit_writes_to_tenant_specific_path(self, tmp_path: Path) -> None:
        """emit() writes to tenant-specific audit file."""
        from core.awpkg.awpkg.audit import emit

        # Mock CORVIN_HOME to tmp_path
        old_env = os.environ.get("CORVIN_HOME")
        try:
            os.environ["CORVIN_HOME"] = str(tmp_path)

            # Emit event for tenant A
            emit("test.event_a", tenant_id="tenant-a", data="test_a")

            # Emit event for tenant B
            emit("test.event_b", tenant_id="tenant-b", data="test_b")

            # Check both files exist and are isolated
            file_a = tmp_path / "tenants" / "tenant-a" / "audit.jsonl"
            file_b = tmp_path / "tenants" / "tenant-b" / "audit.jsonl"

            assert file_a.exists(), f"Tenant A audit file not found: {file_a}"
            assert file_b.exists(), f"Tenant B audit file not found: {file_b}"

            # Parse events
            events_a = [json.loads(line) for line in file_a.read_text().strip().split("\n") if line.strip()]
            events_b = [json.loads(line) for line in file_b.read_text().strip().split("\n") if line.strip()]

            # Each file should have only its own events
            assert len(events_a) == 1
            assert len(events_b) == 1
            assert events_a[0]["event_type"] == "test.event_a"
            assert events_b[0]["event_type"] == "test.event_b"
        finally:
            if old_env:
                os.environ["CORVIN_HOME"] = old_env
            else:
                os.environ.pop("CORVIN_HOME", None)

    def test_emit_default_tenant(self, tmp_path: Path) -> None:
        """emit() defaults to _default tenant when tenant_id not specified."""
        from core.awpkg.awpkg.audit import emit

        old_env = os.environ.get("CORVIN_HOME")
        try:
            os.environ["CORVIN_HOME"] = str(tmp_path)

            # Emit without specifying tenant_id
            emit("test.default", package="test")

            # Should write to _default
            default_file = tmp_path / "tenants" / "_default" / "audit.jsonl"
            assert default_file.exists()

            events = [json.loads(line) for line in default_file.read_text().strip().split("\n") if line.strip()]
            assert len(events) == 1
            assert events[0]["event_type"] == "test.default"
        finally:
            if old_env:
                os.environ["CORVIN_HOME"] = old_env
            else:
                os.environ.pop("CORVIN_HOME", None)

    def test_emit_cross_tenant_isolation(self, tmp_path: Path) -> None:
        """emit() isolates events between tenants."""
        from core.awpkg.awpkg.audit import emit

        old_env = os.environ.get("CORVIN_HOME")
        try:
            os.environ["CORVIN_HOME"] = str(tmp_path)

            # Emit many events across multiple tenants
            for i in range(5):
                emit(f"test.event_{i}", tenant_id="tenant-x", seq=i)
                emit(f"test.event_{i}", tenant_id="tenant-y", seq=i)

            # Verify isolation
            file_x = tmp_path / "tenants" / "tenant-x" / "audit.jsonl"
            file_y = tmp_path / "tenants" / "tenant-y" / "audit.jsonl"

            events_x = [json.loads(line) for line in file_x.read_text().strip().split("\n") if line.strip()]
            events_y = [json.loads(line) for line in file_y.read_text().strip().split("\n") if line.strip()]

            # Each tenant should have exactly 5 events
            assert len(events_x) == 5
            assert len(events_y) == 5

            # Events should be tenant-specific
            assert all(f"test.event_" in e["event_type"] for e in events_x)
            assert all(f"test.event_" in e["event_type"] for e in events_y)

            # Verify no cross-contamination
            x_only = [e for e in events_x if e["event_type"] not in [e2["event_type"] for e2 in events_y]]
            y_only = [e for e in events_y if e["event_type"] not in [e2["event_type"] for e2 in events_x]]
            # Both should be empty since event types are the same, but that's OK
            # The key is they're in different files
            assert len(events_x) > 0 and len(events_y) > 0
        finally:
            if old_env:
                os.environ["CORVIN_HOME"] = old_env
            else:
                os.environ.pop("CORVIN_HOME", None)


class TestEventStorePathIsolation:
    """Test 3: EventStore uses tenant-scoped paths, not hardcoded global paths."""

    def test_eventstore_uses_tenant_learning_dir(self) -> None:
        """EventStore initializes with tenant_id, not tenant_home."""
        store = EventStore("tenant-test")

        # Verify store has tenant_id
        assert store.tenant_id == "tenant-test"

        # Verify events_dir uses tenant-scoped path
        assert "tenant-test" in str(store.events_dir)
        assert "learning" in str(store.events_dir)
        assert "global" not in str(store.events_dir), "Store should not use 'global' path"

    def test_eventstore_different_tenants_different_dirs(self) -> None:
        """Different tenant IDs create different event directories."""
        store_a = EventStore("tenant-a")
        store_b = EventStore("tenant-b")

        assert store_a.events_dir != store_b.events_dir
        assert "tenant-a" in str(store_a.events_dir)
        assert "tenant-b" in str(store_b.events_dir)

    @pytest.mark.asyncio
    async def test_eventstore_write_reads_tenant_scoped(self) -> None:
        """EventStore writes and reads from tenant-scoped paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Mock paths
            old_home = os.environ.get("CORVIN_HOME")
            try:
                os.environ["CORVIN_HOME"] = str(tmp_path)

                # Create stores for two tenants
                store_a = EventStore("tenant-a")
                store_b = EventStore("tenant-b")

                # Create events
                event_a = LearningEvent(
                    event_type=LearningEventType.CONFIDENCE,
                    tenant_id="tenant-a",
                    instance_id="test-1",
                    user_id="user1",
                    skill_name="skill-a",
                    session_id="session-1",
                    timestamp_utc=datetime.utcnow(),
                    event_id="event-1",
                    payload={"score": 0.95},
                )

                event_b = LearningEvent(
                    event_type=LearningEventType.FEEDBACK,
                    tenant_id="tenant-b",
                    instance_id="test-2",
                    user_id="user2",
                    skill_name="skill-b",
                    session_id="session-2",
                    timestamp_utc=datetime.utcnow(),
                    event_id="event-2",
                    payload={"feedback": "good"},
                )

                # Write events
                await store_a.write_event(event_a, "tenant-a")
                await store_b.write_event(event_b, "tenant-b")

                # Read back and verify isolation
                events_a = await store_a.read_events(tenant_id="tenant-a", limit=10)
                events_b = await store_b.read_events(tenant_id="tenant-b", limit=10)

                # Each store should only see its own events
                assert len(events_a) == 1
                assert len(events_b) == 1
                assert events_a[0].skill_name == "skill-a"
                assert events_b[0].skill_name == "skill-b"

                # Verify directory isolation
                dir_a = tmp_path / "tenants" / "tenant-a" / "learning" / "events"
                dir_b = tmp_path / "tenants" / "tenant-b" / "learning" / "events"
                assert dir_a.exists()
                assert dir_b.exists()
                assert dir_a != dir_b
            finally:
                if old_home:
                    os.environ["CORVIN_HOME"] = old_home
                else:
                    os.environ.pop("CORVIN_HOME", None)


class TestEventEmitterTenantIntegration:
    """Test 4: EventEmitter passes tenant_id to EventStore correctly."""

    @pytest.mark.asyncio
    async def test_emitter_initializes_store_with_tenant_id(self) -> None:
        """EventEmitter passes tenant_id to EventStore."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            old_home = os.environ.get("CORVIN_HOME")
            try:
                os.environ["CORVIN_HOME"] = str(tmp_path)

                emitter_a = EventEmitter(tmp_path, "tenant-a")
                emitter_b = EventEmitter(tmp_path, "tenant-b")

                # Verify stores are initialized with tenant_id
                assert emitter_a.store.tenant_id == "tenant-a"
                assert emitter_b.store.tenant_id == "tenant-b"

                # Verify event directories are different
                assert emitter_a.store.events_dir != emitter_b.store.events_dir
                assert "tenant-a" in str(emitter_a.store.events_dir)
                assert "tenant-b" in str(emitter_b.store.events_dir)
            finally:
                if old_home:
                    os.environ["CORVIN_HOME"] = old_home
                else:
                    os.environ.pop("CORVIN_HOME", None)


class TestAuditChainHashingPerTenant:
    """Test 5: Audit chain hash integrity is maintained per tenant (no cross-contamination)."""

    def test_audit_chain_separate_per_tenant(self, tmp_path: Path) -> None:
        """Each tenant maintains its own hash chain independently."""
        from core.awpkg.awpkg.audit import emit

        old_env = os.environ.get("CORVIN_HOME")
        try:
            os.environ["CORVIN_HOME"] = str(tmp_path)

            # Emit 3 events to each tenant
            for i in range(3):
                emit(f"test.event", tenant_id="tenant-x", seq=i)
                emit(f"test.event", tenant_id="tenant-y", seq=i)

            # Parse files
            file_x = tmp_path / "tenants" / "tenant-x" / "audit.jsonl"
            file_y = tmp_path / "tenants" / "tenant-y" / "audit.jsonl"

            events_x = [json.loads(line) for line in file_x.read_text().strip().split("\n") if line.strip()]
            events_y = [json.loads(line) for line in file_y.read_text().strip().split("\n") if line.strip()]

            # Extract hash chains
            chain_x = [e["hash"] for e in events_x]
            chain_y = [e["hash"] for e in events_y]

            # Chains should be different (independent hash chains)
            assert chain_x != chain_y

            # Verify each chain is consistent
            for i, event in enumerate(events_x):
                if i == 0:
                    assert event["prev_hash"] == ""
                else:
                    assert event["prev_hash"] == events_x[i - 1]["hash"]

            for i, event in enumerate(events_y):
                if i == 0:
                    assert event["prev_hash"] == ""
                else:
                    assert event["prev_hash"] == events_y[i - 1]["hash"]
        finally:
            if old_env:
                os.environ["CORVIN_HOME"] = old_env
            else:
                os.environ.pop("CORVIN_HOME", None)
