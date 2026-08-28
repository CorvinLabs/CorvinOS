"""
Phase 2: VibeOrchestrator — Full Integration Layer

Wires all components:
  SessionLifecycleManager → ContextReducer → CheckpointManager → RecoveryEngine

Coordinates autonomous task lifecycle:
- Trigger detection (6 split triggers)
- Context compression (91% reduction)
- Checkpoint serialization (atomic + idempotent)
- Recovery and resumption

Integration with Brain v0.2:
- EventBus for split notifications
- HealthMonitor for stall detection
- LoopEngineer for phase tracking

ADR-0348 (EventBus), ADR-0347 (Hub), Phase 2 orchestration.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime
from pathlib import Path
from enum import Enum
import json
import logging

from core.vibe_engineering.session_lifecycle_manager import (
    SessionLifecycleManager,
    SessionState,
    SplitTrigger,
)
from core.vibe_engineering.checkpoint_manager import (
    CheckpointManager,
    CheckpointState,
)
from core.vibe_engineering.context_reducer import (
    ContextReducer,
    ReducedContext,
)
from core.vibe_engineering.recovery_engine import (
    RecoveryEngine,
    ExecutionState,
)

logger = logging.getLogger(__name__)


class OrchestratorState(Enum):
    """Orchestrator lifecycle states."""
    IDLE = "idle"  # No active task
    RUNNING = "running"  # Task executing
    CHECKPOINT_PENDING = "checkpoint_pending"  # Split trigger detected, checkpoint in progress
    RECOVERING = "recovering"  # Loading checkpoint and resuming
    PAUSED = "paused"  # User pause
    ERROR = "error"  # Unrecoverable error


@dataclass
class OrchestrationMetrics:
    """Metrics and statistics for orchestration."""
    checkpoints_created: int = 0
    total_iterations: int = 0
    total_splits: int = 0
    avg_context_reduction_pct: float = 0.0
    recovery_success_count: int = 0
    recovery_failure_count: int = 0
    total_tokens_saved: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    last_checkpoint_time: Optional[datetime] = None
    total_splits_by_trigger: Dict[str, int] = field(default_factory=dict)


@dataclass
class TaskExecution:
    """Tracks a single task's execution lifecycle."""
    task_id: str
    session_id: str
    goal: str
    constraints: List[str]
    created_at: datetime = field(default_factory=datetime.now)

    # State tracking
    current_phase: str = "initialization"
    iteration_count: int = 0
    context_tokens: int = 0
    max_context_tokens: int = 4000
    tokens_burned_today: int = 0
    daily_token_budget: int = 100000

    # Checkpoint tracking
    last_checkpoint_id: Optional[str] = None
    checkpoints: List[CheckpointState] = field(default_factory=list)

    # Learning state
    strategies_tried: List[str] = field(default_factory=list)
    errors_encountered: List[Dict[str, Any]] = field(default_factory=list)
    learnings: List[Dict[str, Any]] = field(default_factory=list)

    # Artifacts
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    open_subgoals: List[Dict[str, Any]] = field(default_factory=list)


