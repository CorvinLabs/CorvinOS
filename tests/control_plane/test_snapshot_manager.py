"""E2E tests for Snapshot Manager (Phase 9b Stream 4)."""

import dataclasses
import json
import pytest
import tempfile
import os
from core.control_plane.snapshot_manager import SnapshotManager, SnapshotRestoreNotImplemented


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

        # Restore: nothing can apply snapshot state, so it must FAIL — it used
        # to answer status "restored" while restoring nothing (2026-09-27).
        with pytest.raises(SnapshotRestoreNotImplemented):
            await manager.restore_snapshot(snap["snapshot_id"], "tenant_1", "admin_1")

        assert audit.events[-1]["type"] == "snapshot_restore_failed"
        assert audit.events[-1]["payload"]["reason"] == "not_implemented"
        assert not any(e["type"] == "snapshot_restored" for e in audit.events)


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
        # (Snapshot is a frozen dataclass, not a namedtuple)
        manager.snapshots[snap["snapshot_id"]] = dataclasses.replace(
            manager.snapshots[snap["snapshot_id"]], checksum="corrupted_hash"
        )

        # Restore should fail
        with pytest.raises(ValueError, match="checksum mismatch"):
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
        snapshots = await manager.list_snapshots("tenant_1")

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
        list_t1 = await manager.list_snapshots("tenant_1")
        list_t2 = await manager.list_snapshots("tenant_2")

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

        # The large state survives verbatim (a restore is not implemented —
        # see test_restore_snapshot — so verify the stored snapshot instead)
        details = manager.get_snapshot_details(result["snapshot_id"], "tenant_1")
        assert len(details["override_state"]["overrides"]) == 5000


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


@pytest.mark.asyncio
async def test_reload_then_create_does_not_overwrite(tmp_path):
    """After load_snapshots_from_disk a new manager must not reuse snap_000000
    (the counter restarted at 0 and overwrote the first snapshot on disk)."""
    m1 = SnapshotManager(MockAuditBackend(), str(tmp_path))
    first = await m1.create_snapshot({"intent": {"a": 1}}, "one", "", "op", "tenant_1")
    m2 = SnapshotManager(MockAuditBackend(), str(tmp_path))
    m2.load_snapshots_from_disk()
    second = await m2.create_snapshot({"intent": {"b": 2}}, "two", "", "op", "tenant_1")
    assert second["snapshot_id"] != first["snapshot_id"]
    m3 = SnapshotManager(MockAuditBackend(), str(tmp_path))
    m3.load_snapshots_from_disk()
    assert m3.get_snapshot_details(first["snapshot_id"], "tenant_1")["intent_state"] == {"a": 1}


@pytest.mark.asyncio
async def test_default_audit_goes_to_the_tenant_core_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_1")
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    from core.compliance import audit_chain_provider
    from corvin_operator.bridges.shared.paths import tenant_audit_chain

    monkeypatch.setattr(audit_chain_provider, "_CHAIN_WRITERS", {})
    manager = SnapshotManager(storage_path=str(tmp_path / "snaps"))
    snap = await manager.create_snapshot({"intent": {}}, "secret name", "", "op", "tenant_1")
    with pytest.raises(SnapshotRestoreNotImplemented):
        await manager.restore_snapshot(snap["snapshot_id"], "tenant_1", "admin")

    chain = tenant_audit_chain("tenant_1").read_text()
    assert "secret name" not in chain
    types = [json.loads(l)["event_type"] for l in chain.splitlines() if l.strip()]
    assert "snapshot_created" in types and "snapshot_restore_failed" in types
    log = await manager.get_audit_log("tenant_1")
    assert {r["details"]["snapshot_id"] for r in log} == {snap["snapshot_id"]}
    assert all(r["details"].get("checksum") for r in log)  # allowlisted, not dropped
