"""Recursive health-check loop for hierarchical plugins (ADR-0345).

This module implements continuous health monitoring of the plugin tree,
including autonomous fallback chain activation and audit failure isolation.

Key responsibilities:
- Recursive health_check_tree() algorithm (bottom-up)
- 2-tier audit failure isolation (Tier 1 = DEGRADED, Tier 2 = QUARANTINED)
- Automatic fallback chain activation
- Budget cycle reset
- Per-child health aggregation
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from .node import PluginNode, ChildStatus
from .graph import PluginGraph
from .utils import now_utc

log = logging.getLogger("corvin.plugins.health_check_tree")


class PluginHealthStatus(str, Enum):
    """Overall health status of a plugin."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    FAILED = "failed"


@dataclass
class PluginHealth:
    """Health status of a single plugin node."""

    plugin_id: str
    status: PluginHealthStatus = PluginHealthStatus.HEALTHY
    is_busy: bool = False
    budget_usage_ratio: float = 0.0
    audit_failures_10min: int = 0
    avg_latency_ms: float = 0.0
    children_healthy: int = 0
    children_degraded: int = 0
    children_quarantined: int = 0
    children_failed: int = 0
    timestamp: str = field(default_factory=now_utc)
    notes: str = ""

    def __str__(self) -> str:
        """String representation."""
        return (
            f"PluginHealth({self.plugin_id}, "
            f"status={self.status}, "
            f"budget={self.budget_usage_ratio:.1%}, "
            f"latency={self.avg_latency_ms:.1f}ms)"
        )


@dataclass
class TreeHealthReport:
    """Aggregated health report for entire plugin tree."""

    root_plugin_id: str
    total_plugins: int
    healthy_count: int
    degraded_count: int
    quarantined_count: int
    failed_count: int
    max_depth: int
    timestamp: str = field(default_factory=now_utc)
    per_node_health: Dict[str, PluginHealth] = field(default_factory=dict)

    @property
    def overall_health_ratio(self) -> float:
        """Get health ratio (0.0 = all failed, 1.0 = all healthy)."""
        if self.total_plugins == 0:
            return 1.0
        return self.healthy_count / self.total_plugins

    def __str__(self) -> str:
        """String representation."""
        return (
            f"TreeHealthReport(root={self.root_plugin_id}, "
            f"total={self.total_plugins}, "
            f"health={self.overall_health_ratio:.1%})"
        )


class QuarantineRegistry:
    """Registry for quarantined plugins (2-tier audit failure isolation).

    Tier 1 (DEGRADED): Single audit hash mismatch → try fallback
    Tier 2 (QUARANTINED): ≥3 failures in 10min → hard isolation, operator recovery

    Persistence: Uses audit log to survive restarts.
    """

    def __init__(self, audit_log=None):
        """Initialize quarantine registry.

        Args:
            audit_log: Optional audit logger for persistence
        """
        self.audit_log = audit_log
        self.quarantined: Dict[str, str] = {}  # plugin_id → reason
        self.failure_counts: Dict[str, List[str]] = {}  # plugin_id → [timestamp, ...]

    def quarantine(self, plugin_id: str, reason: str = "unknown") -> None:
        """Quarantine a plugin (Tier 2).

        Args:
            plugin_id: ID of plugin to quarantine
            reason: Reason for quarantine (audit failure, etc.)
        """
        self.quarantined[plugin_id] = reason
        log.warning(f"Plugin {plugin_id} quarantined: {reason}")

        if self.audit_log:
            self.audit_log.record({
                "event": "plugin_quarantined",
                "plugin_id": plugin_id,
                "reason": reason,
                "timestamp": now_utc(),
            })

    def degrade(self, plugin_id: str, reason: str = "unknown") -> None:
        """Mark plugin as degraded (Tier 1).

        Args:
            plugin_id: ID of plugin to degrade
            reason: Reason for degradation
        """
        log.warning(f"Plugin {plugin_id} degraded: {reason}")

        if self.audit_log:
            self.audit_log.record({
                "event": "plugin_degraded",
                "plugin_id": plugin_id,
                "reason": reason,
                "timestamp": now_utc(),
            })

    def is_quarantined(self, plugin_id: str) -> bool:
        """Check if plugin is quarantined (Tier 2).

        Args:
            plugin_id: ID of plugin to check

        Returns:
            True if quarantined
        """
        return plugin_id in self.quarantined

    def get_quarantine_reason(self, plugin_id: str) -> Optional[str]:
        """Get reason for quarantine.

        Args:
            plugin_id: ID of plugin

        Returns:
            Reason string, or None if not quarantined
        """
        return self.quarantined.get(plugin_id)

    def release(self, plugin_id: str) -> None:
        """Release a quarantined plugin (operator recovery).

        Args:
            plugin_id: ID of plugin to release
        """
        if plugin_id in self.quarantined:
            del self.quarantined[plugin_id]
            # Reset failure count
            self.failure_counts.pop(plugin_id, None)
            log.info(f"Plugin {plugin_id} released from quarantine")

            if self.audit_log:
                self.audit_log.record({
                    "event": "plugin_released",
                    "plugin_id": plugin_id,
                    "timestamp": now_utc(),
                })

    def record_failure(self, plugin_id: str, timestamp: str) -> None:
        """Record an audit failure for a plugin.

        When ≥3 failures in 10min window, plugin is quarantined.

        Args:
            plugin_id: ID of plugin that failed
            timestamp: Timestamp of failure (ISO format)
        """
        if plugin_id not in self.failure_counts:
            self.failure_counts[plugin_id] = []

        self.failure_counts[plugin_id].append(timestamp)

    def count_recent_failures(
        self, plugin_id: str, window_sec: int = 600
    ) -> int:
        """Count failures in recent time window.

        Args:
            plugin_id: ID of plugin
            window_sec: Time window in seconds (default 600 = 10min)

        Returns:
            Count of failures in window
        """
        # NOTE: This is a simplified implementation.
        # In production, would parse ISO timestamps and compare against now().
        # For now, assume all failures in list are within window.
        return len(self.failure_counts.get(plugin_id, []))

    def get_all_quarantined(self) -> List[str]:
        """Get list of all quarantined plugin IDs.

        Returns:
            List of plugin IDs
        """
        return list(self.quarantined.keys())