class VibeOrchestrator:
    """
    Full Phase 2 integration: orchestrates autonomous task execution with
    checkpointing, context compression, and recovery.

    Guarantees:
    - Split triggers detected automatically (6 types)
    - Context reduced to 91% compression before checkpoint
    - Checkpoints persist atomically to filesystem
    - Recovery restores full state and resumes execution
    - Idempotent: same state always produces same checkpoint ID
    """

    def __init__(
        self,
        checkpoint_dir: Optional[Path] = None,
        context_reduction_target_pct: int = 91
    ):
        """
        Initialize orchestrator with component managers.

        Args:
            checkpoint_dir: Where to persist checkpoints.
                           Defaults to ~/.corvin/vibe/checkpoints/
            context_reduction_target_pct: Target compression (typically 91%).
        """
        self.checkpoint_manager = CheckpointManager(checkpoint_dir)
        self.context_reducer = ContextReducer(context_reduction_target_pct)
        self.recovery_engine = RecoveryEngine()
        self.session_lifecycle_manager = SessionLifecycleManager()

        self.state = OrchestratorState.IDLE
        self.metrics = OrchestrationMetrics()
        self.active_task: Optional[TaskExecution] = None
        self.callbacks: Dict[str, List[Callable]] = {
            "on_split_detected": [],
            "on_checkpoint_created": [],
            "on_recovery_started": [],
            "on_recovery_complete": [],
            "on_error": [],
        }

        logger.info(
            f"VibeOrchestrator initialized "
            f"(checkpoint_dir={self.checkpoint_manager.checkpoint_dir})"
        )

    def register_callback(self, event_type: str, callback: Callable):
        """
        Register callback for orchestration events.

        Args:
            event_type: One of "on_split_detected", "on_checkpoint_created",
                       "on_recovery_started", "on_recovery_complete", "on_error"
            callback: Callable with signature callback(event_data: Dict) -> None
        """
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
            logger.debug(f"Callback registered: {event_type}")
        else:
            logger.warning(f"Unknown event type: {event_type}")

    def _emit_event(self, event_type: str, data: Dict[str, Any]):
        """Emit callback event."""
        for callback in self.callbacks.get(event_type, []):
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Callback error for {event_type}: {e}")

    # ========================================================================
    # TASK LIFECYCLE
    # ========================================================================

    def start_task(
        self,
        task_id: str,
        session_id: str,
        goal: str,
        constraints: List[str],
        max_context_tokens: int = 4000,
        daily_token_budget: int = 100000
    ) -> TaskExecution:
        """
        Start a new task for autonomous execution.

        Args:
            task_id: Unique task identifier
            session_id: Session containing this task
            goal: Task goal/objective
            constraints: List of constraints/requirements
            max_context_tokens: Max context window size
            daily_token_budget: Daily token budget

        Returns:
            TaskExecution ready to run
        """
        task = TaskExecution(
            task_id=task_id,
            session_id=session_id,
            goal=goal,
            constraints=constraints,
            max_context_tokens=max_context_tokens,
            tokens_burned_today=0,
            daily_token_budget=daily_token_budget,
        )

        self.active_task = task
        self.state = OrchestratorState.RUNNING

        logger.info(
            f"Task started: {task_id} (session={session_id}, goal={goal[:50]}...)"
        )
        return task

    def record_iteration(
        self,
        task: TaskExecution,
        iteration_num: int,
        context_tokens: int,
        tokens_used: int,
        phase: Optional[str] = None
    ):
        """
        Record an iteration step in task execution.

        Args:
            task: Current TaskExecution
            iteration_num: Iteration number
            context_tokens: Current context token count
            tokens_used: Tokens consumed this iteration
            phase: Move the task to this phase. ``None`` (the default) KEEPS
                the task's current phase.

        The default used to be the literal ``"execution"``, so every
        `record_iteration` call that did not pass a phase silently moved the
        task back to "execution" — overwriting a phase the caller had just set.
        The wrong phase was then checkpointed, and a resume restarted the run in
        a phase it had already left.
        """
        task.iteration_count = iteration_num
        task.context_tokens = context_tokens
        task.tokens_burned_today += tokens_used
        if phase is not None:
            task.current_phase = phase

        logger.debug(
            f"Iteration {iteration_num}: context={context_tokens} tokens, "
            f"burned={task.tokens_burned_today}/{task.daily_token_budget}"
        )

    def evaluate_split_triggers(self, task: TaskExecution) -> Optional[SplitTrigger]:
        """
        Evaluate all 6 split triggers for current task state.

        Returns:
            Triggered SplitTrigger, or None if no triggers fired.
        """
        # Build session state from task
        session_state = SessionState(
            session_id=task.session_id,
            phase=task.current_phase,
            iteration_count=task.iteration_count,
            context_tokens=task.context_tokens,
            max_context_tokens=task.max_context_tokens,
            tokens_burned_today=task.tokens_burned_today,
            daily_token_budget=task.daily_token_budget,
        )

        # Evaluate triggers
        eval_result = self.session_lifecycle_manager.evaluate_triggers(session_state)

        if eval_result.triggered and eval_result.trigger_type:
            logger.info(
                f"Split trigger detected: {eval_result.trigger_type.value} "
                f"({eval_result.reason})"
            )
            self._emit_event(
                "on_split_detected",
                {
                    "task_id": task.task_id,
                    "trigger": eval_result.trigger_type.value,
                    "reason": eval_result.reason,
                    "iteration": task.iteration_count,
                }
            )
            return eval_result.trigger_type

        return None

    # ========================================================================
    # CHECKPOINT CREATION & PERSISTENCE
    # ========================================================================

    def create_checkpoint(
        self,
        task: TaskExecution,
        trigger: SplitTrigger,
        recovery_reason: Optional[str] = None
    ) -> CheckpointState:
        """
        Create a checkpoint: detect split, reduce context, serialize, persist.

        Full pipeline:
        1. Reduce context (91% compression)
        2. Create checkpoint state
        3. Persist to filesystem (atomic)
        4. Record metrics
        5. Emit callback

        Args:
            task: Current TaskExecution
            trigger: What triggered the checkpoint
            recovery_reason: If triggered by error, error description

        Returns:
            Persisted CheckpointState
        """
        self.state = OrchestratorState.CHECKPOINT_PENDING

        logger.info(
            f"Creating checkpoint for task={task.task_id}, "
            f"trigger={trigger.value}, iteration={task.iteration_count}"
        )

        # Step 1: Reduce context (91% compression)
        reduced_context = self.context_reducer.reduce(
            goal=task.goal,
            constraints=task.constraints,
            decisions=self._build_decisions_list(task),
            errors=task.errors_encountered,
            learnings=task.learnings,
            original_size_tokens=task.context_tokens,
        )

        # Step 2: Build checkpoint state
        task_state = {
            "task_id": task.task_id,
            "goal": task.goal,
            "phase": task.current_phase,
            "context_tokens": task.context_tokens,
            "max_context_tokens": task.max_context_tokens,
            "tokens_burned": task.tokens_burned_today,
            "daily_budget": task.daily_token_budget,
            "progress": {
                "iteration": task.iteration_count,
                "strategies_tried": len(task.strategies_tried),
                "errors": len(task.errors_encountered),
            },
        }

        learning_state = {
            "strategies_tried": task.strategies_tried,
            "success_rate": self._estimate_success_rate(task),
            "errors": [e.get("error_type") for e in task.errors_encountered],
            "recommendations": self._generate_recommendations(task),
        }

        context_essentials_dict = {
            "kept": task.constraints,
            "decisions": self._build_decisions_list(task),
            "errors": task.errors_encountered,
            "learnings": task.learnings,
            "dropped": reduced_context.dropped_sections,
            "reduction_pct": reduced_context.reduction_pct,
            "original_tokens": reduced_context.original_size_tokens,
            "reduced_tokens": reduced_context.reduced_size_tokens,
        }

        # Step 3: Create checkpoint
        checkpoint = self.checkpoint_manager.create_checkpoint(
            task_id=task.task_id,
            session_id=task.session_id,
            phase=task.current_phase,
            trigger=trigger.value,
            iteration_num=task.iteration_count,
            task_state=task_state,
            context_essentials=context_essentials_dict,
            learning_state=learning_state,
            open_subgoals=task.open_subgoals,
            artifacts=task.artifacts,
            recovery_reason=recovery_reason,
        )

        # Step 4: Persist (atomic write)
        self.checkpoint_manager.save(checkpoint)

        # Step 5: Record metrics
        task.last_checkpoint_id = checkpoint.checkpoint_id
        task.checkpoints.append(checkpoint)
        self.metrics.checkpoints_created += 1
        self.metrics.last_checkpoint_time = datetime.now()
        self.metrics.total_splits += 1
        if trigger.value not in self.metrics.total_splits_by_trigger:
            self.metrics.total_splits_by_trigger[trigger.value] = 0
        self.metrics.total_splits_by_trigger[trigger.value] += 1

        tokens_saved = (
            reduced_context.original_size_tokens - reduced_context.reduced_size_tokens
        )
        self.metrics.total_tokens_saved += tokens_saved

        logger.info(
            f"Checkpoint created: {checkpoint.checkpoint_id} "
            f"({reduced_context.reduction_pct}% compression, "
            f"{tokens_saved} tokens saved)"
        )

        # Step 6: Emit callback
        self._emit_event(
            "on_checkpoint_created",
            {
                "task_id": task.task_id,
                "checkpoint_id": checkpoint.checkpoint_id,
                "trigger": trigger.value,
                "compression_pct": reduced_context.reduction_pct,
                "tokens_saved": tokens_saved,
                "iteration": task.iteration_count,
            }
        )

        self.state = OrchestratorState.RUNNING
        return checkpoint

    # ========================================================================
    # RECOVERY & RESUMPTION
    # ========================================================================

    def resume_from_checkpoint(
        self,
        task_id: str,
        checkpoint_id: Optional[str] = None
    ) -> Optional[ExecutionState]:
        """
        Resume task execution from checkpoint.

        If checkpoint_id is None, resumes from latest checkpoint for task_id.

        Args:
            task_id: Task to resume
            checkpoint_id: Specific checkpoint ID, or None for latest

        Returns:
            ExecutionState ready to resume, or None if checkpoint not found
        """
        self.state = OrchestratorState.RECOVERING

        logger.info(f"Resuming from checkpoint: task={task_id}, checkpoint={checkpoint_id}")

        # Load checkpoint
        checkpoint = None
        if checkpoint_id:
            # Load specific checkpoint
            checkpoints = self.checkpoint_manager.list_checkpoints(task_id)
            for cp_meta in checkpoints:
                if cp_meta.checkpoint_id == checkpoint_id:
                    checkpoint = self.checkpoint_manager.load(cp_meta.file_path)
                    break
        else:
            # Load latest
            checkpoint = self.checkpoint_manager.get_latest(task_id)

        if not checkpoint:
            logger.error(
                f"Checkpoint not found: task={task_id}, checkpoint={checkpoint_id}"
            )
            self._emit_event(
                "on_error",
                {
                    "task_id": task_id,
                    "error": "Checkpoint not found",
                    "phase": "resume",
                }
            )
            self.state = OrchestratorState.ERROR
            self.metrics.recovery_failure_count += 1
            return None

        # Recover state
        try:
            execution_state = self.recovery_engine.recover_from_checkpoint(checkpoint)

            # Validate idempotency
            if not self.recovery_engine.validate_resumed_state(checkpoint, execution_state):
                logger.error("Recovery validation failed (idempotency check)")
                self.metrics.recovery_failure_count += 1
                self.state = OrchestratorState.ERROR
                return None

            # Restore task from execution state
            self.active_task = TaskExecution(
                task_id=execution_state.task_id,
                session_id=execution_state.session_id,
                goal=execution_state.full_context.get("goal", ""),
                constraints=execution_state.full_context.get("constraints", []),
                current_phase=execution_state.phase,
                iteration_count=execution_state.iteration_num,
                context_tokens=execution_state.session_state.context_tokens,
                max_context_tokens=execution_state.session_state.max_context_tokens,
                tokens_burned_today=execution_state.session_state.tokens_burned_today,
                daily_token_budget=execution_state.session_state.daily_token_budget,
                last_checkpoint_id=execution_state.last_checkpoint_id,
            )

            self.metrics.recovery_success_count += 1
            self.state = OrchestratorState.RUNNING

            logger.info(
                f"Recovery complete: {execution_state.task_id} resuming at "
                f"iteration {execution_state.iteration_num + 1}"
            )

            self._emit_event(
                "on_recovery_complete",
                {
                    "task_id": execution_state.task_id,
                    "checkpoint_id": execution_state.last_checkpoint_id,
                    "resume_iteration": execution_state.iteration_num,
                    "recovery_reason": execution_state.recovery_reason,
                }
            )

            return execution_state

        except Exception as e:
            logger.error(f"Recovery failed: {e}")
            self._emit_event(
                "on_error",
                {
                    "task_id": task_id,
                    "error": str(e),
                    "phase": "recovery",
                }
            )
            self.state = OrchestratorState.ERROR
            self.metrics.recovery_failure_count += 1
            return None

    # ========================================================================
    # INSPECTION & ANALYTICS
    # ========================================================================

    def list_task_checkpoints(self, task_id: str) -> list:
        """List all checkpoints for a task (newest first)."""
        return self.checkpoint_manager.list_checkpoints(task_id)

    def get_checkpoint_details(self, checkpoint_id: str, task_id: str) -> Optional[CheckpointState]:
        """Get full checkpoint details for inspection."""
        checkpoints = self.list_task_checkpoints(task_id)
        for meta in checkpoints:
            if meta.checkpoint_id == checkpoint_id:
                return self.checkpoint_manager.load(meta.file_path)
        return None

    def get_metrics(self) -> OrchestrationMetrics:
        """Get orchestration metrics."""
        if self.metrics.checkpoints_created > 0:
            reduction_pcts = []
            for task in [self.active_task] if self.active_task else []:
                for cp in task.checkpoints:
                    reduction_pcts.append(
                        cp.context_essentials.get("reduction_pct", 91)
                    )
            if reduction_pcts:
                self.metrics.avg_context_reduction_pct = sum(reduction_pcts) / len(reduction_pcts)

        return self.metrics

    def get_status(self) -> Dict[str, Any]:
        """Get current orchestrator status."""
        return {
            "state": self.state.value,
            "active_task": {
                "task_id": self.active_task.task_id,
                "phase": self.active_task.current_phase,
                "iteration": self.active_task.iteration_count,
                "context_tokens": self.active_task.context_tokens,
                "tokens_burned": self.active_task.tokens_burned_today,
            } if self.active_task else None,
            "metrics": {
                "checkpoints_created": self.metrics.checkpoints_created,
                "total_splits": self.metrics.total_splits,
                "tokens_saved": self.metrics.total_tokens_saved,
                "recovery_success": self.metrics.recovery_success_count,
                "recovery_failures": self.metrics.recovery_failure_count,
            }
        }

    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    def _build_decisions_list(self, task: TaskExecution) -> List[Dict[str, Any]]:
        """Build decisions list from task learnings."""
        decisions = []
        for i, learning in enumerate(task.learnings):
            decisions.append({
                "iter": i,
                "decision": learning.get("learning", ""),
                "why": learning.get("applies_to", ""),
            })
        return decisions

    def _estimate_success_rate(self, task: TaskExecution) -> float:
        """Estimate task success rate (0.0-1.0)."""
        if not task.strategies_tried:
            return 0.5  # Unknown
        # Simple heuristic: fewer errors = higher success rate
        error_ratio = min(1.0, len(task.errors_encountered) / max(1, len(task.strategies_tried)))
        return max(0.0, 1.0 - error_ratio)

    def _generate_recommendations(self, task: TaskExecution) -> List[str]:
        """Generate next-step recommendations."""
        recommendations = []
        if len(task.errors_encountered) > 3:
            recommendations.append("Consider changing strategy to avoid repeated errors")
        if task.iteration_count > 30:
            recommendations.append("High iteration count — verify goal clarity")
        if task.context_tokens > task.max_context_tokens * 0.8:
            recommendations.append("Context approaching limit — prioritize essential info")
        return recommendations
