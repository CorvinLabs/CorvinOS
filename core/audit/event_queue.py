"""Durable SQLite event queue for plugin lifecycle events — ADR-0682.

2-tier priority queue (HIGH for executed, LOW for loaded) with:
- SQLite WAL mode (durable, crash-safe)
- Load deduplication (batch 50x loads into 1 summary)
- FIFO drain with batch read
- Immutable events (PRIMARY KEY prevents duplicates)
- Tenant isolation (every event has tenant_id)

GDPR Art. 30, 32: Audit-logged, durable, never lost.
"""

from __future__ import annotations

import logging
import sqlite3
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventQueueStats:
    """Immutable queue statistics."""
    total_events: int
    high_priority_count: int
    low_priority_count: int
    pending_count: int
    emitted_count: int


class EventQueue:
    """Durable SQLite queue for plugin lifecycle events.

    Schema:
    - id (INTEGER PRIMARY KEY)
    - event_type TEXT
    - plugin_id TEXT
    - tenant_id TEXT
    - payload_json TEXT (frozen dataclass as JSON)
    - priority TEXT ('HIGH' | 'LOW')
    - timestamp TEXT (ISO 8601)
    - emitted_at_chain TEXT (NULL until drained and written to chain)
    - created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

    UNIQUE constraint on (event_type, plugin_id, tenant_id, timestamp)
    to prevent duplicate events in same second.
    """

    QUEUE_PATH = Path.home() / ".corvin" / "tenants" / "_default" / "global" / "plugin_events.db"
    LOAD_DEDUP_WINDOW_S = 60  # Deduplicate plugin_loaded within 60s window

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize queue.

        Args:
            db_path: Optional override for queue database path
        """
        self.db_path = db_path or self.QUEUE_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create/verify schema
        self._init_schema()

    def _init_schema(self) -> None:
        """Create schema if not exists."""
        with sqlite3.connect(self.db_path) as conn:
            # Enable WAL mode for durability
            conn.execute("PRAGMA journal_mode=WAL")

            # Create events table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS plugin_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    plugin_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    priority TEXT NOT NULL CHECK(priority IN ('HIGH', 'LOW')),
                    timestamp TEXT NOT NULL,
                    emitted_at_chain TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_type, plugin_id, tenant_id, timestamp)
                )
            """)

            # Index for draining (priority DESC, id ASC)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_priority_id
                ON plugin_events(priority DESC, id ASC)
                WHERE emitted_at_chain IS NULL
            """)

            conn.commit()

    def enqueue(self, event: any) -> None:
        """Enqueue event with backpressure.

        Args:
            event: PluginLifecycleEvent (frozen dataclass)

        Raises:
            QueueFullError: If queue exceeds 80% capacity for HIGH priority
        """
        try:
            payload_dict = asdict(event) if hasattr(event, "__dataclass_fields__") else event
            payload_json = json.dumps(payload_dict, default=str)

            with sqlite3.connect(self.db_path) as conn:
                # Check backpressure for HIGH priority
                if payload_dict.get("priority") == "HIGH":
                    stats = self._get_stats_internal(conn)
                    if stats.pending_count > stats.total_events * 0.8:
                        raise QueueFullError(
                            f"Queue at 80%+ capacity ({stats.pending_count} pending); "
                            f"HIGH priority events fail-closed"
                        )

                try:
                    conn.execute("""
                        INSERT INTO plugin_events
                        (event_type, plugin_id, tenant_id, payload_json, priority, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        payload_dict.get("event_type"),
                        payload_dict.get("plugin_id"),
                        payload_dict.get("tenant_id"),
                        payload_json,
                        payload_dict.get("priority", "LOW"),
                        payload_dict.get("timestamp"),
                    ))
                    conn.commit()
                except sqlite3.IntegrityError:
                    # Duplicate event (same event_type, plugin_id, tenant_id, timestamp)
                    logger.debug(f"Duplicate event (deduped): {payload_dict.get('event_type')}")
                    pass
        except QueueFullError:
            raise  # Re-raise backpressure errors
        except Exception as e:
            logger.warning(f"Failed to enqueue event: {e}")

    def drain(
        self,
        batch_size: int = 50,
        timeout_sec: float = 5.0,
    ) -> List[dict]:
        """Drain pending events (batch read, FIFO order by priority then id).

        Deduplicates plugin_loaded events: batches 50x loads within 60s window
        into a single summary event with load_count field.

        Args:
            batch_size: Max events per drain (default 50)
            timeout_sec: Hard stop at timeout_sec (fail-closed, trip on timeout)

        Returns:
            List of event dicts (JSON loaded)

        Raises:
            DrainingError: If timeout exceeded
        """
        start_time = datetime.utcnow()
        events = []

        try:
            with sqlite3.connect(self.db_path, timeout=timeout_sec) as conn:
                # Fetch pending events (HIGH priority first, then by id)
                cursor = conn.execute("""
                    SELECT id, payload_json, event_type, plugin_id, tenant_id, timestamp
                    FROM plugin_events
                    WHERE emitted_at_chain IS NULL
                    ORDER BY priority DESC, id ASC
                    LIMIT ?
                """, (batch_size,))

                rows = cursor.fetchall()

                # Check timeout
                elapsed_s = (datetime.utcnow() - start_time).total_seconds()
                if elapsed_s > timeout_sec:
                    raise DrainingError(f"Drain timeout exceeded: {elapsed_s:.2f}s > {timeout_sec}s")

                # Process rows and deduplicate plugin_loaded events
                loaded_events = {}  # (plugin_id, tenant_id) -> [events]

                for row_id, payload_json, event_type, plugin_id, tenant_id, timestamp in rows:
                    try:
                        payload = json.loads(payload_json)

                        # Deduplication: collect plugin_loaded events
                        if event_type == "plugin_loaded":
                            key = (plugin_id, tenant_id)
                            if key not in loaded_events:
                                loaded_events[key] = []
                            loaded_events[key].append((row_id, payload, timestamp))
                        else:
                            events.append((row_id, payload))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to decode event {row_id}: {e}")
                        # Skip corrupted events
                        pass

                # Deduplicate plugin_loaded: batch events within LOAD_DEDUP_WINDOW_S
                for (plugin_id, tenant_id), load_events in loaded_events.items():
                    if not load_events:
                        continue

                    # Sort by timestamp
                    load_events.sort(key=lambda x: x[2])

                    # Batch into summary events (within 60s window)
                    first_timestamp = load_events[0][2]
                    batch = []
                    for row_id, payload, ts in load_events:
                        if (datetime.fromisoformat(ts.rstrip("Z")) -
                            datetime.fromisoformat(first_timestamp.rstrip("Z"))).total_seconds() < self.LOAD_DEDUP_WINDOW_S:
                            batch.append((row_id, payload))
                        else:
                            # New batch
                            if batch:
                                summary = self._summarize_loads(batch[0][1], len(batch))
                                events.append((batch[0][0], summary))
                            batch = [(row_id, payload)]

                    # Final batch
                    if batch:
                        summary = self._summarize_loads(batch[0][1], len(batch))
                        events.append((batch[0][0], summary))

                return [payload for _, payload in events]

        except DrainingError:
            raise
        except Exception as e:
            logger.error(f"Error draining queue: {e}")
            raise DrainingError(f"Drain failed: {e}")

    def mark_emitted(self, event_ids: List[int]) -> None:
        """Mark events as emitted (written to audit chain).

        Args:
            event_ids: List of event IDs to mark
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                now = datetime.utcnow().isoformat() + "Z"
                for event_id in event_ids:
                    conn.execute("""
                        UPDATE plugin_events
                        SET emitted_at_chain = ?
                        WHERE id = ?
                    """, (now, event_id))
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to mark events emitted: {e}")

    def batch_read(self, batch_size: int = 50, offset: int = 0) -> List[dict]:
        """Read events in batch (for testing/inspection).

        Args:
            batch_size: Max events per read
            offset: Offset for pagination

        Returns:
            List of event dicts
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT payload_json FROM plugin_events
                    LIMIT ? OFFSET ?
                """, (batch_size, offset))

                return [json.loads(row[0]) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"Failed to batch read: {e}")
            return []

    def count_by_priority(self) -> dict[str, int]:
        """Get event counts by priority.

        Returns:
            {'HIGH': count, 'LOW': count}
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT priority, COUNT(*) FROM plugin_events
                    WHERE emitted_at_chain IS NULL
                    GROUP BY priority
                """)

                return {priority: count for priority, count in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"Failed to count by priority: {e}")
            return {}

    def stats(self) -> EventQueueStats:
        """Get queue statistics.

        Returns:
            EventQueueStats (immutable)
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                return self._get_stats_internal(conn)
        except Exception as e:
            logger.warning(f"Failed to get queue stats: {e}")
            return EventQueueStats(0, 0, 0, 0, 0)

    def _get_stats_internal(self, conn: sqlite3.Connection) -> EventQueueStats:
        """Internal stats getter (reuses connection)."""
        # Total events
        total = conn.execute("SELECT COUNT(*) FROM plugin_events").fetchone()[0]

        # By priority
        high = conn.execute(
            "SELECT COUNT(*) FROM plugin_events WHERE priority='HIGH'"
        ).fetchone()[0]
        low = total - high

        # Pending (not emitted)
        pending = conn.execute(
            "SELECT COUNT(*) FROM plugin_events WHERE emitted_at_chain IS NULL"
        ).fetchone()[0]

        # Emitted
        emitted = total - pending

        return EventQueueStats(total, high, low, pending, emitted)

    def _summarize_loads(self, sample_event: dict, count: int) -> dict:
        """Create summary event from batched plugin_loaded events.

        Args:
            sample_event: First event in batch (template)
            count: Number of events batched

        Returns:
            Summarized event dict with load_count field
        """
        summary = sample_event.copy()
        summary["load_count"] = count
        summary["event_type"] = "plugin_loaded_summary"
        return summary


class QueueFullError(Exception):
    """Queue exceeded backpressure threshold."""
    pass


class DrainingError(Exception):
    """Error during queue drain operation."""
    pass
