"""TokenMetricsDB — Persistent database backend (Phase 2.K=2).

Stores token metrics to SQLite/PostgreSQL for efficient queries and long-term retention.
Immutable events are always written to EventStore; DB is write-through cache.
"""

from __future__ import annotations

import asyncio
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from core.learning.event_schema import LearningEvent, LearningEventType


class TokenMetricsDB:
    """Persistent storage backend for token metrics."""

    def __init__(self, db_path: str | Path = "~/.corvin/token_metrics.db"):
        """Initialize database backend.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _initialize_schema(self):
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
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
                with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
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
