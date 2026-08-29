"""
Tier 1: Session Manager — Session lifecycle, storage, context preservation.

Responsibilities:
- Create/load/close sessions
- Store session state in SQLite (reproducible)
- Track session metadata (created_at, updated_at, status)
- Audit all session operations (GDPR Art. 30/32)
- Provide context to tier-2/3 (task engine, brain core)

Compliance:
- Every session lifecycle event logged to audit.jsonl
- Session state immutable after closure
- No PII in session metadata
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class SessionStatus(str, Enum):
    """Session lifecycle states."""

    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass
class SessionMetadata:
    """Session metadata (immutable after creation)."""

    session_id: str
    created_at: str
    status: SessionStatus
    task_count: int = 0
    metrics_count: int = 0


class SessionStorage:
    """Persistent session storage (SQLite)."""

    def __init__(self, db_conn: sqlite3.Connection):
        """Initialize session storage.

        Args:
            db_conn: SQLite connection (from bootstrap tier-0)
        """
        self.db = db_conn

    def create_session(self) -> str:
        """Create new session, return session_id.

        Returns:
            session_id (UUID4)
        """
        session_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"

        self.db.execute(
            """
            INSERT INTO sessions (session_id, created_at, updated_at, status, state)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, now, now, SessionStatus.CREATED.value, json.dumps({})),
        )
        self.db.commit()

        return session_id

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load session state from database.

        Returns:
            Session dict or None if not found
        """
        cursor = self.db.execute(
            "SELECT session_id, created_at, updated_at, status, state FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        return {
            "session_id": row[0],
            "created_at": row[1],
            "updated_at": row[2],
            "status": row[3],
            "state": json.loads(row[4]) if row[4] else {},
        }

    def update_session(self, session_id: str, status: SessionStatus, state: Dict):
        """Update session status and state.

        Args:
            session_id: Session ID
            status: New status
            state: Updated state dict
        """
        now = datetime.utcnow().isoformat() + "Z"
        self.db.execute(
            """
            UPDATE sessions SET status = ?, state = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (status.value, json.dumps(state), now, session_id),
        )
        self.db.commit()

    def close_session(self, session_id: str):
        """Close session (immutable after this).

        Args:
            session_id: Session ID to close
        """
        self.update_session(session_id, SessionStatus.CLOSED, {})

    def list_sessions(self, status: Optional[SessionStatus] = None) -> list:
        """List all sessions (optionally filtered by status).

        Returns:
            List of session dicts
        """
        if status:
            cursor = self.db.execute(
                "SELECT session_id, created_at, updated_at, status FROM sessions WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            )
        else:
            cursor = self.db.execute(
                "SELECT session_id, created_at, updated_at, status FROM sessions ORDER BY created_at DESC"
            )

        return [
            {
                "session_id": row[0],
                "created_at": row[1],
                "updated_at": row[2],
                "status": row[3],
            }
            for row in cursor.fetchall()
        ]


class SessionManager:
    """High-level session management API."""

    def __init__(self, db_conn: sqlite3.Connection, audit_chain):
        """Initialize session manager.

        Args:
            db_conn: SQLite connection
            audit_chain: AuditChain instance (from tier-0)
        """
        self.storage = SessionStorage(db_conn)
        self.audit = audit_chain
        self.current_session_id: Optional[str] = None

    def begin_session(self, details: Optional[Dict] = None) -> str:
        """Begin new session, log to audit trail.

        Args:
            details: Optional session details (e.g., purpose, initiator)

        Returns:
            session_id
        """
        session_id = self.storage.create_session()
        self.current_session_id = session_id

        audit_details = {"session_id": session_id}
        if details:
            audit_details.update(details)

        self.audit.write_event(
            event_type="session.created",
            actor="session_manager",
            action="create",
            details=audit_details,
        )

        return session_id

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load existing session (read-only, no audit event).

        Returns:
            Session dict or None
        """
        return self.storage.load_session(session_id)

    def activate_session(self, session_id: str):
        """Activate session (move from created → active).

        Args:
            session_id: Session to activate
        """
        self.storage.update_session(session_id, SessionStatus.ACTIVE, {})
        self.current_session_id = session_id

        self.audit.write_event(
            event_type="session.activated",
            actor="session_manager",
            action="activate",
            details={"session_id": session_id},
        )

    def update_session_state(self, session_id: str, state_update: Dict):
        """Update session state (merge with existing).

        Args:
            session_id: Session to update
            state_update: Dict to merge into state
        """
        current = self.storage.load_session(session_id)
        if not current:
            raise ValueError(f"Session {session_id} not found")

        current_state = current.get("state", {})
        current_state.update(state_update)

        self.storage.update_session(session_id, SessionStatus(current["status"]), current_state)

    def pause_session(self, session_id: str):
        """Pause session (active → paused).

        Args:
            session_id: Session to pause
        """
        current = self.storage.load_session(session_id)
        if not current:
            raise ValueError(f"Session {session_id} not found")

        self.storage.update_session(session_id, SessionStatus.PAUSED, current["state"])

        self.audit.write_event(
            event_type="session.paused",
            actor="session_manager",
            action="pause",
            details={"session_id": session_id},
        )

    def resume_session(self, session_id: str):
        """Resume paused session (paused → active).

        Args:
            session_id: Session to resume
        """
        current = self.storage.load_session(session_id)
        if not current:
            raise ValueError(f"Session {session_id} not found")

        self.storage.update_session(session_id, SessionStatus.ACTIVE, current["state"])

        self.audit.write_event(
            event_type="session.resumed",
            actor="session_manager",
            action="resume",
            details={"session_id": session_id},
        )

    def end_session(self, session_id: str, status: SessionStatus = SessionStatus.COMPLETED):
        """End session (any → closed).

        Args:
            session_id: Session to close
            status: Final status (COMPLETED, FAILED, or CLOSED)
        """
        current = self.storage.load_session(session_id)
        if not current:
            raise ValueError(f"Session {session_id} not found")

        # Ensure status is SessionStatus enum
        if isinstance(status, str):
            status = SessionStatus(status)

        self.storage.update_session(session_id, status, current["state"])
        self.storage.close_session(session_id)

        self.audit.write_event(
            event_type="session.ended",
            actor="session_manager",
            action="close",
            details={"session_id": session_id, "final_status": status.value},
        )

    def get_current_session(self) -> Optional[Dict[str, Any]]:
        """Get current session (None if no active session).

        Returns:
            Current session dict or None
        """
        if not self.current_session_id:
            return None
        return self.storage.load_session(self.current_session_id)

    def list_sessions(self, status: Optional[SessionStatus] = None) -> list:
        """List all sessions.

        Args:
            status: Filter by status (optional)

        Returns:
            List of session dicts
        """
        return self.storage.list_sessions(status)
