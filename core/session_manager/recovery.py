"""RecoveryEngine: 4 recovery patterns for autonomous error recovery.

k=4: RecoveryEngine with recovery patterns
- Replay: Restart same strategy (timeout recovery)
- Adapt: Restart with different strategy (strategy failure)
- Backtrack: Restore earlier checkpoint (validation error)
- Pause → Resume: Checkpoint and wait (quota exceeded)

ADR-0XXX: Session Manager Architecture
Integrates with: LearningEngine, LoopEngineer, CheckpointManager
GDPR Art. 30, 32: Recovery actions are audit-logged.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class RecoveryPattern(str, Enum):
    """4 Canonical recovery patterns."""

    REPLAY = "replay"  # Restart same strategy
    ADAPT = "adapt"  # Restart different strategy
    BACKTRACK = "backtrack"  # Restore earlier checkpoint
    PAUSE = "pause"  # Checkpoint and wait


class RecoveryErrorType(str, Enum):
    """Error types triggering recovery."""

    TIMEOUT = "timeout"
    STRATEGY_FAILED = "strategy_failed"
    VALIDATION_ERROR = "validation_error"
    QUOTA_EXCEEDED = "quota_exceeded"
    INCONSISTENCY = "inconsistency"


@dataclass(frozen=True)
class RecoveryAction:
    """Immutable record of a recovery action."""

    action_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    task_id: str = ""
    tenant_id: str = ""
    error_type: RecoveryErrorType = RecoveryErrorType.TIMEOUT
    recovery_pattern: RecoveryPattern = RecoveryPattern.REPLAY
    timestamp_utc: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""
    source_checkpoint_id: Optional[str] = None
    target_checkpoint_id: Optional[str] = None
    attempt_count: int = 0
    success: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate recovery action."""
        if not self.session_id:
            raise ValueError("session_id is required")
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")

    def to_audit_event(self) -> Dict[str, Any]:
        """Convert to audit.jsonl format (GDPR Art. 30, 32)."""
        return {
            "event_type": f"session.recovery_action.{self.recovery_pattern.value}",
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "error_type": self.error_type.value,
            "recovery_pattern": self.recovery_pattern.value,
            "timestamp": self.timestamp_utc.isoformat() + "Z",
            "action_id": self.action_id,
            "reason": self.reason,
            "source_checkpoint_id": self.source_checkpoint_id,
            "target_checkpoint_id": self.target_checkpoint_id,
            "attempt_count": self.attempt_count,
            "success": self.success,
            "metadata": self.metadata,
        }


