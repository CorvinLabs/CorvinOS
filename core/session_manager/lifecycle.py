"""SessionLifecycleManager: Detects split-triggers and initiates checkpoints.

k=1: Core dataclasses + SessionLifecycleManager with 6 split triggers:
1. Phase Exit → checkpoint + new phase
2. Context Limit (≥85%) → checkpoint + new session (same phase)
3. Token Burn (≥daily budget) → checkpoint + escalate
4. Explicit Milestone → checkpoint + optional split
5. Iteration Cap (≥50) → checkpoint + new session
6. Stall Detected (no progress ≥30 min) → checkpoint + retry/pivot

ADR-0XXX: Session Manager Architecture
Integrates with: Brain v0.2 Hub, EventBus, HealthMonitor
GDPR Art. 30, 32: Audit every split trigger.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class SessionSplitTrigger(str, Enum):
    """6 Canonical split triggers for session lifecycle."""

    PHASE_EXIT = "phase_exit"
    CONTEXT_LIMIT = "context_limit"
    TOKEN_BURN = "token_burn"
    EXPLICIT_MILESTONE = "explicit_milestone"
    ITERATION_CAP = "iteration_cap"
    STALL_DETECTED = "stall_detected"


@dataclass(frozen=True)
class SessionMetadata:
    """Immutable session metadata."""

    session_id: str
    task_id: str
    phase: str
    started_at: datetime
    tenant_id: str
    user_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    created_by: str = "SessionLifecycleManager"

    def __post_init__(self):
        """Validate metadata."""
        if not self.session_id:
            raise ValueError("session_id is required")
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.phase:
            raise ValueError("phase is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")


@dataclass
class SessionMetrics:
    """Mutable metrics for a session."""

    iterations: int = 0
    context_size_tokens: int = 0
    token_budget_used: float = 0.0  # Fraction [0.0-1.0]
    last_progress_at: datetime = field(default_factory=datetime.utcnow)
    stall_detected_at: Optional[datetime] = None
    total_time_seconds: float = 0.0


@dataclass(frozen=True)
class SplitTriggerEvent:
    """Immutable event representing a split trigger detection.

    Audit-logged per GDPR Art. 30, 32.
    """

    trigger_type: SessionSplitTrigger
    session_id: str
    task_id: str
    phase: str
    tenant_id: str
    timestamp_utc: datetime
    event_id: str = field(default_factory=lambda: str(uuid4()))
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    def to_audit_event(self) -> dict[str, Any]:
        """Convert to audit.jsonl format."""
        return {
            "event_type": f"session.split_trigger.{self.trigger_type.value}",
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "phase": self.phase,
            "timestamp": self.timestamp_utc.isoformat() + "Z",
            "event_id": self.event_id,
            "reason": self.reason,
            "metadata": self.metadata,
        }


class SessionLifecycleManager:
    """Detects 6 split-triggers and initiates checkpoints.

    Integrates with Brain v0.2 Hub for event publishing.
    All split events are audit-logged per GDPR Art. 30, 32.
    """

    # Tunable thresholds (can be adjusted per operator settings)
    CONTEXT_LIMIT_THRESHOLD = 0.85  # 85% of max context
    TOKEN_BURN_THRESHOLD = 0.95  # 95% of daily budget
    ITERATION_CAP = 50
    STALL_DETECTION_MINUTES = 30

    def __init__(self, hub: Optional[Any] = None):
        """Initialize SessionLifecycleManager.

        Args:
            hub: Optional SubsystemHub for event publishing.
                 If None, events are logged but not published.
        """
        self.name = "session_lifecycle_manager"
        self.version = "0.1.0"
        self.hub = hub
        self.active_sessions: dict[str, SessionMetadata] = {}
        self.session_metrics: dict[str, SessionMetrics] = {}

    def startup(self, hub: Any) -> None:
        """Register with SubsystemHub and subscribe to events.

        Args:
            hub: SubsystemHub instance
        """
        self.hub = hub
        logger.info(f"Starting {self.name} v{self.version}")

        # Subscribe to relevant events for monitoring
        if self.hub:
            self.hub.subscribe("task.started", self._on_task_started)
            self.hub.subscribe("task.iteration_completed", self._on_iteration_completed)
            self.hub.subscribe("task.milestone_reached", self._on_milestone_reached)
            self.hub.subscribe("health.stall_detected", self._on_stall_detected)

    def shutdown(self) -> None:
        """Clean up on shutdown."""
        logger.info(f"Shutting down {self.name}")
        self.active_sessions.clear()
        self.session_metrics.clear()

    def create_session(
        self,
        task_id: str,
        phase: str,
        tenant_id: str,
        user_id: Optional[str] = None,
        parent_session_id: Optional[str] = None,
    ) -> SessionMetadata:
        """Create a new session.

        Args:
            task_id: Task identifier
            phase: Phase name (e.g., "planning", "execution", "validation")
            tenant_id: Tenant identifier (GDPR Art. 5)
            user_id: Optional user identifier
            parent_session_id: Optional parent session (for nested tasks)

        Returns:
            SessionMetadata for the new session
        """
        session_id = str(uuid4())
        now = datetime.utcnow()

        metadata = SessionMetadata(
            session_id=session_id,
            task_id=task_id,
            phase=phase,
            started_at=now,
            tenant_id=tenant_id,
            user_id=user_id,
            parent_session_id=parent_session_id,
        )

        self.active_sessions[session_id] = metadata
        self.session_metrics[session_id] = SessionMetrics()

        logger.info(
            f"Created session {session_id} for task {task_id} phase {phase}"
        )

        # Publish session created event (GDPR Art. 30, 32)
        if self.hub:
            try:
                audit_event = {
                    "event_type": "session.created",
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "task_id": task_id,
                    "phase": phase,
                    "timestamp": now.isoformat() + "Z",
                    "user_id": user_id,
                    "parent_session_id": parent_session_id,
                }
                self.hub.publish_event("session.created", audit_event)
            except Exception as e:
                logger.error(f"Failed to publish session created event: {e}")

        return metadata

    def record_iteration(self, session_id: str) -> None:
        """Record an iteration in the current session.

        Args:
            session_id: Session identifier
        """
        if session_id not in self.session_metrics:
            return

        metrics = self.session_metrics[session_id]
        metrics.iterations += 1
        metrics.last_progress_at = datetime.utcnow()

        logger.debug(
            f"Session {session_id}: iteration {metrics.iterations}"
        )

    def update_context_size(self, session_id: str, token_count: int) -> None:
        """Update context size for a session.

        Args:
            session_id: Session identifier
            token_count: Current context size in tokens
        """
        if session_id not in self.session_metrics:
            return

        self.session_metrics[session_id].context_size_tokens = token_count

    def update_token_budget(self, session_id: str, fraction_used: float) -> None:
        """Update token budget usage.

        Args:
            session_id: Session identifier
            fraction_used: Fraction of daily budget used [0.0-1.0]
        """
        if session_id not in self.session_metrics:
            return

        self.session_metrics[session_id].token_budget_used = fraction_used

    def check_split_triggers(
        self, session_id: str, max_context_tokens: int = 200000
    ) -> Optional[SplitTriggerEvent]:
        """Check all 6 split triggers for a session.

        Returns first triggered (highest priority), or None if no trigger.

        Trigger Priority (highest to lowest):
        1. Token Burn (quota exhausted)
        2. Context Limit (approaching max)
        3. Iteration Cap (50+ iterations)
        4. Stall Detected (no progress 30+ min)
        5. Phase Exit (signal via explicit API)
        6. Explicit Milestone (signal via explicit API)

        Args:
            session_id: Session identifier
            max_context_tokens: Max context size (default 200k)

        Returns:
            SplitTriggerEvent if triggered, None otherwise
        """
        if session_id not in self.session_metrics or session_id not in self.active_sessions:
            return None

        metrics = self.session_metrics[session_id]
        metadata = self.active_sessions[session_id]
        now = datetime.utcnow()

        # Trigger 3: Token Burn (≥95% of daily budget)
        if metrics.token_budget_used >= self.TOKEN_BURN_THRESHOLD:
            event = self._create_split_event(
                SessionSplitTrigger.TOKEN_BURN,
                metadata,
                now,
                reason=f"Token budget {metrics.token_budget_used:.1%} >= {self.TOKEN_BURN_THRESHOLD:.1%} threshold",
                metadata_dict={"token_budget_used": metrics.token_budget_used},
            )
            self._audit_log_event(event)
            return event

        # Trigger 2: Context Limit (≥85% of max)
        context_fraction = metrics.context_size_tokens / max_context_tokens
        if context_fraction >= self.CONTEXT_LIMIT_THRESHOLD:
            event = self._create_split_event(
                SessionSplitTrigger.CONTEXT_LIMIT,
                metadata,
                now,
                reason=f"Context {metrics.context_size_tokens} tokens / {max_context_tokens} >= {self.CONTEXT_LIMIT_THRESHOLD:.1%}",
                metadata_dict={
                    "context_tokens": metrics.context_size_tokens,
                    "max_context_tokens": max_context_tokens,
                    "fraction": context_fraction,
                },
            )
            self._audit_log_event(event)
            return event

        # Trigger 5: Iteration Cap (≥50 iterations)
        if metrics.iterations >= self.ITERATION_CAP:
            event = self._create_split_event(
                SessionSplitTrigger.ITERATION_CAP,
                metadata,
                now,
                reason=f"Iteration count {metrics.iterations} >= {self.ITERATION_CAP}",
                metadata_dict={"iterations": metrics.iterations},
            )
            self._audit_log_event(event)
            return event

        # Trigger 6: Stall Detected (no progress ≥30 min)
        stall_minutes = (now - metrics.last_progress_at).total_seconds() / 60
        if stall_minutes >= self.STALL_DETECTION_MINUTES:
            event = self._create_split_event(
                SessionSplitTrigger.STALL_DETECTED,
                metadata,
                now,
                reason=f"No progress for {stall_minutes:.0f} minutes >= {self.STALL_DETECTION_MINUTES} min threshold",
                metadata_dict={
                    "stall_minutes": stall_minutes,
                    "last_progress_at": metrics.last_progress_at.isoformat(),
                },
            )
            self._audit_log_event(event)
            return event

        return None

    def signal_phase_exit(self, session_id: str) -> Optional[SplitTriggerEvent]:
        """Signal explicit phase exit trigger.

        Args:
            session_id: Session identifier

        Returns:
            SplitTriggerEvent, or None if session not found
        """
        if session_id not in self.active_sessions:
            return None

        metadata = self.active_sessions[session_id]
        now = datetime.utcnow()

        event = self._create_split_event(
            SessionSplitTrigger.PHASE_EXIT,
            metadata,
            now,
            reason="Explicit phase exit signal from task controller",
        )
        self._audit_log_event(event)
        return event

    def signal_milestone(
        self, session_id: str, milestone_name: str, auto_split: bool = False
    ) -> Optional[SplitTriggerEvent]:
        """Signal explicit milestone reached (optional split).

        Args:
            session_id: Session identifier
            milestone_name: Name of the milestone
            auto_split: If True, triggers split; else just records milestone

        Returns:
            SplitTriggerEvent if auto_split=True, None otherwise
        """
        if session_id not in self.active_sessions:
            return None

        if not auto_split:
            logger.info(f"Session {session_id}: milestone '{milestone_name}'")
            return None

        metadata = self.active_sessions[session_id]
        now = datetime.utcnow()

        event = self._create_split_event(
            SessionSplitTrigger.EXPLICIT_MILESTONE,
            metadata,
            now,
            reason=f"Milestone '{milestone_name}' reached with auto_split enabled",
            metadata_dict={"milestone_name": milestone_name},
        )
        self._audit_log_event(event)
        return event

    def _create_split_event(
        self,
        trigger_type: SessionSplitTrigger,
        metadata: SessionMetadata,
        now: datetime,
        reason: str = "",
        metadata_dict: Optional[dict] = None,
    ) -> SplitTriggerEvent:
        """Create a split trigger event (internal).

        Args:
            trigger_type: Type of split trigger
            metadata: Session metadata
            now: Current timestamp
            reason: Human-readable reason
            metadata_dict: Additional metadata

        Returns:
            SplitTriggerEvent
        """
        return SplitTriggerEvent(
            trigger_type=trigger_type,
            session_id=metadata.session_id,
            task_id=metadata.task_id,
            phase=metadata.phase,
            tenant_id=metadata.tenant_id,
            timestamp_utc=now,
            reason=reason,
            metadata=metadata_dict or {},
        )

    def _audit_log_event(self, event: SplitTriggerEvent) -> None:
        """Log a split trigger event to audit trail (GDPR Art. 30, 32).

        Args:
            event: SplitTriggerEvent to audit
        """
        audit_dict = event.to_audit_event()
        logger.info(f"AUDIT: {audit_dict}")

        # Publish via hub if available
        if self.hub:
            try:
                self.hub.publish_event("session.split_trigger", audit_dict)
            except Exception as e:
                logger.error(f"Failed to publish split trigger event: {e}")

    def create_checkpoint_for_split(
        self,
        session_id: str,
        split_event: SplitTriggerEvent,
        checkpoint_manager: Optional[Any] = None,
        workflow_executor: Optional[Any] = None,
        goal: str = "",
        goal_alignment_score: float = 0.0,
    ) -> Optional[Any]:
        """Convenience method: create checkpoint when split is detected (k=4 Session Manager Wiring).

        Integrates with WorkflowExecutor to capture workflow state and goal alignment.
        Preserves goal and alignment score across session splits.

        Args:
            session_id: Current session ID
            split_event: SplitTriggerEvent that triggered the checkpoint
            checkpoint_manager: CheckpointManager instance
            workflow_executor: WorkflowExecutor instance (optional, for workflow state capture)
            goal: Current goal being pursued (k=4 Session Drift Validation)
            goal_alignment_score: Goal alignment score at checkpoint time (0.0-1.0) (k=4)

        Returns:
            SessionCheckpoint if created, None otherwise
        """
        if not checkpoint_manager:
            logger.warning("checkpoint_manager not provided; skipping checkpoint creation")
            return None

        if session_id not in self.session_metrics:
            logger.warning(f"Session {session_id} not found in metrics")
            return None

        metrics = self.session_metrics[session_id]

        # Capture workflow state if executor is provided
        workflow_state = None
        if workflow_executor and hasattr(workflow_executor, "execution_state"):
            workflow_state = workflow_executor.execution_state

        checkpoint = checkpoint_manager.create_checkpoint(
            session_id=session_id,
            task_id=split_event.task_id,
            phase=split_event.phase,
            tenant_id=split_event.tenant_id,
            trigger_type=split_event.trigger_type.value,
            iterations=metrics.iterations,
            token_count=metrics.context_size_tokens,
            workflow_execution_state=workflow_state,
            goal=goal,
            goal_alignment_score=goal_alignment_score,
        )

        logger.info(
            f"Created checkpoint {checkpoint.checkpoint_id} for split: "
            f"session={session_id}, trigger={split_event.trigger_type.value}, "
            f"goal_alignment={goal_alignment_score:.2f}"
        )

        return checkpoint

    def close_session(self, session_id: str) -> None:
        """Close a session.

        Args:
            session_id: Session identifier
        """
        if session_id in self.active_sessions:
            metadata = self.active_sessions[session_id]
            logger.info(
                f"Closed session {session_id} for task {metadata.task_id} phase {metadata.phase}"
            )
            del self.active_sessions[session_id]

        if session_id in self.session_metrics:
            del self.session_metrics[session_id]

    def restore_session_from_checkpoint(
        self,
        checkpoint: Any,
        goal_alignment_monitor: Optional[Any] = None,
    ) -> Optional[str]:
        """Restore a session from a checkpoint (k=4 Session Manager Wiring).

        Recreates session metadata and metrics from checkpoint.
        Optionally restores goal alignment state.

        Args:
            checkpoint: SessionCheckpoint to restore from
            goal_alignment_monitor: Optional GoalAlignmentMonitor to restore state

        Returns:
            New session_id if restored, None if restoration failed
        """
        try:
            # Recreate session metadata
            new_session = self.create_session(
                task_id=checkpoint.task_id,
                phase=checkpoint.phase,
                tenant_id=checkpoint.tenant_id,
                parent_session_id=checkpoint.session_id,  # Link to previous session
            )

            # Restore metrics
            if new_session.session_id in self.session_metrics:
                metrics = self.session_metrics[new_session.session_id]
                metrics.iterations = checkpoint.iterations_at_checkpoint
                metrics.context_size_tokens = checkpoint.token_count_at_checkpoint

            # Restore goal alignment state if monitor provided
            if goal_alignment_monitor and checkpoint.goal:
                goal_alignment_monitor.set_goal(
                    new_session.session_id,
                    checkpoint.task_id,
                    checkpoint.tenant_id,
                    checkpoint.goal,
                )
                logger.info(
                    f"Restored goal alignment state for new session {new_session.session_id}: "
                    f"goal='{checkpoint.goal[:100]}...', alignment_score={checkpoint.goal_alignment_score:.2f}"
                )

            logger.info(
                f"Restored session {new_session.session_id} from checkpoint {checkpoint.checkpoint_id} "
                f"(parent: {checkpoint.session_id})"
            )

            return new_session.session_id

        except Exception as e:
            logger.error(f"Failed to restore session from checkpoint {checkpoint.checkpoint_id}: {e}")
            return None

    # ========================================================================
    # Event Handlers (subscribe to Hub events)
    # ========================================================================

    async def _on_task_started(self, event_name: str, event_data: dict) -> None:
        """Handle task.started event from hub."""
        logger.debug(f"Task started: {event_data.get('task_id')}")

    async def _on_iteration_completed(self, event_name: str, event_data: dict) -> None:
        """Handle task.iteration_completed event from hub."""
        session_id = event_data.get("session_id")
        if session_id:
            self.record_iteration(session_id)

    async def _on_milestone_reached(self, event_name: str, event_data: dict) -> None:
        """Handle task.milestone_reached event from hub."""
        session_id = event_data.get("session_id")
        milestone_name = event_data.get("milestone_name")
        auto_split = event_data.get("auto_split", False)
        if session_id:
            self.signal_milestone(session_id, milestone_name or "unnamed", auto_split)

    async def _on_stall_detected(self, event_name: str, event_data: dict) -> None:
        """Handle health.stall_detected event from hub."""
        session_id = event_data.get("session_id")
        if session_id:
            logger.debug(f"Stall detected in session {session_id}")
            # Metrics will show stall on next check_split_triggers()
