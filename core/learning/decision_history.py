"""Decision History — tracking user choices (ADR-0316).

Provides:
1. Immutable decision records (who chose what, when)
2. Time-series storage with query interface
3. GDPR retention policy (90-day default)
4. Tenant isolation per GDPR Art. 5, 6, 32
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4


@dataclass(frozen=True)
class DecisionRecord:
    """Immutable record of a user choice."""

    decision_id: str
    choice_type: str
    candidates: list[str]
    chosen: str
    timestamp_utc: datetime
    session_id: str
    tenant_id: str
    user_id: Optional[str] = None
    confidence_score: Optional[float] = None
    user_input: Optional[str] = None
    reasoning: Optional[str] = None
    task_type: Optional[str] = None

    def to_payload(self) -> dict:
        """Convert to learning event payload."""
        return {
            "decision_id": self.decision_id,
            "choice_type": self.choice_type,
            "candidates": self.candidates,
            "chosen": self.chosen,
            "confidence_score": self.confidence_score,
            "user_input": self.user_input,
            "reasoning": self.reasoning,
            "task_type": self.task_type,
        }


class DecisionRecorder:
    """Record user choices for learning."""

    def __init__(self, tenant_id: str):
        """Initialize recorder.

        Args:
            tenant_id: Tenant ID (for isolation)
        """
        self.tenant_id = tenant_id

    def create_decision(
        self,
        choice_type: str,
        candidates: list[str],
        chosen: str,
        session_id: str,
        user_id: Optional[str] = None,
        confidence_score: Optional[float] = None,
        user_input: Optional[str] = None,
        reasoning: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> DecisionRecord:
        """Create a decision record.

        Args:
            choice_type: Type of choice ("skill_selection", "model_choice", etc.)
            candidates: Available options
            chosen: Which option was selected
            session_id: Session ID
            user_id: Optional user ID (for GDPR erasure)
            confidence_score: Confidence in this choice (from ADR-0315)
            user_input: User's original query/request
            reasoning: Why this choice was made
            task_type: Type of task ("data_analysis", "coding", etc.)

        Returns:
            DecisionRecord (ready to emit as event)

        Raises:
            ValueError: If candidates > 100 or invalid state
        """
        if len(candidates) > 100:
            raise ValueError(f"Too many candidates: {len(candidates)} > 100")

        if chosen not in candidates:
            raise ValueError(f"Chosen '{chosen}' not in candidates {candidates}")

        if confidence_score is not None and not (0.0 <= confidence_score <= 1.0):
            raise ValueError(f"Invalid confidence_score: {confidence_score}")

        # Validate no secrets in reasoning
        if reasoning and self._contains_potential_secret(reasoning):
            reasoning = "[redacted]"

        return DecisionRecord(
            decision_id=str(uuid4()),
            choice_type=choice_type,
            candidates=candidates,
            chosen=chosen,
            timestamp_utc=datetime.utcnow(),
            session_id=session_id,
            tenant_id=self.tenant_id,
            user_id=user_id,
            confidence_score=confidence_score,
            user_input=user_input,
            reasoning=reasoning,
            task_type=task_type,
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


class DecisionHistoryStore:
    """Persistent time-series store for decision records (GDPR Art. 30, 32)."""

    def __init__(self, db_path: str | Path):
        """Initialize decision history store.

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
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    choice_type TEXT NOT NULL,
                    chosen TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_id TEXT,
                    confidence_score REAL,
                    task_type TEXT,
                    candidates_json TEXT NOT NULL,
                    reasoning TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Create indexes separately
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant ON decisions(tenant_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_choice_type ON decisions(choice_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON decisions(timestamp_utc)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON decisions(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON decisions(user_id)")
            conn.commit()

    def record_decision(self, decision: DecisionRecord) -> str:
        """Record a decision persistently.

        Args:
            decision: DecisionRecord to store

        Returns:
            Decision ID
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO decisions (
                        decision_id, tenant_id, choice_type, chosen,
                        timestamp_utc, session_id, user_id, confidence_score,
                        task_type, candidates_json, reasoning, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.decision_id,
                        decision.tenant_id,
                        decision.choice_type,
                        decision.chosen,
                        decision.timestamp_utc.isoformat(),
                        decision.session_id,
                        decision.user_id,
                        decision.confidence_score,
                        decision.task_type,
                        json.dumps(decision.candidates),
                        decision.reasoning,
                        datetime.utcnow().isoformat(),
                    ),
                )
                conn.commit()
        return decision.decision_id

    def get_decision(self, decision_id: str) -> Optional[DecisionRecord]:
        """Retrieve a single decision by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
            )
            row = cursor.fetchone()

        if not row:
            return None

        return self._row_to_decision(row)

    def get_decisions_by_type(
        self, tenant_id: str, choice_type: str, limit: int = 1000
    ) -> list[DecisionRecord]:
        """Query decisions by type (e.g., "skill_selection").

        Args:
            tenant_id: Tenant ID
            choice_type: Type of decision
            limit: Max results

        Returns:
            List of DecisionRecords
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM decisions
                WHERE tenant_id = ? AND choice_type = ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (tenant_id, choice_type, limit),
            )
            rows = cursor.fetchall()

        return [self._row_to_decision(row) for row in rows]

    def get_decisions_by_date_range(
        self,
        tenant_id: str,
        start_date: datetime,
        end_date: datetime,
        limit: int = 1000,
    ) -> list[DecisionRecord]:
        """Query decisions within a date range.

        Args:
            tenant_id: Tenant ID
            start_date: Start of time range (UTC)
            end_date: End of time range (UTC)
            limit: Max results

        Returns:
            List of DecisionRecords
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM decisions
                WHERE tenant_id = ? AND timestamp_utc >= ? AND timestamp_utc <= ?
                ORDER BY timestamp_utc DESC
                LIMIT ?
                """,
                (
                    tenant_id,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    limit,
                ),
            )
            rows = cursor.fetchall()

        return [self._row_to_decision(row) for row in rows]

    def get_decisions_by_session(
        self, session_id: str, limit: int = 1000
    ) -> list[DecisionRecord]:
        """Query all decisions in a session.

        Args:
            session_id: Session ID
            limit: Max results

        Returns:
            List of DecisionRecords
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM decisions
                WHERE session_id = ?
                ORDER BY timestamp_utc
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = cursor.fetchall()

        return [self._row_to_decision(row) for row in rows]

    def get_candidate_stats(self, tenant_id: str, choice_type: str) -> dict:
        """Compute stats for each candidate in a choice type.

        Returns dict of {candidate: {count, chosen_count, selection_rate}}
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT candidates_json, chosen
                FROM decisions
                WHERE tenant_id = ? AND choice_type = ?
                """,
                (tenant_id, choice_type),
            )
            rows = cursor.fetchall()

        stats = {}
        for candidates_json, chosen in rows:
            candidates = json.loads(candidates_json)
            for candidate in candidates:
                if candidate not in stats:
                    stats[candidate] = {"total": 0, "chosen": 0}
                stats[candidate]["total"] += 1
                if candidate == chosen:
                    stats[candidate]["chosen"] += 1

        # Compute selection rates
        for candidate in stats:
            total = stats[candidate]["total"]
            chosen_count = stats[candidate]["chosen"]
            stats[candidate]["selection_rate"] = chosen_count / total if total > 0 else 0.0

        return stats

    def delete_user_decisions(self, tenant_id: str, user_id: str) -> int:
        """Delete all decisions for a user (GDPR Art. 17 right to erasure).

        Args:
            tenant_id: Tenant ID
            user_id: User ID to erase

        Returns:
            Number of decisions deleted
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM decisions WHERE tenant_id = ? AND user_id = ?",
                    (tenant_id, user_id),
                )
                conn.commit()
                return cursor.rowcount

    def cleanup_old_decisions(self, tenant_id: str, days: int = 90) -> int:
        """Delete decisions older than N days (GDPR retention policy).

        Args:
            tenant_id: Tenant ID
            days: Number of days to retain (based on decision timestamp, not creation time)

        Returns:
            Number of decisions deleted
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM decisions WHERE tenant_id = ? AND timestamp_utc < ?",
                    (tenant_id, cutoff),
                )
                conn.commit()
                return cursor.rowcount

    def _row_to_decision(self, row: tuple) -> DecisionRecord:
        """Convert database row to DecisionRecord."""
        (
            decision_id,
            tenant_id,
            choice_type,
            chosen,
            timestamp_utc,
            session_id,
            user_id,
            confidence_score,
            task_type,
            candidates_json,
            reasoning,
            created_at,
        ) = row

        return DecisionRecord(
            decision_id=decision_id,
            tenant_id=tenant_id,
            choice_type=choice_type,
            chosen=chosen,
            timestamp_utc=datetime.fromisoformat(timestamp_utc),
            session_id=session_id,
            user_id=user_id,
            confidence_score=confidence_score,
            task_type=task_type,
            candidates=json.loads(candidates_json),
            reasoning=reasoning,
        )
