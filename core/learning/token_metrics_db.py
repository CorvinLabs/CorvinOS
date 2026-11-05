"""TokenMetricsDB — Persistent database backend (Phase 2.K=2).

Stores token metrics to SQLite/PostgreSQL for efficient queries and long-term retention.
Immutable events are always written to EventStore; DB is write-through cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Sequence

from core.learning.event_schema import LearningEvent, LearningEventType


#: File name of a tenant's token-metrics database, under ``<tenant_home>/learning/``.
DB_FILENAME = "token_metrics.db"


def default_db_path(tenant_id: str) -> Path:
    """``<tenant_home>/learning/token_metrics.db`` - honours ``CORVIN_HOME``.

    Until 2026-11-05 the default was ``~/.corvin/token_metrics.db``: one file for every
    tenant, outside the tenant home, outside GDPR Art. 17 erasure, and ignoring
    ``CORVIN_HOME`` (review LRN-14). Under ``learning/`` it is a tenant's learning state:
    path-gate protected and removed with the tenant."""
    from core.paths.tenant import tenant_learning_dir  # noqa: PLC0415 - validates the id

    return tenant_learning_dir(tenant_id) / DB_FILENAME


class TokenMetricsDB:
    """Persistent storage backend for token metrics.

    Without ``db_path`` every operation opens the database of the tenant it is asked about
    (:func:`default_db_path`), so one instance serves every tenant of a process and no row
    of tenant A is ever stored in tenant B's file. An explicit ``db_path`` pins one file
    (tests, tooling)."""

    def __init__(self, db_path: str | Path | None = None):
        """Initialize database backend.

        Args:
            db_path: Path to one SQLite database file, or None for the per-tenant default
        """
        self.db_path: Optional[Path] = Path(db_path).expanduser() if db_path is not None else None
        self._ready: set[Path] = set()
        if self.db_path is not None:
            self._initialize_schema(self.db_path)

    def path_for(self, tenant_id: str) -> Path:
        """The database file that holds ``tenant_id``'s rows."""
        return self.db_path if self.db_path is not None else default_db_path(tenant_id)

    @contextlib.contextmanager
    def _connect(self, tenant_id: str, *, create: bool = True) -> Iterator[Optional[sqlite3.Connection]]:
        """An open connection to the tenant's database (closed on exit - an open handle
        blocks deleting the file on Windows). ``create=False`` yields None when the file
        does not exist, so a read never creates an empty database."""
        path = self.path_for(tenant_id)
        if not create and not path.exists():
            yield None
            return
        if path not in self._ready:
            self._initialize_schema(path)
        with contextlib.closing(sqlite3.connect(str(path))) as conn:
            yield conn

    def _initialize_schema(self, path: Path):
        """Create tables if they don't exist."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(str(path))) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    turn_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT,
                    instance_id TEXT NOT NULL,

                    -- Token counts
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    baseline_tokens INTEGER,

                    -- Analysis
                    task_type TEXT,
                    task_domain TEXT,
                    savings_tokens INTEGER,
                    savings_percent REAL,
                    outcome_quality TEXT,
                    latency_ms REAL,

                    -- Subsystem breakdown (JSON)
                    subsystem_tokens TEXT,

                    -- Metadata
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    event_timestamp TEXT,

                    -- Indexing for queries
                    UNIQUE(event_id)
                );
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_id
                ON token_metrics(session_id);
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_turn_id
                ON token_metrics(turn_id);
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at
                ON token_metrics(created_at);
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tenant_id
                ON token_metrics(tenant_id);
            """)

            conn.commit()
        self._ready.add(path)

    async def insert_token_metrics(self, event: LearningEvent) -> str:
        """Insert token metrics event into database (non-blocking via thread).

        Args:
            event: LearningEvent with TOKEN_METRICS payload

        Returns:
            Event ID
        """
        if event.event_type != LearningEventType.TOKEN_METRICS:
            raise ValueError(f"Expected TOKEN_METRICS, got {event.event_type}")

        payload = event.payload.get("token_metrics", {})

        subsystem_tokens_json = json.dumps(
            payload.get("subsystem_tokens", {})
        )

        def _insert_sync() -> None:
            """Blocking I/O — runs in thread pool."""
            try:
                with self._connect(event.tenant_id) as conn:
                    conn.execute("""
                        INSERT INTO token_metrics (
                            event_id, turn_id, session_id, tenant_id, user_id,
                            instance_id, input_tokens, output_tokens, total_tokens,
                            baseline_tokens, task_type, task_domain, savings_tokens,
                            savings_percent, outcome_quality, latency_ms,
                            subsystem_tokens, event_timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.event_id,
                        payload.get("turn_id"),
                        event.session_id,
                        event.tenant_id,
                        event.user_id,
                        # instance_id lives on the EVENT, not in the payload.
                        # Reading it from the payload yielded None against a
                        # NOT NULL column, so EVERY insert raised IntegrityError
                        # and was swallowed below while the caller still got an
                        # event_id back — a write path that reported success and
                        # stored nothing.
                        event.instance_id,
                        payload.get("input_tokens"),
                        payload.get("output_tokens"),
                        payload.get("total_tokens"),
                        payload.get("baseline_tokens"),
                        payload.get("task_type"),
                        payload.get("task_domain"),
                        payload.get("savings_tokens"),
                        payload.get("savings_percent"),
                        payload.get("outcome_quality"),
                        payload.get("latency_ms"),
                        subsystem_tokens_json,
                        event.timestamp_utc.isoformat() if event.timestamp_utc else None,
                    ))
                    conn.commit()
            except sqlite3.IntegrityError as exc:
                # A repeated event_id is a legitimate idempotent re-write and
                # stays silent. Anything else (NOT NULL, CHECK, …) means the row
                # was DROPPED, so it must be visible rather than swallowed.
                if "UNIQUE" in str(exc).upper():
                    return
                logging.warning("token metrics row rejected: %s", exc)

        # Run blocking I/O in thread pool, don't block event loop
        await asyncio.to_thread(_insert_sync)

        return event.event_id

    def query_by_session(self, session_id: str, tenant_id: str, limit: int = 1000) -> list[dict]:
        """Query all token metrics for a session with tenant isolation.

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier (MUST match for access)
            limit: Maximum number of results

        Returns:
            List of token metrics records
        """
        with self._connect(tenant_id, create=False) as conn:
            if conn is None:
                return []
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM token_metrics
                WHERE session_id = ? AND tenant_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session_id, tenant_id, limit))

            return [dict(row) for row in cursor.fetchall()]

    def query_by_turn(self, turn_id: str, tenant_id: str) -> Optional[dict]:
        """Query a single turn's metrics with tenant isolation.

        Args:
            turn_id: Turn identifier
            tenant_id: Tenant identifier (MUST match for access)

        Returns:
            Token metrics record if found
        """
        with self._connect(tenant_id, create=False) as conn:
            if conn is None:
                return None
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM token_metrics
                WHERE turn_id = ? AND tenant_id = ?
            """, (turn_id, tenant_id))

            row = cursor.fetchone()
            return dict(row) if row else None

    def query_by_timespan(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[dict]:
        """Query metrics within a time range.

        Args:
            tenant_id: Tenant identifier
            start: Start time (inclusive)
            end: End time (inclusive)
            limit: Maximum number of results

        Returns:
            List of token metrics records
        """
        with self._connect(tenant_id, create=False) as conn:
            if conn is None:
                return []
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM token_metrics
                WHERE tenant_id = ?
                  AND created_at >= ?
                  AND created_at <= ?
                ORDER BY created_at
                LIMIT ?
            """, (tenant_id, start.isoformat(), end.isoformat(), limit))

            return [dict(row) for row in cursor.fetchall()]

    def aggregate_by_task_type(self, session_id: str, tenant_id: str) -> dict[str, dict]:
        """Aggregate metrics by task type with tenant isolation.

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier

        Returns:
            Aggregated stats by task_type
        """
        rows = self.query_by_session(session_id, tenant_id, limit=10000)

        aggregates = {}
        for row in rows:
            task_type = row["task_type"] or "unknown"

            if task_type not in aggregates:
                aggregates[task_type] = {
                    "turns": 0,
                    "total_tokens": 0,
                    "baseline_tokens": 0,
                    "savings_tokens": 0,
                }

            agg = aggregates[task_type]
            agg["turns"] += 1
            agg["total_tokens"] += row.get("total_tokens", 0) or 0
            agg["baseline_tokens"] += row.get("baseline_tokens", 0) or 0
            agg["savings_tokens"] += row.get("savings_tokens", 0) or 0

        # Calculate savings percent
        for task_type in aggregates:
            agg = aggregates[task_type]
            baseline = agg["baseline_tokens"]
            if baseline > 0:
                agg["savings_percent"] = round(
                    (agg["savings_tokens"] / baseline) * 100, 1
                )
            else:
                agg["savings_percent"] = 0.0

        return aggregates

    def aggregate_by_subsystem(self, session_id: str, tenant_id: str) -> dict[str, dict]:
        """Aggregate metrics by subsystem with tenant isolation.

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier

        Returns:
            Aggregated stats by subsystem
        """
        rows = self.query_by_session(session_id, tenant_id, limit=10000)

        aggregates = {}
        for row in rows:
            subsystem_str = row.get("subsystem_tokens") or "{}"
            try:
                subsystem_tokens = json.loads(subsystem_str)
            except json.JSONDecodeError:
                subsystem_tokens = {}

            for subsystem, tokens in subsystem_tokens.items():
                if subsystem not in aggregates:
                    aggregates[subsystem] = {
                        "count": 0,
                        "total_tokens": 0,
                    }

                agg = aggregates[subsystem]
                agg["count"] += 1
                agg["total_tokens"] += tokens or 0

        return aggregates

    def summary(self, session_id: str, tenant_id: str) -> dict:
        """Get complete session summary with tenant isolation.

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier

        Returns:
            Summary stats
        """
        rows = self.query_by_session(session_id, tenant_id, limit=10000)

        if not rows:
            return {
                "turn_count": 0,
                "total_tokens": 0,
                "baseline_tokens": 0,
                "savings_tokens": 0,
                "savings_percent": 0.0,
                "avg_tokens_per_turn": 0,
                "by_task_type": {},
                "subsystems": {},
            }

        total_tokens = sum(row.get("total_tokens", 0) or 0 for row in rows)
        baseline_tokens = sum(row.get("baseline_tokens", 0) or 0 for row in rows)
        savings_tokens = sum(row.get("savings_tokens", 0) or 0 for row in rows)

        savings_percent = (
            (savings_tokens / baseline_tokens) * 100
            if baseline_tokens > 0
            else 0.0
        )

        return {
            "turn_count": len(rows),
            "total_tokens": total_tokens,
            "baseline_tokens": baseline_tokens,
            "savings_tokens": savings_tokens,
            "savings_percent": round(savings_percent, 1),
            "avg_tokens_per_turn": round(total_tokens / len(rows)) if rows else 0,
            "by_task_type": self.aggregate_by_task_type(session_id, tenant_id),
            "subsystems": self.aggregate_by_subsystem(session_id, tenant_id),
        }

    def latest_session_id(self, tenant_id: str) -> Optional[str]:
        """The session of this tenant's most recent row, or None when it has none."""
        with self._connect(tenant_id, create=False) as conn:
            if conn is None:
                return None
            row = conn.execute(
                "SELECT session_id FROM token_metrics WHERE tenant_id = ? "
                "ORDER BY id DESC LIMIT 1", (tenant_id,)).fetchone()
        return row[0] if row else None

    def erase_subject(self, tenant_id: str, subject_id: str) -> int:
        """GDPR Art. 17: delete the tenant's rows that name ``subject_id`` as their user or
        their session. ``subject_id == tenant_id`` deletes every row of the tenant. Returns
        the number of rows deleted; an absent database deletes nothing. Raises on a database
        error - an erasure that could not run must not read as done."""
        if not subject_id:
            return 0
        with self._connect(tenant_id, create=False) as conn:
            if conn is None:
                return 0
            if subject_id == tenant_id:
                cur = conn.execute("DELETE FROM token_metrics WHERE tenant_id = ?", (tenant_id,))
            else:
                cur = conn.execute(
                    "DELETE FROM token_metrics WHERE tenant_id = ? AND (user_id = ? OR session_id = ?)",
                    (tenant_id, subject_id, subject_id))
            conn.commit()
            return int(cur.rowcount or 0)

    def _tenant_files(self, tenant_id: Optional[str]) -> list[tuple[str, Path]]:
        """``(tenant, file)`` pairs a maintenance pass covers."""
        if self.db_path is not None or tenant_id:
            return [(tenant_id or "", self.path_for(tenant_id or "_default"))]
        from core.paths.tenant import corvin_home  # noqa: PLC0415

        root = Path(corvin_home()) / "tenants"
        if not root.is_dir():
            return []
        return [(d.name, d / "learning" / DB_FILENAME) for d in sorted(root.iterdir())
                if (d / "learning" / DB_FILENAME).is_file()]

    def cleanup_old_metrics(self, days_old: int = 30, tenant_id: str = None) -> int:
        """MEDIUM FIX #6: Delete old metrics records based on TTL (age-based eviction).

        Args:
            days_old: Delete records older than this many days (default 30)
            tenant_id: Optional tenant to limit cleanup (None = all tenants)

        Returns:
            Number of rows deleted
        """
        from datetime import timedelta

        cutoff_date = (datetime.utcnow() - timedelta(days=days_old)).isoformat()

        deleted_count = 0
        for tid, path in self._tenant_files(tenant_id):
            if not path.exists():
                continue
            try:
                with contextlib.closing(sqlite3.connect(str(path))) as conn:
                    if tenant_id:
                        cursor = conn.execute("""
                            DELETE FROM token_metrics
                            WHERE created_at < ? AND tenant_id = ?
                        """, (cutoff_date, tenant_id))
                    else:
                        cursor = conn.execute("""
                            DELETE FROM token_metrics
                            WHERE created_at < ?
                        """, (cutoff_date,))
                    conn.commit()
                    deleted_count += int(cursor.rowcount or 0)
            except sqlite3.Error as e:
                logging.error(f"Failed to cleanup old metrics ({tid or path.name}): {e}")

        logging.info(
            f"Cleanup old metrics: deleted {deleted_count} records older than {days_old} days"
        )
        return deleted_count


# ---------------------------------------------------------------------------
# Backend alias.
#
# `TokenMetricsDB` above IS the SQLite backend (it opens sqlite3 directly).
#
# CAVEAT: this alias only restores the NAME the factory imports -- it does not
# supply the API that tests/unit/test_token_metrics_db_k2.py expects, which is
# a different class: async methods and a persistent `.conn` attribute, neither
# of which TokenMetricsDB has. Those tests still fail, and should: the async
# SqliteMetricsDB they describe was never written. Do not paper over that by
# bolting `conn` onto this class.
# `token_metrics_db_factory.create_metrics_db()` selects a backend by URI and
# refers to the SQLite one by its backend name, so expose that name here rather
# than duplicating the class. When a second backend (PostgresMetricsDB) is
# added, `TokenMetricsDB` becomes the abstract base and this alias is replaced
# by a real subclass -- the factory's contract does not change either way.
# ---------------------------------------------------------------------------
SqliteMetricsDB = TokenMetricsDB