class RecoveryEngine:
    """Autonomous error recovery via 4 recovery patterns.

    Patterns:
    1. Replay: Restart same strategy (idempotent) — for timeouts
    2. Adapt: Restart with different strategy — for strategy failures
    3. Backtrack: Restore earlier checkpoint, fix root — for validation errors
    4. Pause → Resume: Checkpoint, wait, resume — for quota exceeded

    Integrates with:
    - CheckpointManager: Store/restore checkpoints
    - LearningEngine: Recommend alternatives
    - LoopEngineer: Execute retry strategies
    """

    # Tunable thresholds
    MAX_REPLAY_ATTEMPTS = 3
    MAX_ADAPT_ATTEMPTS = 2
    MAX_BACKTRACK_ATTEMPTS = 2

    def __init__(self, hub: Optional[Any] = None):
        """Initialize RecoveryEngine.

        Args:
            hub: Optional SubsystemHub for event publishing and subsystem access.
        """
        self.name = "recovery_engine"
        self.version = "0.1.0"
        self.hub = hub
        self.recovery_actions: Dict[str, RecoveryAction] = {}  # action_id -> action
        self.session_recovery_history: Dict[str, List[str]] = {}  # session_id -> [action_id, ...]

    def startup(self, hub: Any) -> None:
        """Register with SubsystemHub.

        Args:
            hub: SubsystemHub instance
        """
        self.hub = hub
        logger.info(f"Starting {self.name} v{self.version}")

    def shutdown(self) -> None:
        """Clean up on shutdown."""
        logger.info(f"Shutting down {self.name}")
        self.recovery_actions.clear()
        self.session_recovery_history.clear()

    def initiate_recovery(
        self,
        session_id: str,
        task_id: str,
        tenant_id: str,
        error_type: RecoveryErrorType,
        reason: str = "",
        source_checkpoint_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RecoveryAction:
        """Initiate recovery from an error condition.

        Selects appropriate recovery pattern based on error type.

        Args:
            session_id: Session identifier
            task_id: Task identifier
            tenant_id: Tenant identifier (GDPR Art. 5)
            error_type: Type of error requiring recovery
            reason: Human-readable reason
            source_checkpoint_id: Checkpoint before error (if applicable)
            metadata: Additional metadata

        Returns:
            RecoveryAction describing the recovery
        """
        metadata = metadata or {}

        # Select recovery pattern based on error type
        if error_type == RecoveryErrorType.TIMEOUT:
            pattern = RecoveryPattern.REPLAY
            reason = reason or "Timeout detected, retrying with same strategy"

        elif error_type == RecoveryErrorType.STRATEGY_FAILED:
            pattern = RecoveryPattern.ADAPT
            reason = reason or "Strategy failed, adapting to alternative approach"

        elif error_type == RecoveryErrorType.VALIDATION_ERROR:
            pattern = RecoveryPattern.BACKTRACK
            reason = reason or "Validation error, backtracking to earlier state"

        elif error_type == RecoveryErrorType.QUOTA_EXCEEDED:
            pattern = RecoveryPattern.PAUSE
            reason = reason or "Quota exceeded, pausing and will resume"

        else:  # INCONSISTENCY or unknown
            pattern = RecoveryPattern.ADAPT
            reason = reason or "Inconsistency detected, attempting alternative approach"

        # Create recovery action
        action = RecoveryAction(
            session_id=session_id,
            task_id=task_id,
            tenant_id=tenant_id,
            error_type=error_type,
            recovery_pattern=pattern,
            reason=reason,
            source_checkpoint_id=source_checkpoint_id,
            metadata=metadata,
        )

        # Store action
        self.recovery_actions[action.action_id] = action

        # Track in session history
        if session_id not in self.session_recovery_history:
            self.session_recovery_history[session_id] = []
        self.session_recovery_history[session_id].append(action.action_id)

        logger.info(
            f"Initiated {pattern.value} recovery for session {session_id}: {reason}"
        )

        # Audit log
        self._audit_log_action(action)

        return action

    def execute_replay(
        self,
        action: RecoveryAction,
        current_attempt: int = 1,
    ) -> Dict[str, Any]:
        """Execute Replay recovery pattern.

        Restart same strategy (idempotent). Suitable for timeout recovery.

        Args:
            action: RecoveryAction with recovery_pattern=REPLAY
            current_attempt: Which attempt this is (1, 2, 3...)

        Returns:
            Dict with execution result
        """
        if action.recovery_pattern != RecoveryPattern.REPLAY:
            raise ValueError("Action must have recovery_pattern=REPLAY")

        if current_attempt > self.MAX_REPLAY_ATTEMPTS:
            return {
                "success": False,
                "reason": f"Max replay attempts ({self.MAX_REPLAY_ATTEMPTS}) exceeded",
            }

        logger.info(
            f"Executing REPLAY recovery: session {action.session_id}, "
            f"attempt {current_attempt}/{self.MAX_REPLAY_ATTEMPTS}"
        )

        # In production, this would:
        # 1. Retrieve session state from checkpoint
        # 2. Restart the same strategy
        # 3. Return updated session state

        return {
            "success": True,
            "pattern": RecoveryPattern.REPLAY.value,
            "attempt": current_attempt,
            "action_id": action.action_id,
            "instruction": "Restart the same strategy. Session state restored from checkpoint.",
        }

    def execute_adapt(
        self,
        action: RecoveryAction,
        alternative_strategies: Optional[List[str]] = None,
        current_attempt: int = 1,
    ) -> Dict[str, Any]:
        """Execute Adapt recovery pattern.

        Try a different strategy. Suitable for strategy failure recovery.

        Args:
            action: RecoveryAction with recovery_pattern=ADAPT
            alternative_strategies: List of alternative strategies to try
            current_attempt: Which attempt this is (1, 2...)

        Returns:
            Dict with execution result
        """
        if action.recovery_pattern != RecoveryPattern.ADAPT:
            raise ValueError("Action must have recovery_pattern=ADAPT")

        if current_attempt > self.MAX_ADAPT_ATTEMPTS:
            return {
                "success": False,
                "reason": f"Max adapt attempts ({self.MAX_ADAPT_ATTEMPTS}) exceeded",
            }

        alternative_strategies = alternative_strategies or ["fallback_strategy"]

        logger.info(
            f"Executing ADAPT recovery: session {action.session_id}, "
            f"attempt {current_attempt}/{self.MAX_ADAPT_ATTEMPTS}"
        )

        # In production, this would:
        # 1. Query LearningEngine for alternative strategies
        # 2. Restore session state from checkpoint
        # 3. Restart with new strategy
        # 4. Monitor for success

        strategy_to_try = (
            alternative_strategies[current_attempt - 1]
            if current_attempt <= len(alternative_strategies)
            else "fallback_strategy"
        )

        return {
            "success": True,
            "pattern": RecoveryPattern.ADAPT.value,
            "attempt": current_attempt,
            "action_id": action.action_id,
            "strategy_to_try": strategy_to_try,
            "instruction": f"Try a different strategy: {strategy_to_try}. "
            "Session state restored from checkpoint.",
        }

    def execute_backtrack(
        self,
        action: RecoveryAction,
        target_checkpoint_id: Optional[str] = None,
        current_attempt: int = 1,
    ) -> Dict[str, Any]:
        """Execute Backtrack recovery pattern.

        Restore earlier checkpoint and fix root cause. Suitable for validation errors.

        Args:
            action: RecoveryAction with recovery_pattern=BACKTRACK
            target_checkpoint_id: Checkpoint to restore (if None, uses source)
            current_attempt: Which attempt this is (1, 2...)

        Returns:
            Dict with execution result
        """
        if action.recovery_pattern != RecoveryPattern.BACKTRACK:
            raise ValueError("Action must have recovery_pattern=BACKTRACK")

        if current_attempt > self.MAX_BACKTRACK_ATTEMPTS:
            return {
                "success": False,
                "reason": f"Max backtrack attempts ({self.MAX_BACKTRACK_ATTEMPTS}) exceeded",
            }

        checkpoint_to_restore = target_checkpoint_id or action.source_checkpoint_id
        if not checkpoint_to_restore:
            return {
                "success": False,
                "reason": "No checkpoint to restore",
            }

        logger.info(
            f"Executing BACKTRACK recovery: session {action.session_id}, "
            f"restoring checkpoint {checkpoint_to_restore}, "
            f"attempt {current_attempt}/{self.MAX_BACKTRACK_ATTEMPTS}"
        )

        # In production, this would:
        # 1. Retrieve checkpoint from CheckpointManager
        # 2. Restore complete session state
        # 3. Identify and fix root cause
        # 4. Resume from restored state

        return {
            "success": True,
            "pattern": RecoveryPattern.BACKTRACK.value,
            "attempt": current_attempt,
            "action_id": action.action_id,
            "checkpoint_restored": checkpoint_to_restore,
            "instruction": f"Session state restored to checkpoint {checkpoint_to_restore}. "
            "Identify and fix the root cause before proceeding.",
        }

    def execute_pause(
        self,
        action: RecoveryAction,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Execute Pause recovery pattern.

        Checkpoint session and pause until quota available. Suitable for quota exceeded.

        Args:
            action: RecoveryAction with recovery_pattern=PAUSE
            reason: Reason for pause (e.g., "Daily quota exceeded")

        Returns:
            Dict with execution result
        """
        if action.recovery_pattern != RecoveryPattern.PAUSE:
            raise ValueError("Action must have recovery_pattern=PAUSE")

        pause_reason = reason or action.reason or "Quota exceeded"

        logger.info(
            f"Executing PAUSE recovery: session {action.session_id}, "
            f"reason: {pause_reason}"
        )

        # In production, this would:
        # 1. Create checkpoint of current session state
        # 2. Mark session as paused
        # 3. Wait for quota reset (e.g., midnight UTC)
        # 4. Resume from checkpoint when quota available

        return {
            "success": True,
            "pattern": RecoveryPattern.PAUSE.value,
            "action_id": action.action_id,
            "paused_reason": pause_reason,
            "instruction": f"Session paused: {pause_reason}. "
            "Will resume from checkpoint when quota available.",
        }

    def mark_recovery_success(
        self,
        action_id: str,
        target_checkpoint_id: Optional[str] = None,
    ) -> Optional[RecoveryAction]:
        """Mark a recovery action as successful.

        Args:
            action_id: RecoveryAction identifier
            target_checkpoint_id: Checkpoint created during recovery (if applicable)

        Returns:
            Updated RecoveryAction, or None if not found
        """
        if action_id not in self.recovery_actions:
            return None

        action = self.recovery_actions[action_id]

        # Create updated action with success=True
        updated_action = RecoveryAction(
            action_id=action.action_id,
            session_id=action.session_id,
            task_id=action.task_id,
            tenant_id=action.tenant_id,
            error_type=action.error_type,
            recovery_pattern=action.recovery_pattern,
            timestamp_utc=action.timestamp_utc,
            reason=action.reason,
            source_checkpoint_id=action.source_checkpoint_id,
            target_checkpoint_id=target_checkpoint_id,
            attempt_count=action.attempt_count + 1,
            success=True,
            metadata=action.metadata,
        )

        self.recovery_actions[action_id] = updated_action
        logger.info(f"Marked recovery {action_id} as successful")

        return updated_action

    def get_recovery_history(self, session_id: str) -> List[RecoveryAction]:
        """Get recovery history for a session.

        Args:
            session_id: Session identifier

        Returns:
            List of RecoveryActions for this session (in chronological order)
        """
        if session_id not in self.session_recovery_history:
            return []

        actions = []
        for action_id in self.session_recovery_history[session_id]:
            action = self.recovery_actions.get(action_id)
            if action:
                actions.append(action)

        return actions

    def recovery_success_rate(self, session_id: str) -> float:
        """Calculate recovery success rate for a session.

        Args:
            session_id: Session identifier

        Returns:
            Fraction of successful recoveries [0.0-1.0]
        """
        history = self.get_recovery_history(session_id)
        if not history:
            return 0.0

        successful = sum(1 for action in history if action.success)
        return successful / len(history)

    def _audit_log_action(self, action: RecoveryAction) -> None:
        """Log recovery action to audit trail (GDPR Art. 30, 32).

        Args:
            action: RecoveryAction to audit
        """
        audit_dict = action.to_audit_event()
        logger.info(f"AUDIT: {audit_dict}")

        # Publish via hub if available
        if self.hub:
            try:
                self.hub.publish_event("session.recovery_action", audit_dict)
            except Exception as e:
                logger.error(f"Failed to publish recovery action event: {e}")
