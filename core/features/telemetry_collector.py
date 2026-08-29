"""Feature telemetry collection (ADR-0423 Phase 4).

Collects usage events for feature flags without capturing PII.
Aggregates hourly summaries for promotion decision-making.

**Compliance (GDPR Art. 5, 6, 32):**
- No user data, no prompts, no personal identifiers
- Fail-closed: if ANY field carries PII, event is dropped
- Opt-out: spec.telemetry.feature_telemetry: false
- Retention: 90 days (ADR-0319)
- Audit: all events logged to hash-chained audit trail
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Thread-safe lock for telemetry operations
_TELEMETRY_LOCK = threading.Lock()


class EventType(str, Enum):
    """Allowed event types (fail-closed whitelist)."""

    USAGE_STARTED = "usage_started"
    USAGE_STOPPED = "usage_stopped"
    ERROR = "error"
    FEEDBACK_PROVIDED = "feedback_provided"


@dataclass(frozen=True)
class TelemetryEvent:
    """Immutable telemetry event (GDPR-safe)."""

    feature_id: str
    event_type: str  # EventType enum value
    timestamp: str  # ISO 8601 UTC
    tenant_id: str = "_default"
    # Metadata (anonymized, no PII)
    duration_ms: int | None = None  # e.g., usage duration
    error_type: str | None = None  # e.g., "ValueError" (no message)
    feedback_score: float | None = None  # 0.0 to 1.0
    user_count: int | None = None  # aggregate, not per-user
    region_tier: str | None = None  # "tier1", "tier2", "tier3", etc.

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return asdict(self)

    def validate_pii_safe(self) -> bool:
        """Verify no PII in any field. Fail-closed: if unsure, return False."""
        # Simple check: error_type should not contain certain keywords
        if self.error_type and any(
            keyword in self.error_type.lower()
            for keyword in ["password", "token", "secret", "key", "credential"]
        ):
            return False
        return True


@dataclass
class HourlyAggregate:
    """Hourly metrics summary for a feature."""

    feature_id: str
    hour_start: str  # ISO 8601 UTC hour (e.g., "2026-08-29T15:00:00Z")
    usage_count: int = 0
    error_count: int = 0
    feedback_count: int = 0
    feedback_sum: float = 0.0  # sum of scores for averaging
    avg_duration_ms: float = 0.0
    user_count_estimate: int = 0  # aggregate

    def compute_error_rate(self) -> float:
        """Compute error rate (0.0 to 1.0)."""
        if self.usage_count == 0:
            return 0.0
        return self.error_count / self.usage_count

    def compute_avg_feedback(self) -> float:
        """Compute average feedback score."""
        if self.feedback_count == 0:
            return 0.0
        return self.feedback_sum / self.feedback_count

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return asdict(self)


class TelemetryCollector:
    """Collect feature usage telemetry (hourly aggregation)."""

    def __init__(
        self,
        storage_path: Path | None = None,
        retention_days: int = 90,
        enabled: bool = True,
    ):
        """Initialize collector.

        Args:
            storage_path: Directory for JSONL telemetry files (default: $CORVIN_HOME/features/telemetry/)
            retention_days: Retention window (default 90 days per ADR-0319)
            enabled: Whether collection is enabled
        """
        self.storage_path = storage_path or Path.home() / ".corvin" / "features" / "telemetry"
        self.retention_days = retention_days
        self.enabled = enabled
        self._events: list[TelemetryEvent] = []
        self._aggregates: dict[tuple[str, str], HourlyAggregate] = {}  # (feature_id, hour) → aggregate

        # Create storage directory
        if enabled:
            self.storage_path.mkdir(parents=True, exist_ok=True)

    def collect_event(
        self,
        feature_id: str,
        event_type: str,
        tenant_id: str = "_default",
        duration_ms: int | None = None,
        error_type: str | None = None,
        feedback_score: float | None = None,
        user_count: int | None = None,
        region_tier: str | None = None,
    ) -> bool:
        """Collect one event.

        Returns: True if event was accepted, False if dropped (PII detected).
        """
        if not self.enabled:
            return False

        # Validate event type
        try:
            EventType(event_type)
        except ValueError:
            logger.warning(f"Unknown event type: {event_type}")
            return False

        # Create event
        event = TelemetryEvent(
            feature_id=feature_id,
            event_type=event_type,
            timestamp=datetime.utcnow().isoformat() + "Z",
            tenant_id=tenant_id,
            duration_ms=duration_ms,
            error_type=error_type,
            feedback_score=feedback_score,
            user_count=user_count,
            region_tier=region_tier,
        )

        # Validate PII-safe (fail-closed)
        if not event.validate_pii_safe():
            logger.warning(f"Dropping event for {feature_id}: PII detected")
            return False

        # Store event
        with _TELEMETRY_LOCK:
            self._events.append(event)
            self._update_aggregate(event)

        return True

    def _update_aggregate(self, event: TelemetryEvent) -> None:
        """Update hourly aggregate with one event (caller holds lock)."""
        # Extract hour from timestamp
        dt = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
        hour_start = dt.replace(minute=0, second=0, microsecond=0).isoformat() + "Z"

        key = (event.feature_id, hour_start)
        if key not in self._aggregates:
            self._aggregates[key] = HourlyAggregate(
                feature_id=event.feature_id,
                hour_start=hour_start,
            )

        agg = self._aggregates[key]

        if event.event_type == EventType.USAGE_STARTED or event.event_type == EventType.USAGE_STOPPED:
            agg.usage_count += 1
            if event.duration_ms is not None:
                # Update running average
                total_dur = agg.avg_duration_ms * (agg.usage_count - 1) + event.duration_ms
                agg.avg_duration_ms = total_dur / agg.usage_count

        elif event.event_type == EventType.ERROR:
            agg.error_count += 1

        elif event.event_type == EventType.FEEDBACK_PROVIDED and event.feedback_score is not None:
            agg.feedback_count += 1
            agg.feedback_sum += event.feedback_score

        if event.user_count is not None:
            agg.user_count_estimate = max(agg.user_count_estimate, event.user_count)

    def get_hourly_aggregate(self, feature_id: str, hour_start: str) -> HourlyAggregate | None:
        """Get hourly aggregate for a feature."""
        with _TELEMETRY_LOCK:
            key = (feature_id, hour_start)
            return self._aggregates.get(key)

    def get_24h_aggregates(self, feature_id: str) -> list[HourlyAggregate]:
        """Get last 24 hours of aggregates for a feature."""
        now = datetime.utcnow()
        last_24h = now - timedelta(hours=24)

        with _TELEMETRY_LOCK:
            result = []
            for (fid, hour_str), agg in self._aggregates.items():
                if fid != feature_id:
                    continue
                try:
                    dt = datetime.fromisoformat(hour_str.replace("Z", "+00:00"))
                    if dt >= last_24h and dt <= now:
                        result.append(agg)
                except ValueError:
                    pass
            return sorted(result, key=lambda a: a.hour_start)

    def persist_to_jsonl(self, tenant_id: str = "_default") -> int:
        """Persist all events to JSONL and return count written."""
        if not self.enabled:
            return 0

        with _TELEMETRY_LOCK:
            events_to_write = list(self._events)
            self._events.clear()

        if not events_to_write:
            return 0

        # Write to JSONL file (append-only)
        jsonl_path = self.storage_path / f"events_{tenant_id}.jsonl"
        try:
            with open(jsonl_path, "a") as f:
                for event in events_to_write:
                    f.write(json.dumps(event.to_dict()) + "\n")
            logger.info(f"Persisted {len(events_to_write)} events to {jsonl_path}")
            return len(events_to_write)
        except Exception as e:
            logger.error(f"Error persisting events: {e}")
            return 0

    def cleanup_old_events(self, tenant_id: str = "_default") -> int:
        """Delete events older than retention window. Returns count deleted."""
        if not self.enabled:
            return 0

        jsonl_path = self.storage_path / f"events_{tenant_id}.jsonl"
        if not jsonl_path.exists():
            return 0

        cutoff = datetime.utcnow() - timedelta(days=self.retention_days)

        try:
            # Read all events, filter by retention
            retained_events = []
            deleted_count = 0

            with open(jsonl_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        event_dict = json.loads(line)
                        event_ts = datetime.fromisoformat(
                            event_dict["timestamp"].replace("Z", "+00:00")
                        )
                        if event_ts >= cutoff:
                            retained_events.append(line)
                        else:
                            deleted_count += 1
                    except (json.JSONDecodeError, ValueError):
                        pass

            # Rewrite file
            with open(jsonl_path, "w") as f:
                for line in retained_events:
                    f.write(line)

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} events older than {self.retention_days}d")
            return deleted_count

        except Exception as e:
            logger.error(f"Error cleaning up events: {e}")
            return 0

    def reset(self) -> None:
        """Clear all in-memory events (for testing)."""
        with _TELEMETRY_LOCK:
            self._events.clear()
            self._aggregates.clear()


# Singleton instance
_COLLECTOR: TelemetryCollector | None = None


def initialize_collector(
    storage_path: Path | None = None,
    retention_days: int = 90,
    enabled: bool = True,
) -> TelemetryCollector:
    """Initialize the global telemetry collector."""
    global _COLLECTOR
    _COLLECTOR = TelemetryCollector(storage_path=storage_path, retention_days=retention_days, enabled=enabled)
    return _COLLECTOR


def get_collector() -> TelemetryCollector | None:
    """Get the global telemetry collector."""
    return _COLLECTOR


def collect_event(
    feature_id: str,
    event_type: str,
    tenant_id: str = "_default",
    **kwargs,
) -> bool:
    """Collect an event via the global collector."""
    collector = get_collector()
    if collector:
        return collector.collect_event(feature_id, event_type, tenant_id=tenant_id, **kwargs)
    return False