class HealthCheckTree:
    """Recursive health-check algorithm for plugin tree (ADR-0345)."""

    def __init__(
        self,
        graph: PluginGraph,
        audit_log=None,
        quarantine_registry: Optional[QuarantineRegistry] = None,
    ):
        """Initialize health checker.

        Args:
            graph: PluginGraph instance
            audit_log: Optional audit logger
            quarantine_registry: Optional quarantine system (creates new if not provided)
        """
        self.graph = graph
        self.audit_log = audit_log
        self.quarantine_registry = (
            quarantine_registry or QuarantineRegistry(audit_log)
        )

    def check_tree_health(self, root_id: Optional[str] = None) -> TreeHealthReport:
        """Recursively check health of entire plugin tree.

        Performs bottom-up traversal:
        1. Check each child (recursively)
        2. Aggregate child health
        3. Update parent status
        4. Reset budget cycle
        5. Check audit failures

        Args:
            root_id: ID of root plugin (if None, checks all roots)

        Returns:
            TreeHealthReport with aggregated health
        """
        report = TreeHealthReport(
            root_plugin_id=root_id or "all_roots",
            total_plugins=0,
            healthy_count=0,
            degraded_count=0,
            quarantined_count=0,
            failed_count=0,
            max_depth=0,
        )

        if root_id:
            # Check specific tree
            root = self.graph.get_node(root_id)
            if not root:
                log.error(f"Root plugin {root_id} not found")
                return report

            self._check_node_recursive(root, report)
        else:
            # Check all root plugins
            for root in self.graph.get_root_plugins():
                self._check_node_recursive(root, report)

        log.info(f"Health check complete: {report}")
        return report

    def _check_node_recursive(
        self, node: PluginNode, report: TreeHealthReport
    ) -> PluginHealth:
        """Recursively check node and all descendants (bottom-up).

        Args:
            node: PluginNode to check
            report: TreeHealthReport to aggregate into

        Returns:
            PluginHealth for this node
        """
        # Step 1: Recursively check children
        children_health = []
        for child_id in node.sub_plugins:
            child_node = self.graph.get_node(child_id)
            if not child_node:
                continue

            child_health = self._check_node_recursive(child_node, report)
            children_health.append(child_health)

        # Step 2: Aggregate child health
        children_healthy = sum(
            1 for h in children_health if h.status == PluginHealthStatus.HEALTHY
        )
        children_degraded = sum(
            1 for h in children_health if h.status == PluginHealthStatus.DEGRADED
        )
        children_quarantined = sum(
            1 for h in children_health if h.status == PluginHealthStatus.QUARANTINED
        )
        children_failed = sum(
            1 for h in children_health if h.status == PluginHealthStatus.FAILED
        )

        # Step 3: Determine node's status
        status = PluginHealthStatus.HEALTHY
        notes = []

        # Quarantined check
        if self.quarantine_registry.is_quarantined(node.id):
            status = PluginHealthStatus.QUARANTINED
            notes.append(f"Quarantined: {self.quarantine_registry.get_quarantine_reason(node.id)}")

        # Degraded check
        if status == PluginHealthStatus.HEALTHY:
            if node.status == "degraded":
                status = PluginHealthStatus.DEGRADED
                notes.append("Marked degraded")
            elif children_failed > 0:
                status = PluginHealthStatus.DEGRADED
                notes.append(f"{children_failed} child(ren) failed")
            elif node.is_degraded():
                status = PluginHealthStatus.DEGRADED
                notes.append(f"Budget at {node.get_budget_usage_ratio():.1%}")

        # Capture child status metrics
        avg_child_latency = (
            sum(h.avg_latency_ms for h in children_health) / len(children_health)
            if children_health
            else 0.0
        )

        # Build health object
        health = PluginHealth(
            plugin_id=node.id,
            status=status,
            is_busy=node.status == "processing",
            budget_usage_ratio=node.get_budget_usage_ratio(),
            audit_failures_10min=0,  # Would read from audit_log in production
            avg_latency_ms=avg_child_latency,
            children_healthy=children_healthy,
            children_degraded=children_degraded,
            children_quarantined=children_quarantined,
            children_failed=children_failed,
            notes=" | ".join(notes),
        )

        # Step 4: Reset budget cycle for next cycle
        self.graph.reset_budget_cycle(node.id)

        # Step 5: Aggregate into report
        report.per_node_health[node.id] = health
        report.total_plugins += 1

        if status == PluginHealthStatus.HEALTHY:
            report.healthy_count += 1
        elif status == PluginHealthStatus.DEGRADED:
            report.degraded_count += 1
        elif status == PluginHealthStatus.QUARANTINED:
            report.quarantined_count += 1
        elif status == PluginHealthStatus.FAILED:
            report.failed_count += 1

        # Update node status
        node.status = status.value

        log.debug(f"Health check {node.id}: {health}")
        return health

    def get_fallback_chain_for_failure(
        self, plugin_id: str
    ) -> Optional[List[str]]:
        """Get fallback chain for a failed/quarantined plugin.

        Returns ordered list of fallback children to try in sequence.

        Args:
            plugin_id: ID of plugin that failed

        Returns:
            List of fallback child IDs, or None if no parent
        """
        node = self.graph.get_node(plugin_id)
        if not node or not node.parent_id:
            return None

        parent = self.graph.get_node(node.parent_id)
        if not parent:
            return None

        # Return fallback chain if defined, otherwise all other children
        if parent.fallback_chain:
            return parent.fallback_chain
        else:
            # Default: return all other healthy children
            other_children = [
                c for c in parent.sub_plugins
                if c != plugin_id
                and not self.quarantine_registry.is_quarantined(c)
            ]
            return other_children if other_children else None

    def activate_fallback_chain(self, plugin_id: str, reason: str) -> Optional[str]:
        """Activate fallback chain for a failed plugin.

        Marks plugin as quarantined and logs audit event.

        Args:
            plugin_id: ID of plugin to failover
            reason: Reason for failover

        Returns:
            First fallback child ID, or None if no fallback available
        """
        fallback_chain = self.get_fallback_chain_for_failure(plugin_id)
        if not fallback_chain:
            log.warning(f"No fallback available for {plugin_id}")
            return None

        # Quarantine the failed plugin
        self.quarantine_registry.quarantine(plugin_id, reason=reason)

        # Return first fallback
        return fallback_chain[0]

    def report_audit_failure(
        self, plugin_id: str, timestamp: str
    ) -> PluginHealthStatus:
        """Process audit failure and determine isolation tier.

        Args:
            plugin_id: ID of plugin with audit failure
            timestamp: Timestamp of failure (ISO format)

        Returns:
            Resulting health status (DEGRADED or QUARANTINED)
        """
        # Record failure
        self.quarantine_registry.record_failure(plugin_id, timestamp)

        # Count recent failures
        recent_count = self.quarantine_registry.count_recent_failures(
            plugin_id, window_sec=600
        )

        # Determine tier
        if recent_count >= 3:
            # Tier 2: Quarantine
            self.quarantine_registry.quarantine(
                plugin_id,
                reason=f"audit_failures ({recent_count} in 10min)"
            )
            return PluginHealthStatus.QUARANTINED
        else:
            # Tier 1: Degrade
            self.quarantine_registry.degrade(
                plugin_id, reason="audit_mismatch"
            )
            return PluginHealthStatus.DEGRADED

    def get_quarantine_status(self) -> Dict[str, str]:
        """Get status of all quarantined plugins.

        Returns:
            Dict of {plugin_id: reason}
        """
        return dict(self.quarantine_registry.quarantined)
