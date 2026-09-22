"""E2E tests for Snapshot Manager (Phase 9b Stream 4)."""

import pytest
import tempfile
import os
from core.control_plane.snapshot_manager import SnapshotManager


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    async def log_event(self, event_type: str, payload: dict):
        """Log event."""
        self.events.append({"type": event_type, "payload": payload})


@pytest.mark.asyncio
async def test_create_snapshot():
    """Test creating a snapshot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        state = {
            "intent": {"route": "haiku"},
            "plugins": {"enabled": ["video_producer"]},
            "subsystems": {"learning": "enabled"},
            "overrides": {},
        }

        result = await manager.create_snapshot(
            state,
            "Daily Backup",
            "Regular backup before maintenance",
            "operator_1",
            "tenant_1",
        )

        assert "snapshot_id" in result
        assert "checksum" in result
        assert result["created_at"]
        assert audit.events[-1]["type"] == "snapshot_created"


@pytest.mark.asyncio
async def test_restore_snapshot():
    """Test restoring from snapshot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        state = {
            "intent": {"route": "haiku"},
            "plugins": {"enabled": ["video_producer"]},
            "subsystems": {"learning": "enabled"},
            "overrides": {},
        }

        snap = await manager.create_snapshot(
            state,
            "Backup",
            "Test backup",
            "operator_1",
            "tenant_1",
        )

        # Restore
        result = await manager.restore_snapshot(snap["snapshot_id"], "tenant_1", "admin_1")

        assert result["status"] == "restored"
        assert result["restored_state"]["plugins"]["enabled"] == ["video_producer"]
        assert audit.events[-1]["type"] == "snapshot_restored"


@pytest.mark.asyncio
async def test_checksum_verification():
    """Test checksum verification on restore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        state = {
            "intent": {},
            "plugins": {},
            "subsystems": {},
            "overrides": {},
        }

        snap = await manager.create_snapshot(
            state,
            "Test",
            "Test",
            "operator_1",
            "tenant_1",
        )

        # Corrupt snapshot in memory
        manager.snapshots[snap["snapshot_id"]] = manager.snapshots[snap["snapshot_id"]]._replace(
            checksum="corrupted_hash"
        )

        # Restore should fail
        with pytest.raises(ValueError, match="checksum_mismatch"):
            await manager.restore_snapshot(snap["snapshot_id"], "tenant_1", "admin_1")

        # Verify audit event
        assert audit.events[-1]["type"] == "snapshot_restore_failed"


@pytest.mark.asyncio
async def test_list_snapshots():
    """Test listing snapshots for a tenant."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        # Create multiple snapshots
        for i in range(3):
            await manager.create_snapshot(
                {
                    "intent": {},
                    "plugins": {},
                    "subsystems": {},
                    "overrides": {},
                },
                f"Snapshot {i}",
                f"Test snapshot {i}",
                "operator_1",
                "tenant_1",
            )

        # List
        snapshots = manager.list_snapshots("tenant_1")

        assert len(snapshots) == 3
        assert all("snapshot_id" in s for s in snapshots)
        assert snapshots[0]["name"] == "Snapshot 2"  # Most recent first


@pytest.mark.asyncio
async def test_get_snapshot_details():
    """Test getting snapshot details."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        state = {
            "intent": {"route": "opus"},
            "plugins": {},
            "subsystems": {},
            "overrides": {},
        }

        snap = await manager.create_snapshot(
            state,
            "Test",
            "Test snapshot",
            "operator_1",
            "tenant_1",
        )

        details = manager.get_snapshot_details(snap["snapshot_id"], "tenant_1")

        assert details["name"] == "Test"
        assert details["intent_state"]["route"] == "opus"
        assert details["created_by"] == "operator_1"


@pytest.mark.asyncio
async def test_delete_snapshot():
    """Test deleting a snapshot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        snap = await manager.create_snapshot(
            {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
            "Test",
            "Test",
            "operator_1",
            "tenant_1",
        )

        # Delete
        result = await manager.delete_snapshot(snap["snapshot_id"], "tenant_1", "admin_1")

        assert result["status"] == "deleted"
        assert audit.events[-1]["type"] == "snapshot_deleted"

        # Verify it's gone
        with pytest.raises(ValueError, match="not found"):
            manager.get_snapshot_details(snap["snapshot_id"], "tenant_1")


@pytest.mark.asyncio
async def test_tenant_isolation():
    """Test tenant isolation for snapshots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        # Create snapshots for different tenants
        snap_t1 = await manager.create_snapshot(
            {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
            "T1 Snapshot",
            "Tenant 1",
            "operator_1",
            "tenant_1",
        )

        snap_t2 = await manager.create_snapshot(
            {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
            "T2 Snapshot",
            "Tenant 2",
            "operator_1",
            "tenant_2",
        )

        # Tenant 1 should not access tenant 2's snapshot
        with pytest.raises(ValueError, match="Access denied"):
            manager.get_snapshot_details(snap_t2["snapshot_id"], "tenant_1")

        # Tenant 2 can access its own
        details = manager.get_snapshot_details(snap_t2["snapshot_id"], "tenant_2")
        assert details["name"] == "T2 Snapshot"

        # List should be isolated
        list_t1 = manager.list_snapshots("tenant_1")
        list_t2 = manager.list_snapshots("tenant_2")

        assert len(list_t1) == 1
        assert len(list_t2) == 1


@pytest.mark.asyncio
async def test_storage_stats():
    """Test storage statistics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        # Create multiple snapshots
        for i in range(2):
            await manager.create_snapshot(
                {"intent": {}, "plugins": {}, "subsystems": {}, "overrides": {}},
                f"Snapshot {i}",
                "Test",
                "operator_1",
                "tenant_1",
            )

        stats = manager.get_storage_stats("tenant_1")

        assert stats["snapshot_count"] == 2
        assert stats["total_size_bytes"] > 0
        assert stats["total_size_mb"] > 0


@pytest.mark.asyncio
async def test_large_state_snapshot():
    """Test snapshots with large state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        # Create large state (10k items)
        large_state = {
            "intent": {"routes": [f"route_{i}" for i in range(1000)]},
            "plugins": {"plugins": [f"plugin_{i}" for i in range(1000)]},
            "subsystems": {"subsystems": [f"sub_{i}" for i in range(1000)]},
            "overrides": {"overrides": [f"override_{i}" for i in range(5000)]},
        }

        result = await manager.create_snapshot(
            large_state,
            "Large",
            "Large state",
            "operator_1",
            "tenant_1",
        )

        assert "snapshot_id" in result
        assert result["size_bytes"] > 100000  # Should be > 100KB

        # Restore and verify
        restored = await manager.restore_snapshot(
            result["snapshot_id"], "tenant_1", "admin_1"
        )
        assert len(restored["restored_state"]["overrides"]["overrides"]) == 5000


@pytest.mark.asyncio
async def test_invalid_state_rejected():
    """Test invalid state is rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit = MockAuditBackend()
        manager = SnapshotManager(audit, tmpdir)

        # Invalid state: not a dict
        with pytest.raises(ValueError):
            await manager.create_snapshot(
                "not_a_dict",
                "Bad",
                "Bad state",
                "operator_1",
                "tenant_1",
            )
