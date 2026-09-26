"""Session Bridge Producer - Create snapshots with real context (not just telemetry).

This module implements the PRODUCER side of ADR-0541 Session Bridging.
It captures the current session's context and creates cryptographically-signed snapshots.

Features:
- SessionContextSnapshot: GDPR-safe metadata (no conversation content)
- Bridge event emission: audit-chained, tenant-scoped
- Integration with EventStore for immutable storage
- Hash-chain verification ready

Based on ADR-0541 (Session Bridging - EventStore Protocol) Amendment.
Depends on: ADR-0314 (Learning Events), ADR-0232 (Audit Chain)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionContextSnapshot:
    """Serializable session context snapshot (GDPR-safe: no conversation content).

    This captures all metadata needed to restore a session's state without
    storing actual conversations (which may contain PII).
    """

    # Identity
    tenant_id: str
    task_id: str
    session_id: str

    # Conversation tracking (metadata only, no content)
    last_message_hash: str                    # SHA256 of last message (for verification)
    conversation_turn_count: int              # How many turns so far

    # Worktree state
    worktree_path: str
    base_commit: str                          # Git commit at session start
    current_branch: str = "main"

    # Task structure
    phase_name: str                           # e.g., "Phase 6c: Visualization"
    active_subtasks: List[str] = field(default_factory=list)  # ["task1", "task2"]
    current_file_being_edited: Optional[str] = None

    # Plan tracking
    plan_id: str = ""
    plan_current_step: int = 0
    plan_total_steps: int = 0

    # Tools & Artifacts
    open_tool_calls: Dict[str, str] = field(default_factory=dict)  # {"agent_id": "state"}
    last_artifact_id: Optional[str] = None
    artifact_list: List[str] = field(default_factory=list)  # ["art_1", "art_2"]

    # Timestamps
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    session_start_time: Optional[str] = None

    # Cryptographic binding
    content_hash: str = ""                    # SHA256(all other fields except signatures)
    prev_snapshot_hash: Optional[str] = None  # Links to prior snapshot (chain)

    def compute_content_hash(self) -> str:
        """Compute SHA256 hash of snapshot content (for chaining).

        Excludes content_hash and prev_snapshot_hash from the computation
        to avoid circular dependency.
        """
        payload = json.dumps({
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "last_message_hash": self.last_message_hash,
            "conversation_turn_count": self.conversation_turn_count,
            "worktree_path": self.worktree_path,
            "base_commit": self.base_commit,
            "current_branch": self.current_branch,
            "phase_name": self.phase_name,
            "active_subtasks": sorted(self.active_subtasks),
            "current_file_being_edited": self.current_file_being_edited,
            "plan_id": self.plan_id,
            "plan_current_step": self.plan_current_step,
            "plan_total_steps": self.plan_total_steps,
            "open_tool_calls": self.open_tool_calls,
            "last_artifact_id": self.last_artifact_id,
            "artifact_list": sorted(self.artifact_list),
            "timestamp": self.timestamp,
            "session_start_time": self.session_start_time,
            "prev_snapshot_hash": self.prev_snapshot_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = asdict(self)
        if not d['content_hash']:
            d['content_hash'] = self.compute_content_hash()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> SessionContextSnapshot:
        """Create from dictionary (deserialization)."""
        return cls(**d)


@dataclass(frozen=True)
class SessionBridgeEvent:
    """Immutable bridge event for audit trail (hash-chained).

    Represents the handoff from one session to the next.
    """

    # Event metadata
    event_type: str = "session_bridge_created"
    tenant_id: str = "_default"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Bridge content
    source_session_id: str = ""
    dest_session_id: Optional[str] = None  # Can be None if destination not yet known
    task_id: str = ""

    # Snapshot reference
    snapshot: Optional[SessionContextSnapshot] = None
    snapshot_hash: str = ""                # Hash of the snapshot

    # Audit chain
    hash: str = ""                         # This event's hash
    prev_hash: str = ""                    # Previous event's hash (chain link)

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this event."""
        payload = json.dumps({
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "source_session_id": self.source_session_id,
            "dest_session_id": self.dest_session_id,
            "task_id": self.task_id,
            "snapshot_hash": self.snapshot_hash,
            "prev_hash": self.prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class SessionBridgeProducer:
    """Creates snapshots at session boundaries and emits bridge events.

    Wiring point: Called from chat_runtime.py::finalize_response()
    after each turn/phase completes.
    """

    def __init__(self, event_store_path: Optional[Path] = None):
        """Initialize producer.

        Args:
            event_store_path: Path to event store (default: ~/.corvin/tenants/_default/global/forge/audit.jsonl)
        """
        if event_store_path is None:
            event_store_path = (
                Path.home()
                / ".corvin" / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
            )
        self.event_store_path = event_store_path

    def create_snapshot(
        self,
        *,
        tenant_id: str,
        task_id: str,
        session_id: str,
        last_message_hash: str,
        conversation_turn_count: int,
        worktree_path: str,
        base_commit: str,
        phase_name: str,
        active_subtasks: Optional[List[str]] = None,
        current_file_being_edited: Optional[str] = None,
        plan_id: str = "",
        plan_current_step: int = 0,
        plan_total_steps: int = 0,
        open_tool_calls: Optional[Dict[str, str]] = None,
        last_artifact_id: Optional[str] = None,
        prev_snapshot: Optional[SessionContextSnapshot] = None,
    ) -> SessionContextSnapshot:
        """Create a snapshot of current session context.

        Args:
            All parameters are required by caller (chat_runtime.py::finalize_response).
            prev_snapshot: Previous snapshot to chain from (for hash-chain).

        Returns:
            SessionContextSnapshot ready for serialization and bridge event emission.
        """

        # FIX #1: Compute hash BEFORE creating frozen dataclass (not after)
        # Dataclass is frozen=True, so cannot mutate fields after creation
        temp_dict = {
            "tenant_id": tenant_id,
            "task_id": task_id,
            "session_id": session_id,
            "last_message_hash": last_message_hash,
            "conversation_turn_count": conversation_turn_count,
            "worktree_path": worktree_path,
            "base_commit": base_commit,
            "phase_name": phase_name,
            "active_subtasks": active_subtasks or [],
            "current_file_being_edited": current_file_being_edited,
            "plan_id": plan_id,
            "plan_current_step": plan_current_step,
            "plan_total_steps": plan_total_steps,
            "open_tool_calls": open_tool_calls or {},
            "last_artifact_id": last_artifact_id,
            "prev_snapshot_hash": prev_snapshot.content_hash if prev_snapshot else None,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Compute hash from dict before creating frozen dataclass
        payload = json.dumps(temp_dict, sort_keys=True)
        content_hash = hashlib.sha256(payload.encode()).hexdigest()

        snapshot = SessionContextSnapshot(
            tenant_id=tenant_id,
            task_id=task_id,
            session_id=session_id,
            last_message_hash=last_message_hash,
            conversation_turn_count=conversation_turn_count,
            worktree_path=worktree_path,
            base_commit=base_commit,
            phase_name=phase_name,
            active_subtasks=active_subtasks or [],
            current_file_being_edited=current_file_being_edited,
            plan_id=plan_id,
            plan_current_step=plan_current_step,
            plan_total_steps=plan_total_steps,
            open_tool_calls=open_tool_calls or {},
            last_artifact_id=last_artifact_id,
            prev_snapshot_hash=prev_snapshot.content_hash if prev_snapshot else None,
            timestamp=datetime.utcnow().isoformat(),
            content_hash=content_hash,  # ← Set immutably here
        )

        logger.info(
            f"Snapshot created for task={task_id}, session={session_id}, "
            f"phase={phase_name}, turns={conversation_turn_count}"
        )

        return snapshot

    def emit_bridge_event(
        self,
        snapshot: SessionContextSnapshot,
        source_session_id: str,
        dest_session_id: Optional[str] = None,
        prev_event_hash: str = "",
    ) -> SessionBridgeEvent:
        """Emit bridge event to audit trail (hash-chained).

        Called after snapshot is created and persisted.
        This event serves as the proof-of-handoff in the audit chain.

        Args:
            snapshot: SessionContextSnapshot from create_snapshot()
            source_session_id: Current session
            dest_session_id: Next session (can be None, filled on next resumption)
            prev_event_hash: Previous event hash for chaining

        Returns:
            SessionBridgeEvent that was emitted.
        """

        bridge_event = SessionBridgeEvent(
            event_type="session_bridge_created",
            tenant_id=snapshot.tenant_id,
            timestamp=datetime.utcnow().isoformat(),
            source_session_id=source_session_id,
            dest_session_id=dest_session_id,
            task_id=snapshot.task_id,
            snapshot=snapshot,
            snapshot_hash=snapshot.content_hash,
            prev_hash=prev_event_hash,
        )

        # Compute this event's hash
        bridge_event.hash = bridge_event.compute_hash()

        # FIX #3: Persist snapshot to disk BEFORE audit event
        try:
            self._persist_snapshot_to_disk(snapshot)
        except Exception as e:
            logger.error(f"Failed to persist snapshot: {e}", exc_info=True)
            raise RuntimeError(
                f"Session snapshot persistence failed (fail-closed): {e}"
            ) from e

        # Emit to audit trail (fail-closed if audit write fails)
        try:
            self._write_audit_event(bridge_event)
            logger.info(
                f"Bridge event emitted: {source_session_id} → {dest_session_id} "
                f"for task={snapshot.task_id}"
            )
        except Exception as e:
            logger.error(f"Failed to emit bridge event: {e}", exc_info=True)
            raise RuntimeError(
                f"Session bridge event emission failed (fail-closed): {e}"
            ) from e

        return bridge_event

    def _persist_snapshot_to_disk(self, snapshot: SessionContextSnapshot) -> None:
        """FIX #3: Persist snapshot to disk for next session recovery.

        Stores snapshot at tenant-scoped location:
        ~/.corvin/tenants/{tenant_id}/infinite_session/snapshots/{task_id}/latest.json
        """
        snapshot_dir = (
            Path.home()
            / ".corvin" / "tenants" / snapshot.tenant_id
            / "infinite_session" / "snapshots" / snapshot.task_id
        )
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshot_file = snapshot_dir / "latest.json"
        snapshot_data = {
            "snapshot": snapshot.to_dict(),
            "timestamp": datetime.utcnow().isoformat(),
        }

        with open(snapshot_file, "w") as f:
            json.dump(snapshot_data, f, indent=2)

        logger.info(f"Snapshot persisted: {snapshot_file}")

    def _write_audit_event(self, event: SessionBridgeEvent) -> None:
        """Write bridge event to audit trail (append-only).

        This is the low-level audit trail write. In production, this would go through
        the central audit backend (ADR-0232), not directly to a file.

        For now, we write to the EventStore file for verification.

        FIX #5: Tenant-scoped audit path isolation.
        """

        # FIX #5: Use tenant-scoped path for audit trail
        event_store_path = (
            Path.home()
            / ".corvin" / "tenants" / event.tenant_id
            / "global" / "forge" / "audit.jsonl"
        )
        event_store_path.parent.mkdir(parents=True, exist_ok=True)

        # Serialize event
        event_dict = {
            "event_type": event.event_type,
            "tenant_id": event.tenant_id,
            "timestamp": event.timestamp,
            "source_session_id": event.source_session_id,
            "dest_session_id": event.dest_session_id,
            "task_id": event.task_id,
            "snapshot_hash": event.snapshot_hash,
            "hash": event.hash,
            "prev_hash": event.prev_hash,
        }

        # Append to audit trail (append-only, one JSON per line)
        with open(event_store_path, "a") as f:
            f.write(json.dumps(event_dict) + "\n")


class SnapshotError(Exception):
    """Raised when snapshot creation/verification fails."""
    pass


class BridgeEmissionError(Exception):
    """Raised when bridge event emission fails."""
    pass
