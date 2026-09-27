"""Message Completeness Protocol - Ensure final response contains full session state.

This module implements Phase 2 (Message Completeness) of ADR-0541 Session Bridging.

Integration Point: chat_runtime.py::stream_turn()
- After yielding {type: "done"}, emit final message with session_state
- Consumer can use session_state to restore context in next session

Architecture:
- finalize_turn_with_context() - called at turn end
- SessionMessageEnvelope - dataclass for final response with state
- Must include task_id, phase, plan state, artifacts for continuation

Based on ADR-2077 (Message Completeness Protocol; filed as ADR-0542 until 2026-09-27, renumbered — 0542 is the phase-gate validator).
Depends on: ADR-0541 (Session Bridging), ADR-0314 (Learning Events)

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) —
``chat_runtime.stream_turn`` does not call ``finalize_turn_with_context`` (the
"integration guide" below is prose, not code).

Defused 2026-09-27: the envelope claimed ``context_verified: True`` even when
the bridge event (its audit proof) had failed to emit, carried an invented
``expected_duration_minutes: 90``, and ``TurnContextExtractor`` returned
placeholder state ("Phase X: Unknown", empty plan) as if extracted. The
envelope now reports ``context_verified`` = whether the bridge event was
actually written, and the extractor raises ``NotImplementedError``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional, List

from core.infinite_session.session_bridge_producer import (
    SessionContextSnapshot,
    SessionBridgeProducer,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionMessageEnvelope:
    """Final response envelope with full session continuity info.

    This is the LAST message sent in a turn. It contains everything
    the NEXT session needs to resume without context loss.
    """

    # Turn result
    type: str = "session_message"
    user_message: str = ""
    assistant_response: str = ""

    # Session continuity (the critical part)
    session_state: Dict[str, Any] = None

    # Recovery instructions (for operator debugging)
    recovery_instructions: str = ""

    # Next-action guidance
    next_action: Dict[str, Any] = None  # {"type": "...", "instructions": "..."}

    # Metadata
    turn_number: int = 0
    timestamp: str = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to serializable dict."""
        return asdict(self)


class MessageCompletenessGate:
    """Ensure every turn's final message includes session state."""

    def __init__(self, producer: Optional[SessionBridgeProducer] = None):
        """Initialize gate with bridge producer.

        Args:
            producer: SessionBridgeProducer instance (creates snapshots).
                     If None, will be created on demand.
        """
        self.producer = producer or SessionBridgeProducer()

    def finalize_turn_with_context(
        self,
        *,
        # Turn result
        assistant_response: str,
        user_message: str,
        turn_number: int,

        # Session identity
        task_id: str,
        session_id: str,
        tenant_id: str,

        # Conversation tracking
        last_message_hash: str,
        conversation_turn_count: int,

        # Worktree state
        worktree_path: str,
        base_commit: str,

        # Task structure
        phase_name: str,
        active_subtasks: Optional[List[str]] = None,
        current_file_being_edited: Optional[str] = None,

        # Plan state
        plan_id: str = "",
        plan_current_step: int = 0,
        plan_total_steps: int = 0,

        # Artifacts
        open_tool_calls: Optional[Dict[str, str]] = None,
        last_artifact_id: Optional[str] = None,

        # Prior snapshot (for chaining)
        prior_snapshot: Optional[SessionContextSnapshot] = None,
    ) -> SessionMessageEnvelope:
        """Finalize turn: create snapshot + emit bridge event + return message envelope.

        This is the GATE that ensures Message Completeness. Called at the END of
        stream_turn() after the assistant response is fully generated.

        Args:
            All parameters capture the current session state (no conversation content,
            just metadata).

        Returns:
            SessionMessageEnvelope ready to send to client as final message.
            This message includes session_state for continuation.
        """

        # 1. CREATE SNAPSHOT (with Bridge Producer)
        snapshot = self.producer.create_snapshot(
            tenant_id=tenant_id,
            task_id=task_id,
            session_id=session_id,
            last_message_hash=last_message_hash,
            conversation_turn_count=conversation_turn_count,
            worktree_path=worktree_path,
            base_commit=base_commit,
            phase_name=phase_name,
            active_subtasks=active_subtasks,
            current_file_being_edited=current_file_being_edited,
            plan_id=plan_id,
            plan_current_step=plan_current_step,
            plan_total_steps=plan_total_steps,
            open_tool_calls=open_tool_calls,
            last_artifact_id=last_artifact_id,
            prev_snapshot=prior_snapshot,
        )

        # 2. EMIT BRIDGE EVENT (hash-chained, audit trail)
        prev_hash = prior_snapshot.content_hash if prior_snapshot else ""
        bridge_event = None
        try:
            bridge_event = self.producer.emit_bridge_event(
                snapshot=snapshot,
                source_session_id=session_id,
                dest_session_id=None,  # Will be filled on next session resumption
                prev_event_hash=prev_hash,
            )
            logger.info(
                f"Turn #{turn_number} finalized with bridge event: "
                f"task={task_id}, phase={phase_name}"
            )
        except Exception as e:
            logger.error(f"Bridge event emission failed: {e}", exc_info=True)
            # The message is still sent, but it must not CLAIM a verified
            # context: ``context_verified`` below is False in that case.

        # 3. CONSTRUCT SESSION MESSAGE (with full state)
        next_action = {
            "type": "continue_phase",
            "instructions": f"Continue with {phase_name}",
            "blocking_gate": None,
            "expected_duration_minutes": None,  # not estimated
        }

        recovery_instructions = (
            f"If context lost in next session, run: "
            f"corvin recover-session --task_id={task_id} --session_id={session_id}"
        )

        envelope = SessionMessageEnvelope(
            type="session_message",
            user_message=user_message,
            assistant_response=assistant_response,
            session_state={
                "task_id": task_id,
                "session_id": session_id,
                "tenant_id": tenant_id,
                "phase": phase_name,
                "turn_number": turn_number,
                "conversation_turn_count": conversation_turn_count,

                # Context breadcrumbs
                "context_breadcrumbs": {
                    "worktree": worktree_path,
                    "base_commit": base_commit,
                    "last_artifact_id": last_artifact_id,
                    "files_in_progress": [current_file_being_edited] if current_file_being_edited else [],
                },

                # Task continuity
                "task_continuity": {
                    "plan_id": plan_id,
                    "plan_current_step": plan_current_step,
                    "plan_total_steps": plan_total_steps,
                    "active_subtasks": active_subtasks or [],
                },

                # Audit proof
                "audit_trail": {
                    "bridge_event_id": getattr(bridge_event, "hash", "") if bridge_event is not None else "",
                    "snapshot_hash": snapshot.content_hash,
                    "context_verified": bridge_event is not None,
                },
            },
            recovery_instructions=recovery_instructions,
            next_action=next_action,
            turn_number=turn_number,
            timestamp=datetime.utcnow().isoformat(),
        )

        return envelope


