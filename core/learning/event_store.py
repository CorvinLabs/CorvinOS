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
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from enum import Enum
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
                    created_at TEXT NOT NULL
                )
                """
            )
            # SQLite has no inline INDEX clause — that is MySQL syntax. The
            # three indexes used to sit inside the CREATE TABLE above, so the
            # statement raised `OperationalError: near "INDEX": syntax error`
            # and the EventStore could not be CONSTRUCTED at all: every
            # learning event (ADR-0314) failed at the very first write.
            for name, column in (
                ("idx_events_tenant", "tenant_id"),
                ("idx_events_type", "event_type"),
                ("idx_events_sequence", "sequence"),
            ):
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {name} ON events ({column})"
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

    @staticmethod
    def _json_default(value: Any) -> Any:
        """JSON encoder for the field types LearningEvent actually carries.

        `json.dumps(asdict(event))` raised "Object of type datetime is not JSON
        serializable" on EVERY write — and both callers wrap the emit in a
        fail-closed `except`, so the exception was swallowed and not one
        learning event was ever persisted, silently. Enum is handled here too:
        `LearningEventType` is a `str` Enum so it happens to serialize today,
        but relying on that makes the hash chain hostage to an unrelated base
        class change.
        """
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    @classmethod
    def _serialize(cls, event: LearningEvent) -> str:
        """Deterministic JSON for hashing and storage.

        `sort_keys` + no whitespace is what makes the hash chain reproducible;
        do not "prettify" this.
        """
        event_data = asdict(event)
        # Audit fields are set during the write, so they are not part of what
        # the chain commits to.
        event_data.pop("audit_id", None)
        return json.dumps(
            event_data, sort_keys=True, separators=(",", ":"),
            default=cls._json_default,
        )

    @staticmethod
    def _deserialize(event_json: str) -> LearningEvent:
        """Rebuild a LearningEvent, restoring the types JSON flattened.

        `LearningEvent(**json.loads(...))` (the original) handed back
        `timestamp_utc` as a str and `event_type` as a str, so a round-tripped
        event was NOT equal to the one written and `event.timestamp_utc` had no
        datetime API. Reconstruct the real types.
        """
        data = json.loads(event_json)
        # Tolerate schema drift. This is an APPEND-ONLY audit store: events
        # written by an older (or newer) build must stay readable, and
        # `LearningEvent(**data)` raises TypeError on any key the current
        # dataclass does not declare — which would make a whole tenant's
        # history unreadable after one field is added.
        known = {f.name for f in fields(LearningEvent)}
        dropped = set(data) - known
        if dropped:
            print(f"[WARN] EventStore: ignoring unknown event field(s) "
                  f"{sorted(dropped)} — schema drift")
            data = {k: v for k, v in data.items() if k in known}
        raw_ts = data.get("timestamp_utc")
        if isinstance(raw_ts, str):
            try:
                data["timestamp_utc"] = datetime.fromisoformat(raw_ts)
            except ValueError:
                data["timestamp_utc"] = datetime.utcnow()
        raw_type = data.get("event_type")
        if not isinstance(raw_type, LearningEventType):
            data["event_type"] = LearningEventType(raw_type)
        return LearningEvent(**data)

    def write_event(self, event: LearningEvent) -> str:
        """Write event to store with hash-chaining.

        Args:
            event: LearningEvent to write

        Returns:
            Hash of the written event
        """
        with self._lock:
            # Serialize event (deterministic — see _serialize)
            event_json = self._serialize(event)

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

        return self._deserialize(row[0])

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
            events.append(self._deserialize(event_json))

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
            events.append(self._deserialize(event_json))

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
