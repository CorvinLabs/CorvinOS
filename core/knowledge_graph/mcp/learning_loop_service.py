"""Learning Loop KG MCP Service — Index management + status computation.

This service manages the learning loop index and provides:
1. Auto-indexing hook (on learning event write)
2. Status computation (active/dormant/stale/degrading)
3. Health score updates (rolling mean aggregation)
4. MCP tool backends (list, health_trend, detect_conflicts)

Integration:
- Hooked into EventStore.write_event() (Stream 2.2)
- Called by manifest refresh handler (Stream 2.4)
- Backend for MCP tools (Stream 2.3)

ADR-0907: KG MCP Learning-Loop Index Service
ADR-0314: Learning Infrastructure
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Literal
from dataclasses import replace

from core.knowledge_graph.storage.learning_loop_index import (
    LearningLoopIndexEntry,
    LearningLoopIndexStorage,
)

logger = logging.getLogger(__name__)

Status = Literal["active", "dormant", "stale", "degrading"]


# ── Status Computation ──────────────────────────────────────────────────────


def compute_status(
    last_event_age_hours: float,
    health_score: float,
    health_threshold: Optional[float],
    event_count_7d: int,
) -> Status:
    """Compute loop status based on metrics.

    Status transitions:
    - "active": last event < 24h
    - "dormant": last event 24h–7d
    - "stale": last event > 7d
    - "degrading": health < threshold AND event_count >= 10

    Args:
        last_event_age_hours: Hours since last event
        health_score: Rolling mean health score (0.0–1.0)
        health_threshold: Threshold for degrading status (optional)
        event_count_7d: Events in last 7 days

    Returns:
        Status string
    """
    # Dormancy/stale takes precedence
    if last_event_age_hours < 24:
        status = "active"
    elif last_event_age_hours < 168:  # 7 days
        status = "dormant"
    else:
        status = "stale"

    # Degrading requires both conditions: health low AND sufficient events
    if (
        health_threshold is not None
        and health_score < health_threshold
        and event_count_7d >= 10
    ):
        return "degrading"

    return status


# ── Health Score Aggregation ────────────────────────────────────────────────


class HealthScoreAggregator:
    """Compute rolling mean health scores for learning loops."""

    @staticmethod
    def rolling_mean_7d(
        signals: List[float],
        aggregation_type: str = "rolling_mean_7d",
    ) -> float:
        """Compute rolling mean confidence/signal over 7 days.

        Args:
            signals: List of signal values (confidence scores, latencies, etc.)
            aggregation_type: Type of aggregation (for documentation)

        Returns:
            Rolling mean (0.0–1.0 or clamped)
        """
        if not signals:
            return 0.0
        mean = sum(signals) / len(signals)
        return max(0.0, min(1.0, mean))

    @staticmethod
    def percentile_p95(
        signals: List[float],
        aggregation_type: str = "percentile_p95",
    ) -> float:
        """Compute 95th percentile (for latency-based health).

        Args:
            signals: List of signal values
            aggregation_type: Type of aggregation

        Returns:
            95th percentile (0.0–1.0)
        """
        if not signals:
            return 0.0
        sorted_signals = sorted(signals)
        idx = int(len(sorted_signals) * 0.95)
        if idx >= len(sorted_signals):
            idx = len(sorted_signals) - 1
        return max(0.0, min(1.0, sorted_signals[idx]))

    @classmethod
    def aggregate(cls, signals: List[float], aggregation: str) -> float:
        """Delegate to the appropriate aggregator.

        Args:
            signals: List of signal values
            aggregation: Aggregation method ("rolling_mean_7d", "percentile_p95")

        Returns:
            Aggregated health score (0.0–1.0)
        """
        if aggregation == "percentile_p95":
            return cls.percentile_p95(signals)
        else:  # default: rolling_mean_7d
            return cls.rolling_mean_7d(signals)


# ── Learning Loop Service ───────────────────────────────────────────────────


class LearningLoopService:
    """Main service for learning loop indexing + management."""

    def __init__(self, tenant_id: str, db_path: Optional[Path] = None):
        """Initialize service.

        Args:
            tenant_id: Tenant identifier
            db_path: Custom LevelDB path (optional)

        Raises:
            ValueError: If tenant_id invalid or db inaccessible
        """
        from core.tenants.validation import validate_tenant_id

        self.tenant_id = validate_tenant_id(tenant_id)
        self._storage = LearningLoopIndexStorage(tenant_id, db_path)

    def insert_from_manifest(
        self,
        plugin_id: str,
        loop_id: str,
        description: str,
        event_source: str,
        feedback_types: List[str],
        aggregation: str,
        health_threshold: Optional[float],
        dormancy_alert_hours: int,
        owner_skill: Optional[str],
    ) -> LearningLoopIndexEntry:
        """Create a new index entry from manifest definition.

        Args:
            plugin_id: Plugin identifier
            loop_id: Loop identifier
            description: Human-readable description
            event_source: Event source path ("SkillExecutedEvent.confidence_score")
            feedback_types: List of supported feedback types
            aggregation: Aggregation method ("rolling_mean_7d", "percentile_p95")
            health_threshold: Optional health threshold (0.0–1.0)
            dormancy_alert_hours: Hours before dormancy alert
            owner_skill: Optional owner skill identifier

        Returns:
            Created index entry

        Raises:
            ValueError: If arguments invalid
        """
        now = datetime.utcnow()
        entry = LearningLoopIndexEntry(
            tenant_id=self.tenant_id,
            plugin_id=plugin_id,
            loop_id=loop_id,
            description=description,
            event_source=event_source,
            feedback_types=feedback_types,
            aggregation=aggregation,
            health_threshold=health_threshold,
            dormancy_alert_hours=dormancy_alert_hours,
            owner_skill=owner_skill,
            last_event_ts=now,
            event_count_7d=0,
            health_score=0.5,  # default middle score
            status="active",
            created_at=now,
            updated_at=now,
        )
        self._storage.insert(entry)
        return entry

    def update_on_event(
        self,
        plugin_id: str,
        loop_id: str,
        feedback_signal: Optional[float] = None,
        event_type: Optional[str] = None,
    ) -> Optional[LearningLoopIndexEntry]:
        """Update index entry on new learning event.

        This is called by the auto-indexing hook in event_persistence.py
        when a learning event is written. It updates:
        - last_event_ts
        - event_count_7d
        - health_score (rolling mean)
        - status (via compute_status)

        Args:
            plugin_id: Plugin identifier
            loop_id: Loop identifier
            feedback_signal: Optional signal value (confidence, latency, etc.)
            event_type: Optional event type (for logging)

        Returns:
            Updated entry, or None if not found
        """
        entry = self._storage.get(plugin_id, loop_id)
        if entry is None:
            logger.warning(
                f"Index entry not found for {self.tenant_id}:{plugin_id}:{loop_id}"
            )
            return None

        now = datetime.utcnow()
        age_hours = (now - entry.last_event_ts).total_seconds() / 3600.0

        # Update event count (7-day rolling window)
        new_event_count_7d = entry.event_count_7d + 1

        # Update health score (rolling mean)
        # In a real implementation, this would aggregate from audit chain signals
        # For now, use the feedback signal directly or keep the current score
        new_health_score = entry.health_score
        if feedback_signal is not None:
            # Simple rolling mean: (old_score + new_signal) / 2
            new_health_score = (entry.health_score + feedback_signal) / 2.0
            new_health_score = max(0.0, min(1.0, new_health_score))

        # Compute new status
        new_status = compute_status(
            age_hours,
            new_health_score,
            entry.health_threshold,
            new_event_count_7d,
        )

        # Update entry
        updated_entry = replace(
            entry,
            last_event_ts=now,
            event_count_7d=new_event_count_7d,
            health_score=new_health_score,
            status=new_status,
            updated_at=now,
        )

        self._storage.insert(updated_entry)

        # Audit-log status transitions
        if new_status != entry.status:
            self._audit_status_transition(
                plugin_id, loop_id, entry.status, new_status
            )

        return updated_entry

    def list_loops(
        self,
        plugin_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> List[LearningLoopIndexEntry]:
        """List all learning loops for this tenant (with optional filters).

        Args:
            plugin_id: Filter by plugin (optional)
            status: Filter by status (optional)
            limit: Maximum entries to return

        Returns:
            List of matching entries
        """
        return self._storage.list_all(plugin_id=plugin_id, status=status, limit=limit)

    def get_health_trend(
        self,
        plugin_id: str,
        loop_id: str,
        days: int = 7,
    ) -> Optional[dict]:
        """Get health trend over N days for a loop.

        This queries the audit chain for learning events related to this loop
        and computes the health trend.

        Args:
            plugin_id: Plugin identifier
            loop_id: Loop identifier
            days: Number of days to look back (default 7)

        Returns:
            Dict with timestamps, health_scores, event_counts, or None if not found
        """
        entry = self._storage.get(plugin_id, loop_id)
        if entry is None:
            return None

        # In a real implementation, this would query the audit chain
        # For now, return a stub response
        now = datetime.utcnow()
        since = now - timedelta(days=days)

        return {
            "loop_id": loop_id,
            "plugin_id": plugin_id,
            "period_start": since.isoformat(),
            "period_end": now.isoformat(),
            "timestamps": [now.isoformat()],
            "health_scores": [entry.health_score],
            "event_counts": [entry.event_count_7d],
            "current_status": entry.status,
            "current_health_score": entry.health_score,
        }

    def detect_duplicate_loops(self) -> List[dict]:
        """Detect learning loops declared by multiple plugins (duplicate loop_ids).

        Returns:
            List of warnings with loop_id and conflicting plugin_ids
        """
        all_loops = self._storage.list_all(limit=10000)

        # Group by loop_id
        loop_id_to_plugins: dict[str, List[str]] = {}
        for entry in all_loops:
            if entry.loop_id not in loop_id_to_plugins:
                loop_id_to_plugins[entry.loop_id] = []
            loop_id_to_plugins[entry.loop_id].append(entry.plugin_id)

        # Find duplicates
        conflicts = []
        for loop_id, plugins in loop_id_to_plugins.items():
            if len(plugins) > 1:
                conflicts.append(
                    {
                        "loop_id": loop_id,
                        "plugin_ids": sorted(set(plugins)),
                        "conflict_count": len(set(plugins)),
                        "action_taken": "logged",
                    }
                )
                # Audit-log the conflict
                self._audit_conflict_detected(loop_id, plugins)

        return conflicts

    def archive_loop(self, plugin_id: str, loop_id: str) -> bool:
        """Archive a loop (mark as deleted/removed).

        Args:
            plugin_id: Plugin identifier
            loop_id: Loop identifier

        Returns:
            True if archived, False if not found
        """
        # Mark as archived by setting status to "archived" (or delete if preferred)
        entry = self._storage.get(plugin_id, loop_id)
        if entry is None:
            return False

        # For now, just delete the entry
        # In a real implementation, we might mark it as archived instead
        self._audit_loop_removed(plugin_id, loop_id)
        return self._storage.delete(plugin_id, loop_id)

    # ── Internal audit helpers ──────────────────────────────────────────────

    def _audit_status_transition(
        self,
        plugin_id: str,
        loop_id: str,
        old_status: str,
        new_status: str,
    ) -> None:
        """Emit audit event for status transition."""
        try:
            from core.learning.event_persistence import core_audit_event

            core_audit_event(
                "learning.loop_status_changed",
                tenant_id=self.tenant_id,
                details={
                    "plugin_id": plugin_id,
                    "loop_id": loop_id,
                    "old_status": old_status,
                    "new_status": new_status,
                },
            )
        except Exception as exc:
            logger.error(f"Failed to audit status transition: {exc}")

    def _audit_conflict_detected(
        self,
        loop_id: str,
        plugin_ids: List[str],
    ) -> None:
        """Emit audit event for duplicate loop detection."""
        try:
            from core.learning.event_persistence import core_audit_event

            core_audit_event(
                "learning.loop_conflict_detected",
                tenant_id=self.tenant_id,
                details={
                    "loop_id": loop_id,
                    "plugin_ids": plugin_ids,
                    "count": len(set(plugin_ids)),
                },
            )
        except Exception as exc:
            logger.error(f"Failed to audit conflict: {exc}")

    def _audit_loop_removed(
        self,
        plugin_id: str,
        loop_id: str,
    ) -> None:
        """Emit audit event for loop removal."""
        try:
            from core.learning.event_persistence import core_audit_event

            core_audit_event(
                "learning.loop_removed",
                tenant_id=self.tenant_id,
                details={
                    "plugin_id": plugin_id,
                    "loop_id": loop_id,
                },
            )
        except Exception as exc:
            logger.error(f"Failed to audit loop removal: {exc}")

    def close(self) -> None:
        """Close storage and cleanup."""
        self._storage.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, *args):
        """Context manager exit."""
        self.close()