def integrate_message_completeness_into_stream_turn():
    """
    Integration guide for chat_runtime.py::stream_turn()

    BEFORE: The final message was just {type: "done"}
    AFTER: It should be SessionMessageEnvelope with full session state

    Code pattern (in stream_turn at the very end):

    ```python
    # ... inside _stream_turn_impl, after all normal processing ...

    # Get final context values (from sess, task, execution context, etc.)
    gate = MessageCompletenessGate()
    envelope = gate.finalize_turn_with_context(
        assistant_response=final_response_text,
        user_message=prompt,
        turn_number=sess.turn_count,

        task_id=task_id,
        session_id=sess.chat_key,
        tenant_id=sess.tenant_id,

        last_message_hash=hash(final_response_text),
        conversation_turn_count=len(turns),

        worktree_path=str(sess.workdir),
        base_commit=current_git_commit(),

        phase_name="Phase X: ...",
        active_subtasks=get_active_subtasks(),
        current_file_being_edited=...,

        plan_id=sess.plan_id if hasattr(sess, 'plan_id') else "",
        plan_current_step=...,
        plan_total_steps=...,

        open_tool_calls=...,
        last_artifact_id=...,

        prior_snapshot=sess.prior_snapshot if hasattr(sess, 'prior_snapshot') else None,
    )

    # Yield the session message envelope as the very last event
    yield envelope.to_dict()
    yield {"type": "done"}
    ```

    This ensures that every response includes session_state, allowing
    the next session to restore full context without loss.
    """
    pass


class TurnContextExtractor:
    """Helper to extract session context — NOT IMPLEMENTED.

    Each method used to return placeholder state ("Phase X: Unknown", an empty
    plan) that a caller could not tell from real extracted state.
    """

    @staticmethod
    def extract_phase_name(sess) -> str:
        raise NotImplementedError("phase extraction is not implemented")

    @staticmethod
    def extract_active_subtasks(sess) -> List[str]:
        raise NotImplementedError("subtask extraction is not implemented")

    @staticmethod
    def extract_plan_state(sess) -> tuple[str, int, int]:
        raise NotImplementedError("plan-state extraction is not implemented")

    @staticmethod
    def extract_artifacts(sess) -> tuple[Optional[str], Dict[str, str]]:
        raise NotImplementedError("artifact extraction is not implemented")


__all__ = [
    "SessionMessageEnvelope",
    "MessageCompletenessGate",
    "TurnContextExtractor",
]
