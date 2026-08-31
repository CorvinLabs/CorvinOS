"""Outcome Feedback — closed-loop learning (ADR-0317).

Provides:
1. Immutable outcome records (linked to decisions)
2. Feedback loop (async, non-blocking)
3. Training data export (CSV/Parquet)
4. Confidence backprop rules (ADR-0315 integration)
5. GDPR compliance (Art. 5 accuracy, Art. 17 erasure)
"""

from __future__ import annotations

import asyncio
import csv
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional
from uuid import uuid4


class OutcomeType(str, Enum):
    """Outcome classifications."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


@dataclass(frozen=True)
class OutcomeRecord:
    """Immutable record of a choice outcome."""

    outcome_id: str
    decision_id: str
    session_id: str
    outcome: OutcomeType
    timestamp_utc: datetime
    tenant_id: str
    user_id: Optional[str] = None
    feedback_text: Optional[str] = None
    rating: Optional[int] = None
    quality_score: Optional[float] = None
    latency_ms: Optional[int] = None

    def to_payload(self) -> dict:
        """Convert to learning event payload."""
        return {
            "outcome_id": self.outcome_id,
            "decision_id": self.decision_id,
            "outcome": self.outcome.value,
            "feedback_text": self.feedback_text,
            "rating": self.rating,
            "quality_score": self.quality_score,
            "latency_ms": self.latency_ms,
        }


class OutcomeRecorder:
    """Record outcomes of decisions for closed-loop learning."""

    def __init__(self, tenant_id: str):
        """Initialize recorder.

        Args:
            tenant_id: Tenant ID (for isolation)
        """
        self.tenant_id = tenant_id

    def record_outcome(
        self,
        decision_id: str,
        session_id: str,
        outcome: OutcomeType,
        user_id: Optional[str] = None,
        feedback_text: Optional[str] = None,
        rating: Optional[int] = None,
        quality_score: Optional[float] = None,
        latency_ms: Optional[int] = None,
    ) -> OutcomeRecord:
        """Record an outcome for a decision.

        Args:
            decision_id: ID of decision being evaluated
            session_id: Session ID
            outcome: "success", "partial", or "failure"
            user_id: Optional user ID (for GDPR erasure)
            feedback_text: User's feedback on the outcome
            rating: Optional numeric rating (1-5)
            quality_score: Outcome quality (0-1, from metrics)
            latency_ms: Time to outcome in milliseconds

        Returns:
            OutcomeRecord (ready to emit as event)

        Raises:
            ValueError: If invalid rating or quality score
        """
        if rating is not None and not (1 <= rating <= 5):
            raise ValueError(f"Invalid rating: {rating}, must be 1-5")

        if quality_score is not None and not (0.0 <= quality_score <= 1.0):
            raise ValueError(f"Invalid quality_score: {quality_score}, must be 0-1")

        if feedback_text and self._contains_potential_secret(feedback_text):
            feedback_text = "[redacted]"

        return OutcomeRecord(
            outcome_id=str(uuid4()),
            decision_id=decision_id,
            session_id=session_id,
            outcome=outcome,
            timestamp_utc=datetime.utcnow(),
            tenant_id=self.tenant_id,
            user_id=user_id,
            feedback_text=feedback_text,
            rating=rating,
            quality_score=quality_score,
            latency_ms=latency_ms,
        )

    def _contains_potential_secret(self, text: str) -> bool:
        """Check if text might contain secrets using regex patterns."""
        import re

        patterns = [
            r'\b(api_key|api_secret|password|token|credential|secret|auth)\b\s*[=:]',
            r'Bearer\s+[a-zA-Z0-9\-._~+/]+=*',
            r'[a-f0-9]{32,}',  # Hex blobs (MD5+ length)
        ]

        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False


class OutcomeFeedbackStore:
    """Persistent store for outcome records with confidence backprop (ADR-0317)."""

    def __init__(self, db_path: str | Path):
        """Initialize outcome feedback store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_id TEXT,
                    outcome TEXT NOT NULL,
                    rating INTEGER,
                    quality_score REAL,
                    latency_ms INTEGER,
                    timestamp_utc TEXT NOT NULL,
                    feedback_text TEXT,
                    confidence_delta REAL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Create indexes separately
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant ON outcomes(tenant_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decision ON outcomes(decision_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON outcomes(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_outcome ON outcomes(outcome)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON outcomes(user_id)")
            conn.commit()

    def record_outcome(self, outcome: OutcomeRecord) -> str:
        """Record an outcome persistently.

        Args:
            outcome: OutcomeRecord to store

        Returns:
            Outcome ID
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO outcomes (
                        outcome_id, tenant_id, decision_id, session_id, user_id,
                        outcome, rating, quality_score, latency_ms, timestamp_utc,
                        feedback_text, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome.outcome_id,
                        outcome.tenant_id,
                        outcome.decision_id,
                        outcome.session_id,
                        outcome.user_id,
                        outcome.outcome.value,
                        outcome.rating,
                        outcome.quality_score,
                        outcome.latency_ms,
                        outcome.timestamp_utc.isoformat(),
                        outcome.feedback_text,
                        datetime.utcnow().isoformat(),
                    ),
                )
                conn.commit()
        return outcome.outcome_id

    def get_outcome(self, outcome_id: str) -> Optional[OutcomeRecord]:
        """Retrieve a single outcome by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM outcomes WHERE outcome_id = ?", (outcome_id,)
            )
            row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_outcome(row)

    def get_outcomes_by_decision(self, decision_id: str) -> list[OutcomeRecord]:
        """Get all outcomes for a decision."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM outcomes
                WHERE decision_id = ?
                ORDER BY timestamp_utc
                """,
                (decision_id,),
            )
            rows = cursor.fetchall()

        return [self._row_to_outcome(row) for row in rows]

    def get_outcomes_by_type(
        self, tenant_id: str, outcome_type: OutcomeType, limit: int = 1000
    ) -> list[OutcomeRecord]:
        """Get outcomes by type (success/partial/failure).

        Args:
            tenant_id: Tenant ID
            outcome_type: Type of outcome
            limit: Max results

        Returns:
            List of OutcomeRecords
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM outcomes
                WHERE tenant_id = ? AND outcome = ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (tenant_id, outcome_type.value, limit),
            )
            rows = cursor.fetchall()

        return [self._row_to_outcome(row) for row in rows]

    def compute_success_rate(self, tenant_id: str, decision_ids: Optional[list[str]] = None) -> float:
        """Compute success rate (success outcomes / total outcomes).

        Args:
            tenant_id: Tenant ID
            decision_ids: Optional list to filter by decisions

        Returns:
            Success rate (0-1)
        """
        with sqlite3.connect(self.db_path) as conn:
            if decision_ids:
                placeholders = ",".join("?" * len(decision_ids))
                cursor = conn.execute(
                    f"""
                    SELECT outcome FROM outcomes
                    WHERE tenant_id = ? AND decision_id IN ({placeholders})
                    """,
                    [tenant_id] + decision_ids,
                )
            else:
                cursor = conn.execute(
                    "SELECT outcome FROM outcomes WHERE tenant_id = ?", (tenant_id,)
                )
            rows = cursor.fetchall()

        if not rows:
            return 0.5  # Default for no data

        success_count = sum(1 for (outcome,) in rows if outcome == "success")
        return success_count / len(rows) if rows else 0.5

    def export_training_data_csv(self, tenant_id: str, output_path: str | Path) -> int:
        """Export outcomes as CSV for training (no PII).

        Args:
            tenant_id: Tenant ID
            output_path: Path to write CSV

        Returns:
            Number of records exported
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT outcome_id, decision_id, outcome, rating, quality_score,
                       latency_ms, timestamp_utc
                FROM outcomes
                WHERE tenant_id = ?
                ORDER BY timestamp_utc
                """,
                (tenant_id,),
            )
            rows = cursor.fetchall()

        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "outcome_id",
                "decision_id",
                "outcome",
                "rating",
                "quality_score",
                "latency_ms",
                "timestamp_utc",
            ])
            writer.writerows(rows)

        return len(rows)

    def compute_confidence_delta(
        self, outcome: OutcomeType, rating: Optional[int] = None
    ) -> float:
        """Compute confidence adjustment based on outcome (backprop rule).

        Backprop logic:
        - SUCCESS + high_rating → +0.15
        - SUCCESS + no_rating → +0.10
        - PARTIAL → 0.0 (neutral)
        - FAILURE + high_rating → -0.20
        - FAILURE + no_rating → -0.15

        Args:
            outcome: Outcome type
            rating: Optional user rating (1-5)

        Returns:
            Confidence delta to apply to future decisions
        """
        if outcome == OutcomeType.SUCCESS:
            return 0.15 if rating and rating >= 4 else 0.10
        elif outcome == OutcomeType.PARTIAL:
            return 0.0
        else:  # FAILURE
            return -0.20 if rating and rating <= 2 else -0.15

    def delete_user_outcomes(self, tenant_id: str, user_id: str) -> int:
        """Delete all outcomes for a user (GDPR Art. 17).

        Args:
            tenant_id: Tenant ID
            user_id: User ID to erase

        Returns:
            Number of outcomes deleted
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM outcomes WHERE tenant_id = ? AND user_id = ?",
                    (tenant_id, user_id),
                )
                conn.commit()
                return cursor.rowcount

    def cleanup_old_outcomes(self, tenant_id: str, days: int = 90) -> int:
        """Delete outcomes older than N days (retention policy).

        Args:
            tenant_id: Tenant ID
            days: Number of days to retain (based on outcome timestamp, not creation time)

        Returns:
            Number of outcomes deleted
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM outcomes WHERE tenant_id = ? AND timestamp_utc < ?",
                    (tenant_id, cutoff),
                )
                conn.commit()
                return cursor.rowcount

    def _row_to_outcome(self, row: tuple) -> OutcomeRecord:
        """Convert database row to OutcomeRecord."""
        (
            outcome_id,
            tenant_id,
            decision_id,
            session_id,
            user_id,
            outcome,
            rating,
            quality_score,
            latency_ms,
            timestamp_utc,
            feedback_text,
            confidence_delta,
            created_at,
        ) = row

        return OutcomeRecord(
            outcome_id=outcome_id,
            decision_id=decision_id,
            session_id=session_id,
            outcome=OutcomeType(outcome),
            timestamp_utc=datetime.fromisoformat(timestamp_utc),
            tenant_id=tenant_id,
            user_id=user_id,
            feedback_text=feedback_text,
            rating=rating,
            quality_score=quality_score,
            latency_ms=latency_ms,
        )


class OutcomeFeedbackLoop:
    """Async feedback loop for closed-loop learning (non-blocking)."""

    def __init__(self, tenant_id: str, store: OutcomeFeedbackStore, max_queue_size: int = 1000):
        """Initialize feedback loop.

        Args:
            tenant_id: Tenant ID
            store: OutcomeFeedbackStore instance
            max_queue_size: Max pending outcomes
        """
        self.tenant_id = tenant_id
        self.store = store
        self.max_queue_size = max_queue_size
        self.feedback_queue: asyncio.Queue[OutcomeRecord] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the feedback processing worker."""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._process_feedback())

    async def stop(self) -> None:
        """Stop the feedback worker."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def emit_outcome(self, outcome: OutcomeRecord) -> None:
        """Emit an outcome (non-blocking).

        Args:
            outcome: OutcomeRecord to emit
        """
        if outcome.tenant_id != self.tenant_id:
            raise ValueError(f"Tenant mismatch: {outcome.tenant_id} != {self.tenant_id}")

        try:
            self.feedback_queue.put_nowait(outcome)
        except asyncio.QueueFull:
            import logging
            logging.warning(
                f"OutcomeFeedbackLoop queue full, dropping outcome: "
                f"decision={outcome.decision_id}, outcome={outcome.outcome.value}"
            )

    async def _process_feedback(self) -> None:
        """Background worker: persist outcomes."""
        while True:
            try:
                outcome = await self.feedback_queue.get()
                self.store.record_outcome(outcome)
                self.feedback_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                continue

    async def flush(self) -> None:
        """Wait for all pending outcomes."""
        await self.feedback_queue.join()
