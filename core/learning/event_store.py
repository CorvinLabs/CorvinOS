"""EventStore — persistent learning event storage with hash-chaining (Phase 0).

Provides:
1. Immutable event append-only log
2. Hash-chained audit trail (GDPR Art. 30, 32)
3. Tenant isolation (per-tenant storage)
4. Event replay and verification
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from core.learning.event_schema import LearningEvent, LearningEventType


class EventStore:
    """Persistent learning event storage with hash-chained audit trail.

    Guarantees:
    - Events are immutable (append-only)
    - Hash-chained for tamper detection
    - Tenant-isolated (GDPR Art. 32)
    - ACID transactions
    """

    GENESIS_HASH = hashlib.sha256(b"eventstore.genesis").hexdigest()

    def __init__(self, db_path: str | Path):
        """Initialize EventStore.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._last_hash = self.GENESIS_HASH
        self._event_count = 0

        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    INDEX idx_tenant (tenant_id),
                    INDEX idx_type (event_type),
                    INDEX idx_sequence (sequence)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hash_chain (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(event_hash)
                )
                """
            )
            conn.commit()

    def write_event(self, event: LearningEvent) -> str:
        """Write event to store with hash-chaining.

        Args:
            event: LearningEvent to write

        Returns:
            Hash of the written event
        """
        with self._lock:
            # Serialize event (deterministic)
            event_data = asdict(event)
            # Remove audit fields for hashing (they're set during write)
            event_data.pop("audit_id", None)
            event_json = json.dumps(event_data, sort_keys=True, separators=(",", ":"))

            # Compute hash: H(prev_hash || event_json)
            combined = (self._last_hash + event_json).encode("utf-8")
            event_hash = hashlib.sha256(combined).hexdigest()

            # Write to database with transaction
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO events (
                            event_id, tenant_id, event_type, sequence,
                            event_json, event_hash, prev_hash, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            event.tenant_id,
                            event.event_type.value,
                            self._event_count,
                            event_json,
                            event_hash,
                            self._last_hash,
                            datetime.utcnow().isoformat(),
                        ),
                    )

                    conn.execute(
                        """
                        INSERT INTO hash_chain (event_id, event_hash, prev_hash, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            event_hash,
                            self._last_hash,
                            datetime.utcnow().isoformat(),
                        ),
                    )

                    conn.commit()

                # Update in-memory state
                self._last_hash = event_hash
                self._event_count += 1

                return event_hash

            except sqlite3.IntegrityError as e:
                raise ValueError(f"Failed to write event: {e}")

    def read_event(self, event_id: str) -> Optional[LearningEvent]:
        """Read a single event by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT event_json FROM events WHERE event_id = ?", (event_id,)
            )
            row = cursor.fetchone()

        if not row:
            return None

        event_data = json.loads(row[0])
        return LearningEvent(**event_data)

    def read_events_by_tenant(self, tenant_id: str, limit: int = 1000) -> list[LearningEvent]:
        """Read all events for a tenant (GDPR Art. 15 right of access)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT event_json FROM events
                WHERE tenant_id = ?
                ORDER BY sequence
                LIMIT ?
                """,
                (tenant_id, limit),
            )
            rows = cursor.fetchall()

        events = []
        for (event_json,) in rows:
            event_data = json.loads(event_json)
            events.append(LearningEvent(**event_data))

        return events

    def read_events_by_type(
        self, event_type: LearningEventType, limit: int = 1000
    ) -> list[LearningEvent]:
        """Read events by type."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT event_json FROM events
                WHERE event_type = ?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (event_type.value, limit),
            )
            rows = cursor.fetchall()

        events = []
        for (event_json,) in rows:
            event_data = json.loads(event_json)
            events.append(LearningEvent(**event_data))

        return events

    def verify_chain(self) -> bool:
        """Verify hash chain integrity.

        Returns:
            True if chain is valid, False if corrupted
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT event_json, event_hash, prev_hash
                FROM events
                ORDER BY sequence
                """
            )
            rows = cursor.fetchall()

        prev_hash = self.GENESIS_HASH
        for event_json, stored_hash, expected_prev in rows:
            # Verify previous hash
            if expected_prev != prev_hash:
                return False

            # Recompute hash
            combined = (prev_hash + event_json).encode("utf-8")
            computed_hash = hashlib.sha256(combined).hexdigest()

            # Compare
            if computed_hash != stored_hash:
                return False

            prev_hash = stored_hash

        return True

    def get_last_hash(self) -> str:
        """Get the hash of the most recent event."""
        return self._last_hash

    def get_event_count(self) -> int:
        """Get total event count."""
        return self._event_count

    def delete_tenant_events(self, tenant_id: str) -> int:
        """Delete all events for a tenant (GDPR Art. 17 right to erasure).

        Args:
            tenant_id: Tenant ID to erase

        Returns:
            Number of events deleted
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM events WHERE tenant_id = ?", (tenant_id,)
                )
                conn.commit()
                return cursor.rowcount

    def cleanup_old_events(self, days: int = 90) -> int:
        """Delete events older than N days (retention policy).

        Args:
            days: Number of days to retain (default 90)

        Returns:
            Number of events deleted
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM events WHERE created_at < ?", (cutoff,)
                )
                conn.commit()
                return cursor.rowcount

    def get_stats(self) -> dict[str, Any]:
        """Get store statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM events")
            total_events = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(DISTINCT tenant_id) FROM events")
            tenant_count = cursor.fetchone()[0]

            cursor = conn.execute(
                """
                SELECT event_type, COUNT(*)
                FROM events
                GROUP BY event_type
                """
            )
            events_by_type = {row[0]: row[1] for row in cursor.fetchall()}

        return {
            "total_events": total_events,
            "tenant_count": tenant_count,
            "events_by_type": events_by_type,
            "last_hash": self._last_hash,
            "db_path": str(self.db_path),
        }
