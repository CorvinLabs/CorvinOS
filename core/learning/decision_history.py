"""Phase 3.2: Decision History (ADR-0316) — User Choices Tracking.

Immutable, append-only log of user decisions on Skill recommendations.
Separate from EventStore (ADR-0314) for query optimization.
Tenant-isolated per GDPR Art. 32; redacts secrets at write time.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Union
from uuid import uuid4

logger = logging.getLogger(__name__)


class UserAction(str, Enum):
    """User action on Skill decision (ADR-0316)."""

    ACCEPT = "accept"  # User approved decision
    REJECT = "reject"  # User rejected decision
    MODIFY = "modify"  # User modified decision
    IGNORE = "ignore"  # User ignored (didn't act)


@dataclass(frozen=True)
class DecisionRecord:
    """Immutable decision record (ADR-0316, GDPR Art. 30, 32).

    Tracks what choice was made, among which candidates, with confidence.
    Append-only: corrections are new records, not updates (ADR-0232/0233).
    """

    decision_id: str  # UUID4, unique
    choice_type: str  # "skill_selection", "model_choice", "routing", etc.
    candidates: list[str]  # All options presented
    chosen: str  # The selection
    timestamp_utc: datetime  # When decided
    session_id: str  # Which session?
    tenant_id: str  # GDPR Art. 32 isolation
    version: str = "1.0"

    # Optional context
    confidence_score: Optional[float] = None  # From ADR-0315 [0.0, 1.0]
    reasoning: Optional[str] = None  # Why this choice? (sanitized)
    user_id: Optional[str] = None  # For GDPR erasure (Art. 17)

    # Metadata (audit trail)
    lom: Optional[str] = None  # Line of Moral Responsibility
    prev_hash: Optional[str] = None  # Previous event hash (chain link)

    def __post_init__(self):
        """Validate at construction (fail-closed)."""
        if not self.tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32)")
        if not self.decision_id:
            raise ValueError("decision_id required")
        if self.chosen not in self.candidates:
            raise ValueError(f"chosen '{self.chosen}' not in candidates {self.candidates}")
        if len(self.candidates) > 100:
            raise ValueError(f"Too many candidates: {len(self.candidates)} > 100")
        if self.confidence_score is not None and not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError(f"Invalid confidence_score: {self.confidence_score} not in [0.0, 1.0]")

    def to_payload(self) -> dict:
        """Convert to event payload (for ADR-0314 EventStore emission)."""
        return {
            "decision_id": self.decision_id,
            "choice_type": self.choice_type,
            "candidates": self.candidates,
            "chosen": self.chosen,
            "timestamp_utc": self.timestamp_utc.isoformat() + "Z",
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "confidence_score": self.confidence_score,
            "reasoning": self.reasoning,
            "user_id": self.user_id,
            "version": self.version,
            "lom": self.lom,
        }


@dataclass
class DecisionRecorder:
    """Create DecisionRecord instances with validation and sanitization (ADR-0316)."""

    tenant_id: str

    # Secret patterns to redact from reasoning (fail-closed)
    _SECRET_PATTERNS = [
        r"api[_-]?key\s*=\s*[a-zA-Z0-9_\-]+",
        r"password\s*=\s*[^\s]+",
        r"token\s*=\s*[a-zA-Z0-9_\-\.]+",
        r"secret\s*=\s*[a-zA-Z0-9_\-]+",
        r"auth\s*=\s*[^\s]+",
    ]

    def create_decision(
        self,
        choice_type: str,
        candidates: list[str],
        chosen: str,
        session_id: str,
        *,
        confidence_score: Optional[float] = None,
        reasoning: Optional[str] = None,
        user_id: Optional[str] = None,
        lom: Optional[str] = None,
    ) -> DecisionRecord:
        """Create a decision record with validation and sanitization.

        Args:
            choice_type: Type of choice (e.g., "skill_selection")
            candidates: Options presented
            chosen: The selected option
            session_id: Session identifier
            confidence_score: Optional confidence [0.0, 1.0]
            reasoning: Optional explanation (will be sanitized)
            user_id: Optional user identifier (for GDPR erasure)
            lom: Optional line-of-moral-responsibility

        Returns:
            DecisionRecord (immutable)

        Raises:
            ValueError: If validation fails
        """
        # Sanitize reasoning (fail-closed: redact suspected secrets)
        sanitized_reasoning = reasoning
        if reasoning:
            for pattern in self._SECRET_PATTERNS:
                if re.search(pattern, reasoning, re.IGNORECASE):
                    sanitized_reasoning = "[redacted]"
                    break

        return DecisionRecord(
            decision_id=str(uuid4()),
            choice_type=choice_type,
            candidates=candidates,
            chosen=chosen,
            timestamp_utc=datetime.utcnow(),
            session_id=session_id,
            tenant_id=self.tenant_id,
            confidence_score=confidence_score,
            reasoning=sanitized_reasoning,
            user_id=user_id,
            lom=lom,
        )


class DecisionHistoryStore:
    """Persistent, query-optimized decision history store (ADR-0316).

    SQLite-backed, date-partitioned schema, tenant-isolated, append-only.
    Separate from EventStore (ADR-0314) for performance & isolation.
    """

    _lock = threading.RLock()

    def __init__(self, db_path: Union[str, Path]):
        """Initialize or open decision history database.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create schema on first init
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection (thread-local)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Initialize database schema (idempotent)."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Main decisions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    choice_type TEXT NOT NULL,
                    candidates TEXT NOT NULL,  -- JSON array
                    chosen TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    confidence_score REAL,
                    reasoning TEXT,
                    user_id TEXT,
                    version TEXT DEFAULT '1.0',
                    lom TEXT,
                    prev_hash TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tenant_timestamp
                ON decisions(tenant_id, timestamp_utc)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tenant_choice_type
                ON decisions(tenant_id, choice_type)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_session
                ON decisions(session_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_id
                ON decisions(user_id, tenant_id)
            """)

            conn.commit()
            conn.close()

    def record_decision(self, decision: DecisionRecord) -> str:
        """Record a decision persistently (append-only).

        Args:
            decision: DecisionRecord to persist

        Returns:
            decision_id (immutable, same as input)

        Raises:
            ValueError: If decision already exists or validation fails
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            try:
                cursor.execute(
                    """
                    INSERT INTO decisions (
                        decision_id, choice_type, candidates, chosen,
                        timestamp_utc, session_id, tenant_id, confidence_score,
                        reasoning, user_id, version, lom, prev_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.decision_id,
                        decision.choice_type,
                        json.dumps(decision.candidates),
                        decision.chosen,
                        decision.timestamp_utc.isoformat() + "Z",
                        decision.session_id,
                        decision.tenant_id,
                        decision.confidence_score,
                        decision.reasoning,
                        decision.user_id,
                        decision.version,
                        decision.lom,
                        decision.prev_hash,
                        datetime.utcnow().isoformat() + "Z",
                    ),
                )
                conn.commit()
                logger.debug(f"Recorded decision {decision.decision_id}")
                return decision.decision_id

            except sqlite3.IntegrityError:
                raise ValueError(f"Decision {decision.decision_id} already exists")
            finally:
                conn.close()

    def get_decision(self, decision_id: str, *, tenant_id: str) -> Optional[DecisionRecord]:
        """Retrieve a single decision by ID (tenant-scoped, GDPR Art. 32).

        Args:
            decision_id: Decision identifier
            tenant_id: Tenant identifier (required; a row of another tenant is
                never returned, even when the decision_id is known)

        Returns:
            DecisionRecord or None if not found in this tenant
        """
        if not tenant_id:
            raise ValueError("tenant_id is required")
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            try:
                cursor.execute(
                    "SELECT * FROM decisions WHERE decision_id = ? AND tenant_id = ?",
                    (decision_id, tenant_id),
                )
                row = cursor.fetchone()

                if not row:
                    return None

                return DecisionRecord(
                    decision_id=row["decision_id"],
                    choice_type=row["choice_type"],
                    candidates=json.loads(row["candidates"]),
                    chosen=row["chosen"],
                    timestamp_utc=datetime.fromisoformat(row["timestamp_utc"].rstrip("Z")),
                    session_id=row["session_id"],
                    tenant_id=row["tenant_id"],
                    version=row["version"],
                    confidence_score=row["confidence_score"],
                    reasoning=row["reasoning"],
                    user_id=row["user_id"],
                    lom=row["lom"],
                    prev_hash=row["prev_hash"],
                )

            finally:
                conn.close()

    def get_decisions_by_type(
        self, tenant_id: str, choice_type: str, limit: int = 1000
    ) -> list[DecisionRecord]:
        """Query decisions by type (for a tenant).

        Args:
            tenant_id: Tenant identifier (tenant isolation)
            choice_type: Type filter (e.g., "skill_selection")
            limit: Max results

        Returns:
            List of DecisionRecord
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            try:
                cursor.execute(
                    """
                    SELECT * FROM decisions
                    WHERE tenant_id = ? AND choice_type = ?
                    ORDER BY timestamp_utc DESC
                    LIMIT ?
                    """,
                    (tenant_id, choice_type, limit),
                )
                rows = cursor.fetchall()

                return [
                    DecisionRecord(
                        decision_id=row["decision_id"],
                        choice_type=row["choice_type"],
                        candidates=json.loads(row["candidates"]),
                        chosen=row["chosen"],
                        timestamp_utc=datetime.fromisoformat(row["timestamp_utc"].rstrip("Z")),
                        session_id=row["session_id"],
                        tenant_id=row["tenant_id"],
                        version=row["version"],
                        confidence_score=row["confidence_score"],
                        reasoning=row["reasoning"],
                        user_id=row["user_id"],
                        lom=row["lom"],
                        prev_hash=row["prev_hash"],
                    )
                    for row in rows
                ]

            finally:
                conn.close()

    def get_decisions_by_date_range(
        self, tenant_id: str, start: datetime, end: datetime, limit: int = 1000
    ) -> list[DecisionRecord]:
        """Query decisions within a date range.

        Args:
            tenant_id: Tenant identifier
            start: Start datetime (inclusive)
            end: End datetime (inclusive)
            limit: Max results

        Returns:
            List of DecisionRecord
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            try:
                cursor.execute(
                    """
                    SELECT * FROM decisions
                    WHERE tenant_id = ?
                    AND timestamp_utc >= ? AND timestamp_utc <= ?
                    ORDER BY timestamp_utc DESC
                    LIMIT ?
                    """,
                    (
                        tenant_id,
                        start.isoformat() + "Z",
                        end.isoformat() + "Z",
                        limit,
                    ),
                )
                rows = cursor.fetchall()

                return [
                    DecisionRecord(
                        decision_id=row["decision_id"],
                        choice_type=row["choice_type"],
                        candidates=json.loads(row["candidates"]),
                        chosen=row["chosen"],
                        timestamp_utc=datetime.fromisoformat(row["timestamp_utc"].rstrip("Z")),
                        session_id=row["session_id"],
                        tenant_id=row["tenant_id"],
                        version=row["version"],
                        confidence_score=row["confidence_score"],
                        reasoning=row["reasoning"],
                        user_id=row["user_id"],
                        lom=row["lom"],
                        prev_hash=row["prev_hash"],
                    )
                    for row in rows
                ]

            finally:
                conn.close()

    def get_decisions_by_session(
        self, session_id: str, *, tenant_id: str, limit: int = 1000
    ) -> list[DecisionRecord]:
        """Query all decisions in a session (tenant-scoped, GDPR Art. 32).

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier (required)
            limit: Max results

        Returns:
            List of DecisionRecord
        """
        if not tenant_id:
            raise ValueError("tenant_id is required")
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            try:
                cursor.execute(
                    """
                    SELECT * FROM decisions
                    WHERE session_id = ? AND tenant_id = ?
                    ORDER BY timestamp_utc ASC
                    LIMIT ?
                    """,
                    (session_id, tenant_id, limit),
                )
                rows = cursor.fetchall()

                return [
                    DecisionRecord(
                        decision_id=row["decision_id"],
                        choice_type=row["choice_type"],
                        candidates=json.loads(row["candidates"]),
                        chosen=row["chosen"],
                        timestamp_utc=datetime.fromisoformat(row["timestamp_utc"].rstrip("Z")),
                        session_id=row["session_id"],
                        tenant_id=row["tenant_id"],
                        version=row["version"],
                        confidence_score=row["confidence_score"],
                        reasoning=row["reasoning"],
                        user_id=row["user_id"],
                        lom=row["lom"],
                        prev_hash=row["prev_hash"],
                    )
                    for row in rows
                ]

            finally:
                conn.close()

    def get_candidate_stats(self, tenant_id: str, choice_type: str) -> dict:
        """Compute statistics for each candidate.

        Returns:
            {candidate: {"total": int, "chosen": int, "selection_rate": float}}
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            try:
                # Get all decisions for this type
                cursor.execute(
                    """
                    SELECT candidates, chosen FROM decisions
                    WHERE tenant_id = ? AND choice_type = ?
                    """,
                    (tenant_id, choice_type),
                )
                rows = cursor.fetchall()

                stats = {}
                for row in rows:
                    candidates = json.loads(row["candidates"])
                    chosen = row["chosen"]

                    for candidate in candidates:
                        if candidate not in stats:
                            stats[candidate] = {"total": 0, "chosen": 0}
                        stats[candidate]["total"] += 1
                        if candidate == chosen:
                            stats[candidate]["chosen"] += 1

                # Compute selection_rate
                for candidate in stats:
                    total = stats[candidate]["total"]
                    stats[candidate]["selection_rate"] = (
                        stats[candidate]["chosen"] / total if total > 0 else 0
                    )

                return stats

            finally:
                conn.close()

    def delete_user_decisions(self, tenant_id: str, user_id: str) -> int:
        """Delete all decisions for a user (GDPR Art. 17 — Right to Erasure).

        Args:
            tenant_id: Tenant identifier
            user_id: User identifier

        Returns:
            Number of rows deleted
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            try:
                cursor.execute(
                    """
                    DELETE FROM decisions
                    WHERE tenant_id = ? AND user_id = ?
                    """,
                    (tenant_id, user_id),
                )
                conn.commit()
                deleted = cursor.rowcount
                logger.info(f"Deleted {deleted} decisions for user {user_id} (GDPR erasure)")
                return deleted

            finally:
                conn.close()

    def cleanup_old_decisions(self, tenant_id: str, days: int = 90) -> int:
        """Delete decisions older than N days (retention policy).

        Args:
            tenant_id: Tenant identifier
            days: Retention period in days

        Returns:
            Number of rows deleted
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()

            try:
                cursor.execute(
                    """
                    DELETE FROM decisions
                    WHERE tenant_id = ? AND timestamp_utc < ?
                    """,
                    (tenant_id, cutoff.isoformat() + "Z"),
                )
                conn.commit()
                deleted = cursor.rowcount
                logger.info(f"Cleaned up {deleted} decisions older than {days} days")
                return deleted

            finally:
                conn.close()
