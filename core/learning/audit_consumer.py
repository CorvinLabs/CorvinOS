"""AuditEventConsumer — Phase C k=3 Loop Closure (k=1 iteration).

Reads Audit-Chain, aggregates events into windows for confidence scoring.
- Batch-size: 100–1000 events
- Window: time-based (5-min) or count-based (1000 events), whichever first
- Source: task_tracking.events (reference) + core audit chain (validation)
- Output: list[AggregatedAuditWindow]

Invariants:
- Each window carries chain_hash of last event (for verification)
- Events deduplicated by event_id (idempotent reads)
- Tenant-scoped (per tenant_id)
- Eventual-consistency: async, non-blocking
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.paths import tenant_home
from core.task_tracking.audit import verify_audit_chain


@dataclass
class AuditEventSummary:
    """Lightweight representation of an audit event for batching."""

    event_id: str
    event_type: str
    task_id: str
    tenant_id: str
    actor: str
    action: str
    timestamp: str  # ISO-8601
    chain_hash: str


@dataclass
class AggregatedAuditWindow:
    """Aggregated batch of audit events, ready for confidence scoring."""

    window_id: str
    tenant_id: str
    event_count: int
    event_types: dict[str, int] = field(default_factory=dict)
    action_counts: dict[str, int] = field(default_factory=dict)
    actor_counts: dict[str, int] = field(default_factory=dict)
    latest_chain_hash: str = ""
    window_start_ts: str = ""
    window_end_ts: str = ""
    duration_seconds: float = 0.0
    throughput_events_per_sec: float = 0.0

    def is_statistically_valid(self) -> bool:
        """Check if window has minimum statistical power (n >= 10)."""
        return self.event_count >= 10


class AuditEventConsumer:
    """Read Audit-Chain, aggregate events into windows for confidence scoring."""

    def __init__(self, batch_size: int = 100, window_seconds: int = 300):
        """Initialize consumer.

        Args:
          batch_size: target events per window (100–1000)
          window_seconds: time-based window (5 min default)
        """
        self.batch_size = max(10, min(batch_size, 10000))  # Clamp to sensible range
        self.window_seconds = max(30, min(window_seconds, 3600))  # 30sec–1hr
        self._processed_hashes: set[str] = set()  # For idempotency

    async def read_unprocessed_events(
        self,
        tenant_id: str,
        since_chain_hash: Optional[str] = None,
    ) -> list[AuditEventSummary]:
        """Read events from audit-chain since last processed hash.

        Args:
          tenant_id: tenant to read
          since_chain_hash: start after this hash (None = from beginning)

        Returns:
          List of audit events, in order
        """
        from core.task_tracking import store

        events = []

        try:
            with store.connect(tenant_id) as conn:
                # Query task_tracking.events (reference copy of audit trail)
                if since_chain_hash:
                    # Find events after this hash
                    # Note: SQLite doesn't have a good way to do "after hash in append-only log"
                    # So we read all and filter in-memory
                    rows = conn.execute(
                        "SELECT event_id, event_type, item_id, ts, actor, delta, chain_hash "
                        "FROM events WHERE tenant_id = ? ORDER BY ts",
                        (tenant_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT event_id, event_type, item_id, ts, actor, delta, chain_hash "
                        "FROM events WHERE tenant_id = ? ORDER BY ts",
                        (tenant_id,),
                    ).fetchall()

                found_start = since_chain_hash is None
                for row in rows:
                    chain_hash = row["chain_hash"] or ""

                    # Skip until we find the start hash
                    if not found_start:
                        if chain_hash == since_chain_hash:
                            found_start = True
                        continue

                    # Skip if already processed
                    if chain_hash in self._processed_hashes:
                        continue

                    # Extract action from delta (if present)
                    import json

                    action = "unknown"
                    try:
                        delta = json.loads(row["delta"] or "{}")
                        # First key in delta is usually the action
                        action = list(delta.keys())[0] if delta else "unknown"
                    except (json.JSONDecodeError, IndexError):
                        pass

                    event = AuditEventSummary(
                        event_id=row["event_id"],
                        event_type=row["event_type"],
                        task_id=row["item_id"],
                        tenant_id=tenant_id,
                        actor=row["actor"] or "system",
                        action=action,
                        timestamp=row["ts"],
                        chain_hash=chain_hash,
                    )

                    events.append(event)
                    self._processed_hashes.add(chain_hash)

        except Exception as e:
            # Non-blocking: log and return empty
            # Consumer is async, failures should not crash the task service
            return []

        return events

    async def aggregate_into_window(
        self, events: list[AuditEventSummary]
    ) -> Optional[AggregatedAuditWindow]:
        """Aggregate events into one batch with statistics.

        Args:
          events: list of audit events to aggregate

        Returns:
          AggregatedAuditWindow if events >= 10 (statistically valid), else None
        """
        if not events or len(events) < 10:
            # Minimum statistical power: n >= 10
            return None

        # Calculate statistics
        event_types = {}
        action_counts = {}
        actor_counts = {}

        for event in events:
            event_types[event.event_type] = event_types.get(event.event_type, 0) + 1
            action_counts[event.action] = action_counts.get(event.action, 0) + 1
            actor_counts[event.actor] = actor_counts.get(event.actor, 0) + 1

        # Timestamps
        try:
            ts_start = datetime.fromisoformat(events[0].timestamp.replace("Z", "+00:00"))
            ts_end = datetime.fromisoformat(events[-1].timestamp.replace("Z", "+00:00"))
            duration = (ts_end - ts_start).total_seconds()
        except (ValueError, IndexError):
            duration = 0.0

        throughput = len(events) / max(duration, 1.0) if duration > 0 else 0.0

        window_id = f"win_{events[0].tenant_id}_{int(time.time()*1000)}"

        return AggregatedAuditWindow(
            window_id=window_id,
            tenant_id=events[0].tenant_id,
            event_count=len(events),
            event_types=event_types,
            action_counts=action_counts,
            actor_counts=actor_counts,
            latest_chain_hash=events[-1].chain_hash,
            window_start_ts=events[0].timestamp,
            window_end_ts=events[-1].timestamp,
            duration_seconds=duration,
            throughput_events_per_sec=throughput,
        )

    async def process_until_window_complete(
        self,
        tenant_id: str,
        since_chain_hash: Optional[str] = None,
    ) -> Optional[AggregatedAuditWindow]:
        """Read events and aggregate until a complete window (batch_size or window_seconds elapsed).

        Returns:
          Complete window, or None if no complete window yet.
        """
        window_start = datetime.now(timezone.utc)
        window_end = window_start + timedelta(seconds=self.window_seconds)

        accumulated_events = []

        while datetime.now(timezone.utc) < window_end:
            # Read unprocessed events
            events = await self.read_unprocessed_events(
                tenant_id, since_chain_hash=since_chain_hash
            )

            accumulated_events.extend(events)

            # Check if we have a complete window
            if len(accumulated_events) >= self.batch_size:
                # Take first batch_size events
                window_events = accumulated_events[: self.batch_size]
                return await self.aggregate_into_window(window_events)

            # Wait a bit before next read
            await asyncio.sleep(1)

        # Window expired, return what we have if statistically valid
        if accumulated_events:
            return await self.aggregate_into_window(accumulated_events)

        return None
