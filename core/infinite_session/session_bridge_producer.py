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

NOT WIRED (persistence/bridge event): as of 2026-09-27 (adversarial review)
``emit_bridge_event`` has no production caller — chat_runtime.py only calls
``create_snapshot``; the one other caller, message_completeness_protocol.py,
is itself unreachable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field, asdict, replace
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
    # Defaulted: a non-default field after ``current_branch`` made the whole
    # module fail to import (TypeError at class creation).
    phase_name: str = ""                      # e.g., "Phase 6c: Visualization"
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
            event_store_path: Optional explicit chain file (tests/ops). Default
                ``None`` writes THE tenant chain, ``tenant_audit_chain(tid)``.
        """
        self.event_store_path = Path(event_store_path) if event_store_path else None

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
        )
        # The hash is computed over the FINAL field values with the same
        # function a verifier uses. The previous version hashed a separate dict
        # with its own utcnow() timestamp and a different field set, so
        # ``content_hash`` never equalled ``compute_content_hash()``.
        snapshot = replace(snapshot, content_hash=snapshot.compute_content_hash())

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

        # Compute this event's hash (the dataclass is frozen: assigning the
        # attribute raised FrozenInstanceError on every call)
        bridge_event = replace(bridge_event, hash=bridge_event.compute_hash())

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
        <corvin_home>/tenants/{tenant_id}/infinite_session/snapshots/{task_id}/latest.json
        """
        from core.infinite_session.paths import safe_child, tenant_root  # noqa: PLC0415

        # <corvin_home>/tenants/<tid>/infinite_session/snapshots/<task_id> —
        # honours CORVIN_HOME, validates tenant_id and task_id (no traversal).
        snapshot_dir = safe_child(tenant_root(snapshot.tenant_id) / "snapshots", snapshot.task_id)
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshot_file = snapshot_dir / "latest.json"
        snapshot_dict = snapshot.to_dict()
        # Signed with the same function the recovery side verifies with. The
        # snapshot used to be persisted with NO signature, so
        # ``SessionRecoveryManager`` rejected every snapshot this producer wrote.
        # No snapshot key configured → raises → emit_bridge_event fails closed.
        from core.infinite_session.session_recovery import sign_snapshot  # noqa: PLC0415

        snapshot_data = {
            "snapshot": snapshot_dict,
            "signature": sign_snapshot(snapshot_dict),
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Atomic + owner-only (task/worktree metadata): write a 0o600 temp file
        # in the same directory, then rename over latest.json.
        tmp = snapshot_file.with_name(f".latest.json.{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(snapshot_data, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, snapshot_file)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

        logger.info(f"Snapshot persisted: {snapshot_file}")

    def _write_audit_event(self, event: SessionBridgeEvent) -> None:
        """Write the bridge record to THE tenant audit chain (fail-closed).

        Goes through ``core.infinite_session.paths.core_audit`` →
        ``tenant_audit_chain(tenant_id)``, the one hash chain the boot tripwire
        verifies. The previous version appended its own JSON line to a
        hand-composed ``Path.home()/.corvin/.../forge/audit.jsonl`` — outside
        the core writer's hash linking, so the next chain verification would
        fail — and ignored both ``CORVIN_HOME`` and ``event_store_path``.

        An explicit ``event_store_path`` (tests/ops) redirects the record to
        that file, still written by the core chain writer.
        Content-free: session ids are fingerprinted, never written verbatim.
        """
        from core.infinite_session import paths as _paths  # noqa: PLC0415

        def _fp(value: Optional[str]) -> str:
            return hashlib.sha256(value.encode()).hexdigest()[:16] if value else ""

        details = {
            "task_id": event.task_id,
            "content_hash": event.snapshot_hash,
            "prev_snapshot_hash": event.snapshot.prev_snapshot_hash if event.snapshot else None,
            "bridge_hash": event.hash,
            "source_session_fp": _fp(event.source_session_id),
            "dest_session_fp": _fp(event.dest_session_id),
        }
        details = {k: v for k, v in details.items() if v not in (None, "")}

        if self.event_store_path is None:
            _paths.core_audit(
                "infinite_session.bridge_created", tenant_id=event.tenant_id, details=details
            )
            return

        _paths.validate_tenant_id(event.tenant_id)
        _paths._register_allowlists()
        from forge import security_events  # noqa: PLC0415  # type: ignore[import-not-found]

        security_events.write_event(
            Path(self.event_store_path),
            "infinite_session.bridge_created",
            details={**details, "tenant_id": event.tenant_id},
        )


class SnapshotError(Exception):
    """Raised when snapshot creation/verification fails."""
    pass


class BridgeEmissionError(Exception):
    """Raised when bridge event emission fails."""
    pass
