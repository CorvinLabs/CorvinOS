"""
Unified telemetry model for plugin system (Phase 5, ADR-0345).

Tracks:
- Plugin health (ready/degraded/quarantined)
- Work delegation (routing, fallback, budget)
- Audit events (hash integrity, failures)
- Performance (latency, throughput)

All events are immutable, tagged with plugin_id + tenant_id, and audit-logged.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any
from datetime import datetime
from enum import Enum
import json


class PluginTelemetryEventType(Enum):
    """Plugin telemetry event types."""
    # Health
    HEALTH_CHECK = "plugin_health_check"
    STATUS_CHANGED = "plugin_status_changed"

    # Work delegation
    WORK_RECEIVED = "work_received"
    WORK_DELEGATED = "work_delegated"
    WORK_HANDLED_LOCALLY = "work_handled_locally"
    WORK_FAILED = "work_failed"

    # Budget
    BUDGET_ALLOCATED = "budget_allocated"
    BUDGET_EXHAUSTED = "budget_exhausted"

    # Audit
    AUDIT_HASH_COMPUTED = "audit_hash_computed"
    AUDIT_HASH_MISMATCH = "audit_hash_mismatch"
    DELEGATION_TRANSACTION_COMPLETE = "delegation_transaction_complete"

    # Fallback
    FALLBACK_TRIGGERED = "fallback_triggered"
    CHILD_QUARANTINED = "child_quarantined"


class WorkTier(Enum):
    """Work priority tier (from ADR-0345)."""
    COMPLIANCE = "compliance"
    HIGH = "high"
    STANDARD = "standard"
    LOW = "low"


@dataclass(frozen=True)
class PluginTelemetryEvent:
    """Immutable telemetry event for plugin system."""

    # Identity
    event_type: PluginTelemetryEventType
    plugin_id: str
    tenant_id: str

    # Timing
    timestamp_utc: datetime = field(default_factory=datetime.utcnow)

    # Context
    parent_id: Optional[str] = None
    work_id: Optional[str] = None

    # Data (payload is ANY to accommodate event-specific fields)
    data: Dict[str, Any] = field(default_factory=dict)

    # Audit
    prior_hash: Optional[str] = None
    self_hash: Optional[str] = None

    def to_dict(self) -> Dict:
        """Serialize for audit trail + dashboard."""
        return {
            "event_type": self.event_type.value,
            "plugin_id": self.plugin_id,
            "tenant_id": self.tenant_id,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "parent_id": self.parent_id,
            "work_id": self.work_id,
            "data": self.data,
            "prior_hash": self.prior_hash,
            "self_hash": self.self_hash,
        }

    def to_json(self) -> str:
        """Serialize to JSON for storage."""
        return json.dumps(self.to_dict())


@dataclass
class PluginTelemetrySnapshot:
    """Current state snapshot for a plugin (for dashboards)."""

    plugin_id: str
    status: str  # ready/degraded/quarantined/offline
    health_score: float  # 0.0–1.0

    # Work stats (24h window)
    work_handled_count: int = 0
    work_delegated_count: int = 0
    work_failed_count: int = 0

    # Latency (ms)
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0

    # Budget
    budget_used: Dict[str, int] = field(default_factory=dict)
    budget_available: Dict[str, int] = field(default_factory=dict)

    # Audit
    audit_events_24h: int = 0
    audit_failures_24h: int = 0

    # Delegation tree
    children: List[str] = field(default_factory=list)
    fallback_chain: List[str] = field(default_factory=list)

    # Last update
    timestamp_utc: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict:
        """Serialize for dashboard API."""
        return {
            "plugin_id": self.plugin_id,
            "status": self.status,
            "health_score": self.health_score,
            "work": {
                "handled": self.work_handled_count,
                "delegated": self.work_delegated_count,
                "failed": self.work_failed_count,
            },
            "latency": {
                "avg_ms": self.avg_latency_ms,
                "p95_ms": self.p95_latency_ms,
                "p99_ms": self.p99_latency_ms,
            },
            "budget": {
                "used": self.budget_used,
                "available": self.budget_available,
            },
            "audit": {
                "events_24h": self.audit_events_24h,
                "failures_24h": self.audit_failures_24h,
            },
            "tree": {
                "children": self.children,
                "fallback_chain": self.fallback_chain,
            },
            "timestamp_utc": self.timestamp_utc.isoformat(),
        }


class PluginTelemetryCollector:
    """Collect and aggregate plugin telemetry events."""

    def __init__(self):
        """Initialize collector."""
        self.events: List[PluginTelemetryEvent] = []
        self.snapshots: Dict[str, PluginTelemetrySnapshot] = {}

    def emit_event(self, event: PluginTelemetryEvent) -> None:
        """Emit telemetry event (immutable)."""
        self.events.append(event)

    def get_plugin_snapshot(self, plugin_id: str, tenant_id: str) -> Optional[PluginTelemetrySnapshot]:
        """Get current snapshot for a plugin."""
        key = f"{tenant_id}:{plugin_id}"
        return self.snapshots.get(key)

    def update_plugin_snapshot(
        self,
        plugin_id: str,
        tenant_id: str,
        snapshot: PluginTelemetrySnapshot,
    ) -> None:
        """Update plugin snapshot (called by monitoring loop)."""
        key = f"{tenant_id}:{plugin_id}"
        self.snapshots[key] = snapshot

    def get_events_for_plugin(
        self,
        plugin_id: str,
        tenant_id: str,
        event_type: Optional[PluginTelemetryEventType] = None,
        limit: int = 100,
    ) -> List[PluginTelemetryEvent]:
        """Get recent events for a plugin."""
        events = [
            e for e in self.events
            if e.plugin_id == plugin_id and e.tenant_id == tenant_id
        ]

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        # Return most recent first
        return sorted(events, key=lambda e: e.timestamp_utc, reverse=True)[:limit]

    def compute_health_score(
        self,
        plugin_id: str,
        tenant_id: str,
    ) -> float:
        """Compute health score (0.0–1.0) based on recent events."""
        events = self.get_events_for_plugin(plugin_id, tenant_id, limit=100)

        if not events:
            return 1.0  # Unknown = healthy

        # Count recent failures (audit mismatches, work failures)
        failures = sum(
            1 for e in events
            if e.event_type in [
                PluginTelemetryEventType.AUDIT_HASH_MISMATCH,
                PluginTelemetryEventType.WORK_FAILED,
            ]
        )

        # Score: (100 - failures) / 100, min 0.0
        return max(0.0, (len(events) - failures) / len(events))


# Singleton collector (in production, inject via DI)
_telemetry_collector = PluginTelemetryCollector()


def get_telemetry_collector() -> PluginTelemetryCollector:
    """Get global telemetry collector."""
    return _telemetry_collector
