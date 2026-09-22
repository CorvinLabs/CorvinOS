"""E2E tests for Snapshot System (Phase 9b Stream 4 — Snapshots)."""

import pytest
import tempfile
import os
import hashlib
from datetime import datetime
from core.control_plane.snapshot_manager import SnapshotManager, Snapshot
from core.control_plane.state_capture import StateCapture


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        """Initialize mock."""
        self.events = []

    async def log_event(self, event_type: str, payload: dict):
        """Log event."""
        self.events.append({
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })


@pytest.fixture
def audit_backend():
    """Provide mock audit backend."""
    return MockAuditBackend()


@pytest.fixture
def snapshot_manager(audit_backend):
    """Provide snapshot manager with temp storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SnapshotManager(audit_backend, tmpdir)
        yield manager


class TestSnapshotCreation:
    """Test snapshot creation workflow."""

    @pytest.mark.asyncio
    async def test_create_snapshot_basic(self, snapshot_manager, audit_backend):
        """Test creating a basic snapshot."""
        control_plane_state = {
            "intent": {"route": "haiku"},
            "plugins": {"enabled": ["video_producer"]},
            "subsystems": {"learning": "enabled"},
            "overrides": {},
        }

        result = await snapshot_manager.create_snapshot(
            control_plane_state=control_plane_state,
            name="Daily Backup",
            description="Regular backup before maintenance",
            creator_id="operator_1",
            tenant_id="tenant_1",
        )

        assert "snapshot_id" in result
        assert "checksum" in result
        assert "created_at" in result
        assert result["size_bytes"] > 0

        # Verify audit event
        assert len(audit_backend.events) > 0
        assert audit_backend.events[-1]["type"] == "snapshot_created"
        assert audit_backend.events[-1]["payload"]["snapshot_id"] == result["snapshot_id"]

    @pytest.mark.asyncio
    async def test_create_snapshot_with_all_state(self, snapshot_manager):
        """Test creating snapshot with all state components."""
        control_plane_state = {
            "intent": {
                "route": "opus",
                "confidence": 0.95,
                "routing_model": "claude-v3",
            },
            "plugins": {
                "enabled": ["video_producer", "learning_loop"],
                "disabled": ["legacy_plugin"],
            },
            "subsystems": {
                "learning": "enabled",
                "audit": "enabled",
                "marketplace": "disabled",
            },
            "overrides": {
                "force_model": "haiku",
                "disable_learning": False,
            },
        }

        result = await snapshot_manager.create_snapshot(
            control_plane_state,
            "Full State Snapshot",
            "Complete state capture",
            "admin_1",
            "tenant_1",
        )

        assert result["snapshot_id"] is not None
        assert result["checksum"] is not None

        # Verify snapshot is stored
        details = await snapshot_manager.get_snapshot(
            result["snapshot_id"], "tenant_1"
        )
        assert details is not None
        assert details["intent_state"]["route"] == "opus"
        assert "video_producer" in details["plugin_state"]["enabled"]


class TestSnapshotRetrieval:
    """Test snapshot listing and retrieval."""

    @pytest.mark.asyncio
    async def test_list_snapshots(self, snapshot_manager):
        """Test listing snapshots for a tenant."""
        # Create multiple snapshots
        for i in range(3):
            await snapshot_manager.create_snapshot(
                {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
                f"Snapshot {i}",
                f"Test snapshot {i}",
                "operator_1",
                "tenant_1",
            )

        snapshots = await snapshot_manager.list_snapshots("tenant_1")
        assert len(snapshots) == 3

        # Verify sorting (most recent first)
        for i in range(len(snapshots) - 1):
            assert snapshots[i]["created_at"] >= snapshots[i + 1]["created_at"]

    @pytest.mark.asyncio
    async def test_list_snapshots_tenant_isolation(self, snapshot_manager):
        """Test that list_snapshots respects tenant isolation."""
        # Create snapshots for different tenants
        await snapshot_manager.create_snapshot(
            {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
            "Tenant 1 Snapshot",
            "Test",
            "operator_1",
            "tenant_1",
        )

        await snapshot_manager.create_snapshot(
            {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
            "Tenant 2 Snapshot",
            "Test",
            "operator_2",
            "tenant_2",
        )

        # Verify isolation
        tenant_1_snapshots = await snapshot_manager.list_snapshots("tenant_1")
        tenant_2_snapshots = await snapshot_manager.list_snapshots("tenant_2")

        assert len(tenant_1_snapshots) == 1
        assert len(tenant_2_snapshots) == 1
        assert tenant_1_snapshots[0]["created_by"] == "operator_1"
        assert tenant_2_snapshots[0]["created_by"] == "operator_2"

    @pytest.mark.asyncio
    async def test_get_snapshot_details(self, snapshot_manager):
        """Test retrieving full snapshot details."""
        state = {
            "intent": {"model": "claude-opus"},
            "plugins": {"enabled": ["video_producer"]},
            "subsystems": {"learning": "enabled"},
            "overrides": {},
        }

        result = await snapshot_manager.create_snapshot(
            state, "Test Snapshot", "Test", "operator_1", "tenant_1"
        )

        details = await snapshot_manager.get_snapshot(result["snapshot_id"], "tenant_1")
        assert details is not None
        assert details["intent_state"]["model"] == "claude-opus"
        assert details["checksum"] == result["checksum"]


class TestSnapshotRestoration:
    """Test snapshot restoration workflow."""

    @pytest.mark.asyncio
    async def test_restore_snapshot_basic(self, snapshot_manager, audit_backend):
        """Test basic snapshot restoration."""
        state = {
            "intent": {"route": "haiku"},
            "plugins": {"enabled": ["video_producer"]},
            "subsystems": {"learning": "enabled"},
            "overrides": {},
        }

        created = await snapshot_manager.create_snapshot(
            state, "Backup", "Test", "operator_1", "tenant_1"
        )

        result = await snapshot_manager.restore_snapshot(
            created["snapshot_id"], "tenant_1", "admin_1"
        )

        assert result["status"] == "restored"
        assert result["restored_state"]["plugins"]["enabled"] == ["video_producer"]

        # Verify audit event
        restore_events = [
            e for e in audit_backend.events if e["type"] == "snapshot_restored"
        ]
        assert len(restore_events) > 0

    @pytest.mark.asyncio
    async def test_restore_snapshot_checksum_validation(self, snapshot_manager, audit_backend):
        """Test that checksum is validated before restoration."""
        state = {
            "intent": {},
            "plugins": {},
            "subsystems": {},
            "overrides": {},
        }

        created = await snapshot_manager.create_snapshot(
            state, "Test", "Test", "operator_1", "tenant_1"
        )

        # Corrupt the snapshot in memory
        snapshot_manager.snapshots[created["snapshot_id"]] = Snapshot(
            snapshot_id=created["snapshot_id"],
            timestamp=snapshot_manager.snapshots[created["snapshot_id"]].timestamp,
            name="Corrupted",
            description="Corrupted snapshot",
            intent_state={"corrupted": True},
            plugin_state={},
            subsystem_state={},
            override_state={},
            checksum="wrong_checksum_12345",
            tenant_id="tenant_1",
            created_by="operator_1",
            size_bytes=0,
        )

        # Attempt restoration should fail
        with pytest.raises(ValueError, match="checksum mismatch"):
            await snapshot_manager.restore_snapshot(
                created["snapshot_id"], "tenant_1", "admin_1"
            )

        # Verify failure event
        fail_events = [
            e for e in audit_backend.events
            if e["type"] == "snapshot_restore_failed"
        ]
        assert len(fail_events) > 0


class TestSnapshotIntegrity:
    """Test snapshot integrity verification."""

    @pytest.mark.asyncio
    async def test_checksum_consistency(self, snapshot_manager):
        """Test that checksums are computed consistently."""
        state = {
            "intent": {},
            "plugins": {},
            "subsystems": {},
            "overrides": {},
        }

        result1 = await snapshot_manager.create_snapshot(
            state, "Test", "Test", "op1", "tenant_1"
        )

        result2 = await snapshot_manager.create_snapshot(
            state, "Test", "Test", "op1", "tenant_1"
        )

        # Same state → same checksum
        assert result1["checksum"] == result2["checksum"]

    @pytest.mark.asyncio
    async def test_checksum_differs_on_state_change(self, snapshot_manager):
        """Test that checksums differ for different states."""
        state1 = {
            "intent": {"model": "haiku"},
            "plugins": {},
            "subsystems": {},
            "overrides": {},
        }

        state2 = {
            "intent": {"model": "opus"},
            "plugins": {},
            "subsystems": {},
            "overrides": {},
        }

        result1 = await snapshot_manager.create_snapshot(
            state1, "Test 1", "Test", "op1", "tenant_1"
        )

        result2 = await snapshot_manager.create_snapshot(
            state2, "Test 2", "Test", "op1", "tenant_1"
        )

        # Different state → different checksum
        assert result1["checksum"] != result2["checksum"]


class TestSnapshotDeletion:
    """Test snapshot deletion workflow."""

    @pytest.mark.asyncio
    async def test_delete_snapshot(self, snapshot_manager, audit_backend):
        """Test deleting a snapshot."""
        created = await snapshot_manager.create_snapshot(
            {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
            "Snapshot to Delete",
            "Will be deleted",
            "operator_1",
            "tenant_1",
        )

        result = await snapshot_manager.delete_snapshot(
            created["snapshot_id"], "tenant_1", "admin_1"
        )

        assert result["snapshot_id"] == created["snapshot_id"]
        assert result["status"] == "deleted"

        # Verify snapshot is gone
        snapshots = await snapshot_manager.list_snapshots("tenant_1")
        assert created["snapshot_id"] not in [s["snapshot_id"] for s in snapshots]

        # Verify audit event
        delete_events = [
            e for e in audit_backend.events if e["type"] == "snapshot_deleted"
        ]
        assert len(delete_events) > 0


class TestStateCaptureIntegration:
    """Test StateCapture integration with snapshots."""

    def test_state_capture_basic(self):
        """Test basic state capture."""
        capture = StateCapture()

        plugin_registry = {
            "video_producer": {"enabled": True, "version": "1.0.0"},
            "learning": {"enabled": True, "version": "1.0.0"},
        }

        state = capture.capture_plugins(plugin_registry)
        assert state["total_plugins"] == 2
        assert "video_producer" in state["plugins"]

    def test_state_capture_serialization(self):
        """Test state capture serialization."""
        capture = StateCapture()

        plugin_registry = {
            "test_plugin": {"enabled": True, "version": "1.0.0", "boot_layer": "bundled"},
        }

        capture.capture_plugins(plugin_registry)
        capture.capture_config({"key1": "value1", "secret_key": "hidden"})

        serialized = capture.serialize_state(include_config=False)
        assert "plugins" in serialized
        assert "config" not in serialized

        serialized_with_config = capture.serialize_state(include_config=True)
        assert "config" in serialized_with_config

    def test_state_capture_secret_filtering(self):
        """Test that secrets are filtered during serialization."""
        capture = StateCapture()

        config = {
            "api_key": "secret123",
            "password": "pass123",
            "normal_setting": "value",
        }

        capture.capture_config(config)
        serialized = capture.serialize_state(include_config=True)

        # Secrets should be filtered
        assert "api_key" not in serialized["config"]["config"]
        assert "password" not in serialized["config"]["config"]
        assert "normal_setting" in serialized["config"]["config"]


class TestSnapshotConcurrency:
    """Test concurrent snapshot operations."""

    @pytest.mark.asyncio
    async def test_concurrent_snapshot_creation(self, snapshot_manager):
        """Test creating multiple snapshots concurrently."""
        import asyncio

        tasks = []
        for i in range(5):
            task = snapshot_manager.create_snapshot(
                {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
                f"Concurrent Snapshot {i}",
                f"Test {i}",
                f"operator_{i}",
                "tenant_1",
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks)
        assert len(results) == 5

        # Verify all snapshots are stored
        snapshots = await snapshot_manager.list_snapshots("tenant_1")
        assert len(snapshots) == 5

        # Verify all have unique IDs
        snapshot_ids = [s["snapshot_id"] for s in snapshots]
        assert len(set(snapshot_ids)) == 5
