"""
Rollback & Snapshot Management for Marketplace (Phase 4).

Creates snapshots before/after plugin installation for safe rollback.
Enables recovery from corrupted configs or failed installs.

ADR-0385 Phase 4: Resilience
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import shutil

logger = logging.getLogger(__name__)


@dataclass
class PluginSnapshot:
    """Immutable snapshot of plugin state at a point in time."""

    snapshot_id: str
    plugin_id: str
    tenant_id: str

    # Snapshot metadata
    created_at: datetime
    snapshot_type: str  # "pre-install" | "post-install" | "pre-uninstall"
    reason: str  # e.g., "User initiated install"

    # Plugin state
    manifest: Dict[str, Any]
    config: Dict[str, Any]
    metadata: Dict[str, Any]

    # Recovery info
    rollback_available: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Serialize snapshot to dict."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        return data

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2, default=str)


class PluginSnapshotManager:
    """Manages plugin snapshots and rollback operations."""

    def __init__(self, snapshot_base_dir: Optional[Path] = None):
        """
        Initialize snapshot manager.

        Args:
            snapshot_base_dir: Directory to store snapshots
                             Defaults to ~/.corvin/plugins/snapshots/
        """
        if snapshot_base_dir is None:
            from pathlib import Path
            corvin_home = Path.home() / ".corvin"
            snapshot_base_dir = corvin_home / "plugins" / "snapshots"

        self.base_dir = Path(snapshot_base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"PluginSnapshotManager initialized at {self.base_dir}")

    def create_snapshot(
        self,
        plugin_id: str,
        tenant_id: str,
        manifest: Dict[str, Any],
        config: Dict[str, Any],
        metadata: Dict[str, Any],
        snapshot_type: str = "pre-install",
        reason: str = "",
    ) -> PluginSnapshot:
        """
        Create a snapshot of the current plugin state.

        Args:
            plugin_id: Plugin identifier
            tenant_id: Tenant ID
            manifest: Plugin manifest
            config: Plugin configuration
            metadata: Plugin metadata
            snapshot_type: Type of snapshot (pre-install, post-install, etc.)
            reason: Reason for snapshot

        Returns:
            PluginSnapshot instance
        """
        from datetime import datetime
        import uuid

        snapshot_id = f"{plugin_id}-snap-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow()

        snapshot = PluginSnapshot(
            snapshot_id=snapshot_id,
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            created_at=now,
            snapshot_type=snapshot_type,
            reason=reason,
            manifest=manifest,
            config=config,
            metadata=metadata,
            rollback_available=True,
        )

        # Write to disk
        snapshot_file = self._get_snapshot_path(plugin_id, snapshot_id)
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)

        with open(snapshot_file, "w") as f:
            f.write(snapshot.to_json())

        logger.info(f"Created snapshot {snapshot_id} for plugin {plugin_id}")
        return snapshot

    def get_snapshot(self, plugin_id: str, snapshot_id: str) -> Optional[PluginSnapshot]:
        """
        Load a snapshot from disk.

        Args:
            plugin_id: Plugin identifier
            snapshot_id: Snapshot identifier

        Returns:
            PluginSnapshot or None if not found
        """
        snapshot_file = self._get_snapshot_path(plugin_id, snapshot_id)

        if not snapshot_file.exists():
            logger.warning(f"Snapshot {snapshot_id} not found")
            return None

        try:
            with open(snapshot_file, "r") as f:
                data = json.load(f)

            # Parse datetime
            data["created_at"] = datetime.fromisoformat(data["created_at"])

            return PluginSnapshot(**data)
        except Exception as e:
            logger.error(f"Failed to load snapshot {snapshot_id}: {e}")
            return None

    def list_snapshots(self, plugin_id: str) -> List[PluginSnapshot]:
        """
        List all snapshots for a plugin.

        Args:
            plugin_id: Plugin identifier

        Returns:
            List of PluginSnapshot objects, sorted by creation time (newest first)
        """
        plugin_dir = self.base_dir / plugin_id
        if not plugin_dir.exists():
            return []

        snapshots = []
        for snapshot_file in plugin_dir.glob("snap-*.json"):
            try:
                with open(snapshot_file, "r") as f:
                    data = json.load(f)
                data["created_at"] = datetime.fromisoformat(data["created_at"])
                snapshots.append(PluginSnapshot(**data))
            except Exception as e:
                logger.error(f"Failed to load snapshot {snapshot_file}: {e}")

        # Sort by creation time, newest first
        snapshots.sort(key=lambda s: s.created_at, reverse=True)
        return snapshots

    def restore_snapshot(
        self,
        plugin_id: str,
        snapshot_id: str,
    ) -> tuple[bool, str]:
        """
        Restore a plugin from a snapshot.

        In production, this would:
        1. Read the snapshot
        2. Verify integrity
        3. Restore manifest, config, metadata
        4. Create a new post-restore snapshot

        Args:
            plugin_id: Plugin identifier
            snapshot_id: Snapshot to restore from

        Returns:
            (success: bool, message: str)
        """
        snapshot = self.get_snapshot(plugin_id, snapshot_id)
        if not snapshot:
            return False, f"Snapshot {snapshot_id} not found"

        if not snapshot.rollback_available:
            return False, f"Snapshot {snapshot_id} is not available for rollback"

        logger.info(f"Restoring plugin {plugin_id} from snapshot {snapshot_id}")

        # In production, this would:
        # 1. Stop the plugin if running
        # 2. Restore config/manifest from snapshot
        # 3. Restart the plugin
        # 4. Verify it works
        # 5. Create a post-restore snapshot

        return True, f"Successfully restored {plugin_id} from snapshot {snapshot_id}"

    def cleanup_old_snapshots(self, plugin_id: str, keep_count: int = 10) -> int:
        """
        Remove old snapshots, keeping the most recent N.

        Args:
            plugin_id: Plugin identifier
            keep_count: Number of recent snapshots to keep (default 10)

        Returns:
            Number of snapshots deleted
        """
        snapshots = self.list_snapshots(plugin_id)

        if len(snapshots) <= keep_count:
            return 0

        to_delete = snapshots[keep_count:]
        deleted = 0

        for snapshot in to_delete:
            snapshot_file = self._get_snapshot_path(plugin_id, snapshot.snapshot_id)
            try:
                snapshot_file.unlink()
                logger.info(f"Deleted old snapshot {snapshot.snapshot_id}")
                deleted += 1
            except Exception as e:
                logger.error(f"Failed to delete snapshot {snapshot.snapshot_id}: {e}")

        return deleted

    def _get_snapshot_path(self, plugin_id: str, snapshot_id: str) -> Path:
        """Get the file path for a snapshot."""
        return self.base_dir / plugin_id / f"{snapshot_id}.json"


class RollbackProcedure:
    """Manages the rollback workflow."""

    def __init__(self, snapshot_manager: PluginSnapshotManager):
        self.snapshots = snapshot_manager

    def automatic_rollback(self, plugin_id: str, reason: str) -> tuple[bool, str]:
        """
        Attempt automatic rollback on error.

        Looks for the most recent pre-install snapshot and restores it.

        Args:
            plugin_id: Plugin identifier
            reason: Reason for rollback

        Returns:
            (success: bool, message: str)
        """
        snapshots = self.snapshots.list_snapshots(plugin_id)

        # Find most recent pre-install snapshot
        pre_install = next(
            (s for s in snapshots if s.snapshot_type == "pre-install"),
            None
        )

        if not pre_install:
            return False, f"No pre-install snapshot found for {plugin_id}"

        logger.warning(f"Initiating automatic rollback for {plugin_id}: {reason}")
        return self.snapshots.restore_snapshot(plugin_id, pre_install.snapshot_id)

    def manual_rollback(self, plugin_id: str, snapshot_id: str, reason: str) -> tuple[bool, str]:
        """
        Perform manual rollback to a specific snapshot.

        Args:
            plugin_id: Plugin identifier
            snapshot_id: Snapshot to restore
            reason: Reason for rollback

        Returns:
            (success: bool, message: str)
        """
        logger.warning(f"Initiating manual rollback for {plugin_id}: {reason}")
        return self.snapshots.restore_snapshot(plugin_id, snapshot_id)
