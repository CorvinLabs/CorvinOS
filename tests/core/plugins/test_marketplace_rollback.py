"""
Tests for Rollback & Snapshot Management (Phase 4).

Tests snapshot creation, restore, and rollback procedures.
"""

import pytest
import json
from pathlib import Path
from datetime import datetime

try:
    from core.plugins.marketplace_rollback import (
        PluginSnapshot,
        PluginSnapshotManager,
        RollbackProcedure,
    )
except ImportError:
    pytest.skip("Rollback module not available", allow_module_level=True)


@pytest.fixture
def snapshot_dir(tmp_path):
    """Temporary directory for snapshots."""
    return tmp_path / "snapshots"


@pytest.fixture
def manager(snapshot_dir):
    """Create a PluginSnapshotManager with temp directory."""
    return PluginSnapshotManager(snapshot_dir)


@pytest.fixture
def sample_manifest():
    """Sample plugin manifest."""
    return {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
    }


@pytest.fixture
def sample_config():
    """Sample plugin config."""
    return {
        "api_key": "key-123",
        "timeout": 30,
    }


@pytest.fixture
def sample_metadata():
    """Sample plugin metadata."""
    return {
        "author": "Test Author",
        "license": "Apache-2.0",
    }


class TestPluginSnapshot:
    """Test PluginSnapshot dataclass."""

    def test_create_snapshot(self, sample_manifest, sample_config, sample_metadata):
        """Should create a snapshot instance."""
        snap = PluginSnapshot(
            snapshot_id="test-snap-1",
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            created_at=datetime.utcnow(),
            snapshot_type="pre-install",
            reason="User initiated install",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
        )
        assert snap.snapshot_id == "test-snap-1"
        assert snap.plugin_id == "test-plugin"
        assert snap.rollback_available is True

    def test_snapshot_to_dict(self, sample_manifest, sample_config, sample_metadata):
        """Should convert snapshot to dict."""
        snap = PluginSnapshot(
            snapshot_id="test-snap-1",
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            created_at=datetime.utcnow(),
            snapshot_type="pre-install",
            reason="Test",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
        )
        data = snap.to_dict()
        assert data["snapshot_id"] == "test-snap-1"
        assert data["plugin_id"] == "test-plugin"
        assert "created_at" in data
        assert isinstance(data["created_at"], str)

    def test_snapshot_to_json(self, sample_manifest, sample_config, sample_metadata):
        """Should convert snapshot to JSON."""
        snap = PluginSnapshot(
            snapshot_id="test-snap-1",
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            created_at=datetime.utcnow(),
            snapshot_type="pre-install",
            reason="Test",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
        )
        json_str = snap.to_json()
        assert isinstance(json_str, str)
        data = json.loads(json_str)
        assert data["snapshot_id"] == "test-snap-1"


