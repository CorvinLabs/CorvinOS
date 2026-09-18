"""Dual-Write Metrics Collector (OTEL + SQLite).

Phase 2: Skill Execution Metrics Collection
- Write metrics to SQLite (local, durable, single source of truth)
- Async export to OTEL (cloud mirror, observability)
- Learning loop reads from SQLite only (no drift)

ADR-0682 (Multi-Tenant Learning from Distributed OTEL Signals)
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class SkillExecutionStatus(Enum):
    """Skill execution outcome."""
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class SkillMetricsEvent:
    """Immutable skill execution metrics."""
    tenant_id: str
    skill_id: str
    skill_version: str
    execution_duration_ms: float
    status: SkillExecutionStatus
    tokens_used: int = 0
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    input_size_bytes: int = 0
    output_size_bytes: int = 0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    convergence_score: float = 0.5


class DualWriteMetricsCollector:
    """Collects skill execution metrics and writes to both SQLite + OTEL."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        retention_days: int = 7,
        otel_enabled: bool = True,
    ):
        self.db_path = db_path or (Path.home() / ".corvin" / "metrics_v2.db")
        self.retention_days = retention_days
        self.otel_enabled = otel_enabled
        self._lock = threading.RLock()
        self._initialize_db()

    def _initialize_db(self) -> None:
        """Initialize SQLite schema."""
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS skill_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    execution_duration_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    tokens_used INTEGER,
                    error_type TEXT,
                    error_message TEXT,
                    input_size_bytes INTEGER,
                    output_size_bytes INTEGER,
                    convergence_score REAL,
                    timestamp TEXT NOT NULL,
                    exported BOOLEAN DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_skill_metrics_tenant_skill
                ON skill_metrics(tenant_id, skill_id, timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_skill_metrics_exported
                ON skill_metrics(exported, created_at)
            """)
            conn.commit()
            conn.close()

    def record_execution(self, event: SkillMetricsEvent) -> None:
        """Record skill execution metrics (sync write to SQLite)."""
        with self._lock:
            try:
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO skill_metrics (
                        tenant_id, skill_id, skill_version, execution_duration_ms,
                        status, tokens_used, error_type, error_message,
                        input_size_bytes, output_size_bytes, convergence_score, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event.tenant_id, event.skill_id, event.skill_version,
                    event.execution_duration_ms, event.status.value, event.tokens_used,
                    event.error_type, event.error_message, event.input_size_bytes,
                    event.output_size_bytes, event.convergence_score, event.timestamp,
                ))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to record metric: {e}")

    def query_metrics(
        self,
        tenant_id: str,
        skill_ids: Optional[List[str]] = None,
        range_hours: int = 1,
        include_errors_only: bool = False,
    ) -> List[Dict]:
        """Query metrics from SQLite."""
        with self._lock:
            try:
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                where_clauses = ["tenant_id = ?"]
                params: List = [tenant_id]
                if include_errors_only:
                    where_clauses.append("status = ?")
                    params.append("error")
                where_clauses.append("exported = 1")
                where_clauses.append(f"datetime(timestamp) > datetime('now', '-{range_hours} hours')")
                where_clause = " AND ".join(where_clauses)
                query = f"SELECT * FROM skill_metrics WHERE {where_clause} ORDER BY timestamp DESC"
                cursor.execute(query, params)
                rows = cursor.fetchall()
                conn.close()
                return [dict(row) for row in rows]
            except Exception as e:
                logger.error(f"Query failed: {e}")
                return []

    def get_unexported_metrics(self, batch_size: int = 100) -> List[Dict]:
        """Get metrics not yet exported to OTEL."""
        with self._lock:
            try:
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM skill_metrics WHERE exported = 0
                    ORDER BY created_at ASC LIMIT ?
                """, (batch_size,))
                rows = cursor.fetchall()
                conn.close()
                return [dict(row) for row in rows]
            except Exception as e:
                logger.error(f"Failed to fetch unexported metrics: {e}")
                return []

    def mark_exported(self, metric_ids: List[int]) -> None:
        """Mark metrics as exported to OTEL."""
        with self._lock:
            try:
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()
                placeholders = ",".join(["?" for _ in metric_ids])
                cursor.execute(
                    f"UPDATE skill_metrics SET exported = 1 WHERE id IN ({placeholders})",
                    metric_ids
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to mark metrics exported: {e}")


_collector: Optional[DualWriteMetricsCollector] = None
_collector_lock = threading.Lock()


def get_metrics_collector() -> DualWriteMetricsCollector:
    """Get or create the global metrics collector."""
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = DualWriteMetricsCollector()
    return _collector
