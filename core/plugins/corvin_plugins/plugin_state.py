"""Distributed plugin state management (ADR-0345 k=4).

Handles checkpoint/restore of plugin state across hierarchy for recovery
and audit trail integration.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
from datetime import datetime

log = logging.getLogger("corvin.plugins.plugin_state")


@dataclass
class PluginStateSnapshot:
    """Immutable snapshot of plugin state at a point in time."""

    plugin_id: str
    timestamp_utc: str
    status: str
    budget_used: Dict[str, int]
    child_health: Dict[str, str]
    work_count: int
    avg_latency_ms: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for storage/audit."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to JSON for storage."""
        return json.dumps(self.to_dict())


class PluginStateStore:
    """Store and retrieve plugin state snapshots."""

    def __init__(self):
        """Initialize state store."""
        self.snapshots: Dict[str, list[PluginStateSnapshot]] = {}

    def checkpoint(self, plugin_id: str, plugin_node: Any) -> PluginStateSnapshot:
        """Capture current state of plugin.

        Args:
            plugin_id: Plugin identifier
            plugin_node: PluginNode instance

        Returns:
            PluginStateSnapshot immutable snapshot
        """
        timestamp = datetime.utcnow().isoformat() + "Z"

        # Capture child health
        child_health = {
            cid: status.status
            for cid, status in plugin_node.child_status.items()
        }

        snapshot = PluginStateSnapshot(
            plugin_id=plugin_id,
            timestamp_utc=timestamp,
            status=plugin_node.status,
            budget_used=plugin_node.current_budget_used.copy(),
            child_health=child_health,
            work_count=sum(
                s.work_count for s in plugin_node.child_status.values()
            ),
            avg_latency_ms=sum(
                s.avg_latency_ms for s in plugin_node.child_status.values()
            )
            / max(1, len(plugin_node.child_status)),
        )

        # Store
        if plugin_id not in self.snapshots:
            self.snapshots[plugin_id] = []

        self.snapshots[plugin_id].append(snapshot)

        # Keep only last 100 snapshots per plugin
        if len(self.snapshots[plugin_id]) > 100:
            self.snapshots[plugin_id] = self.snapshots[plugin_id][-50:]

        log.debug(f"Checkpointed state for {plugin_id}")
        return snapshot

    def restore(self, plugin_id: str, plugin_node: Any) -> bool:
        """Restore plugin state from latest snapshot.

        Args:
            plugin_id: Plugin identifier
            plugin_node: PluginNode instance to restore into

        Returns:
            True if restore succeeded, False otherwise
        """
        if plugin_id not in self.snapshots or len(self.snapshots[plugin_id]) == 0:
            log.warning(f"No snapshot found for {plugin_id}")
            return False

        snapshot = self.snapshots[plugin_id][-1]

        try:
            plugin_node.status = snapshot.status
            plugin_node.current_budget_used = snapshot.budget_used.copy()

            # Note: child_health restoration is informational only,
            # actual status is managed by registry
            log.debug(f"Restored state for {plugin_id} from {snapshot.timestamp_utc}")
            return True
        except Exception as e:
            log.error(f"Failed to restore state for {plugin_id}: {e}")
            return False

    def get_latest_snapshot(self, plugin_id: str) -> Optional[PluginStateSnapshot]:
        """Get most recent snapshot for plugin."""
        if plugin_id not in self.snapshots or len(self.snapshots[plugin_id]) == 0:
            return None
        return self.snapshots[plugin_id][-1]

    def get_snapshots(
        self, plugin_id: str, limit: int = 10
    ) -> list[PluginStateSnapshot]:
        """Get recent snapshots for plugin."""
        if plugin_id not in self.snapshots:
            return []
        return self.snapshots[plugin_id][-limit:]

    def clear(self, plugin_id: Optional[str] = None) -> None:
        """Clear snapshots for plugin or all plugins."""
        if plugin_id:
            if plugin_id in self.snapshots:
                del self.snapshots[plugin_id]
        else:
            self.snapshots.clear()
        log.debug(f"Cleared snapshots for {plugin_id or 'all plugins'}")