class TestSnapshotManager:
    """Test PluginSnapshotManager."""

    def test_create_snapshot(self, manager, sample_manifest, sample_config, sample_metadata):
        """Should create a snapshot and write to disk."""
        snap = manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
            snapshot_type="pre-install",
            reason="Test snapshot",
        )
        assert snap.snapshot_id is not None
        assert snap.plugin_id == "test-plugin"

    def test_snapshot_written_to_disk(self, manager, sample_manifest, sample_config, sample_metadata):
        """Snapshot should be written to disk."""
        snap = manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
        )

        # Check file exists
        snapshot_dir = manager.base_dir / "test-plugin"
        assert snapshot_dir.exists()
        snapshot_files = list(snapshot_dir.glob("*.json"))
        assert len(snapshot_files) > 0

    def test_get_snapshot(self, manager, sample_manifest, sample_config, sample_metadata):
        """Should load snapshot from disk."""
        snap1 = manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
            snapshot_type="pre-install",
        )

        # Load it back
        snap2 = manager.get_snapshot("test-plugin", snap1.snapshot_id)
        assert snap2 is not None
        assert snap2.snapshot_id == snap1.snapshot_id
        assert snap2.plugin_id == "test-plugin"

    def test_get_missing_snapshot(self, manager):
        """Should return None for missing snapshot."""
        snap = manager.get_snapshot("test-plugin", "nonexistent-snap")
        assert snap is None

    def test_list_snapshots(self, manager, sample_manifest, sample_config, sample_metadata):
        """Should list all snapshots for a plugin."""
        # Create 3 snapshots
        for i in range(3):
            manager.create_snapshot(
                plugin_id="test-plugin",
                tenant_id="tenant-1",
                manifest=sample_manifest,
                config=sample_config,
                metadata=sample_metadata,
                snapshot_type="pre-install" if i == 0 else "post-install",
            )

        snapshots = manager.list_snapshots("test-plugin")
        assert len(snapshots) == 3

    def test_list_snapshots_sorted_by_creation_time(self, manager, sample_manifest, sample_config, sample_metadata):
        """Snapshots should be sorted by creation time, newest first."""
        snap1 = manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
        )

        # Create another
        snap2 = manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
        )

        snapshots = manager.list_snapshots("test-plugin")
        assert snapshots[0].created_at >= snapshots[1].created_at

    def test_list_snapshots_empty_plugin(self, manager):
        """Should return empty list for plugin with no snapshots."""
        snapshots = manager.list_snapshots("nonexistent-plugin")
        assert snapshots == []

    def test_restore_snapshot(self, manager, sample_manifest, sample_config, sample_metadata):
        """Should be able to restore a snapshot."""
        snap = manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
        )

        success, message = manager.restore_snapshot("test-plugin", snap.snapshot_id)
        assert success is True
        assert "Successfully restored" in message

    def test_restore_missing_snapshot(self, manager):
        """Should fail to restore missing snapshot."""
        success, message = manager.restore_snapshot("test-plugin", "nonexistent-snap")
        assert success is False
        assert "not found" in message

    def test_cleanup_old_snapshots(self, manager, sample_manifest, sample_config, sample_metadata):
        """Should cleanup old snapshots, keeping recent ones."""
        # Create 15 snapshots
        for i in range(15):
            manager.create_snapshot(
                plugin_id="test-plugin",
                tenant_id="tenant-1",
                manifest=sample_manifest,
                config=sample_config,
                metadata=sample_metadata,
            )

        snapshots_before = manager.list_snapshots("test-plugin")
        assert len(snapshots_before) == 15

        # Cleanup, keeping 10
        deleted = manager.cleanup_old_snapshots("test-plugin", keep_count=10)
        assert deleted == 5

        snapshots_after = manager.list_snapshots("test-plugin")
        assert len(snapshots_after) == 10


class TestRollbackProcedure:
    """Test RollbackProcedure."""

    def test_automatic_rollback(self, manager, sample_manifest, sample_config, sample_metadata):
        """Should perform automatic rollback to pre-install snapshot."""
        # Create pre-install snapshot
        pre_install = manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
            snapshot_type="pre-install",
        )

        rollback = RollbackProcedure(manager)
        success, message = rollback.automatic_rollback(
            "test-plugin",
            reason="Installation failed"
        )
        assert success is True

    def test_automatic_rollback_no_pre_install(self, manager, sample_manifest, sample_config, sample_metadata):
        """Should fail if no pre-install snapshot exists."""
        # Create post-install snapshot only
        manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
            snapshot_type="post-install",
        )

        rollback = RollbackProcedure(manager)
        success, message = rollback.automatic_rollback(
            "test-plugin",
            reason="Installation failed"
        )
        assert success is False
        assert "not found" in message

    def test_manual_rollback(self, manager, sample_manifest, sample_config, sample_metadata):
        """Should perform manual rollback to specific snapshot."""
        snap = manager.create_snapshot(
            plugin_id="test-plugin",
            tenant_id="tenant-1",
            manifest=sample_manifest,
            config=sample_config,
            metadata=sample_metadata,
        )

        rollback = RollbackProcedure(manager)
        success, message = rollback.manual_rollback(
            "test-plugin",
            snap.snapshot_id,
            reason="User requested rollback"
        )
        assert success is True
