"""TaskContextTracker subsystem for guidance scoping.

Maintains task context stack for nested/parallel task handling.

ADR-0353: Task Context Tracking
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Task execution status."""
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskContext:
    """Immutable task context snapshot."""
    task_id: str
    task_title: str
    status: TaskStatus = TaskStatus.RUNNING
    current_step: int = 0
    total_steps: int = 0
    started_at: datetime = field(default_factory=datetime.utcnow)
    parent_task_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def is_active(self) -> bool:
        """Check if task is currently active."""
        return self.status == TaskStatus.RUNNING

    def progress_pct(self) -> float:
        """Get progress percentage."""
        if self.total_steps == 0:
            return 0.0
        return (self.current_step / self.total_steps) * 100.0


@dataclass
class ContextMetrics:
    """Metrics for context tracking."""
    total_tasks: int = 0
    max_depth: int = 0
    context_switches: int = 0
    safety_gates_triggered: int = 0


class TaskContextTracker:
    """Tracks task context stack for guidance scoping."""

    def __init__(self):
        """Initialize task context tracker."""
        self.context_stack: List[TaskContext] = []
        self.context_history: dict[str, TaskContext] = {}
        self.metrics = ContextMetrics()
        self.name = "task_context_tracker"

    async def push_context(self, context: TaskContext) -> None:
        """Push a task context onto the stack.

        Args:
            context: Task context to push
        """
        self.context_stack.append(context)
        self.context_history[context.task_id] = context
        self.metrics.total_tasks += 1

        # Track max depth
        if len(self.context_stack) > self.metrics.max_depth:
            self.metrics.max_depth = len(self.context_stack)

        logger.debug(
            f"Pushed context: {context.task_title} (depth={len(self.context_stack)})"
        )

    async def pop_context(self) -> Optional[TaskContext]:
        """Pop a task context from the stack.

        Returns:
            Popped context, or None if stack empty
        """
        if not self.context_stack:
            logger.warning("Cannot pop: context stack is empty")
            return None

        context = self.context_stack.pop()
        self.metrics.context_switches += 1
        logger.debug(f"Popped context: {context.task_title}")
        return context

    async def get_current_context(self) -> Optional[TaskContext]:
        """Get the current (top of stack) context.

        Returns:
            Current task context, or None if stack empty
        """
        if not self.context_stack:
            return None
        return self.context_stack[-1]

    async def get_context_for_guidance(self, target_task_id: Optional[str] = None) -> Optional[TaskContext]:
        """Determine which task guidance applies to.

        Args:
            target_task_id: Explicit task ID, or None for current

        Returns:
            Target task context, or None
        """
        if target_task_id:
            # Search stack for task ID
            for context in self.context_stack:
                if context.task_id == target_task_id:
                    return context
            logger.warning(f"Task {target_task_id} not found in context stack")
            return None

        # Default: current task
        return await self.get_current_context()

    async def update_step(self, step_increment: int = 1) -> None:
        """Update current task step counter.

        Args:
            step_increment: Steps to increment by
        """
        if not self.context_stack:
            return

        current = self.context_stack[-1]
        new_context = TaskContext(
            task_id=current.task_id,
            task_title=current.task_title,
            status=current.status,
            current_step=current.current_step + step_increment,
            total_steps=current.total_steps,
            started_at=current.started_at,
            parent_task_id=current.parent_task_id,
            metadata=current.metadata,
        )
        self.context_stack[-1] = new_context

    async def get_metrics(self) -> dict:
        """Return context tracker metrics."""
        return {
            "name": self.name,
            "stack_depth": len(self.context_stack),
            "total_tasks": self.metrics.total_tasks,
            "max_depth": self.metrics.max_depth,
            "context_switches": self.metrics.context_switches,
            "safety_gates_triggered": self.metrics.safety_gates_triggered,
        }


class SafetyValidator:
    """Safety validator for high-risk guidance."""

    HIGH_RISK_KEYWORDS = {"delete", "cancel", "stop", "destroy", "remove", "abort"}
    CONFIRMATION_REQUIRED_RISK_LEVEL = "high"

    def __init__(self, context_tracker: TaskContextTracker):
        """Initialize safety validator.

        Args:
            context_tracker: TaskContextTracker instance
        """
        self.context_tracker = context_tracker
        self.confirmations_pending: dict[str, bool] = {}

    async def validate_guidance(self, guidance_text: str, risk_level: str) -> tuple[bool, Optional[str]]:
        """Validate guidance against safety rules.

        Args:
            guidance_text: Guidance text to validate
            risk_level: Risk level (safe, medium, high)

        Returns:
            (is_safe, reason_if_not)
        """
        # Check for high-risk keywords
        guidance_lower = guidance_text.lower()
        for keyword in self.HIGH_RISK_KEYWORDS:
            if keyword in guidance_lower:
                return (False, f"High-risk keyword detected: {keyword}")

        # Check risk level
        if risk_level == "high":
            return (False, "High-risk guidance requires confirmation")

        return (True, None)

    async def request_confirmation(
        self,
        guidance_text: str,
        subsystem_id: str,
        on_confirmation_received=None,
    ) -> bool:
        """Request user confirmation for high-risk guidance.

        Args:
            guidance_text: Guidance requiring confirmation
            subsystem_id: Subsystem requesting confirmation
            on_confirmation_received: Callback when user confirms/denies

        Returns:
            True if confirmed (eventually), False if denied/timeout
        """
        confirmation_id = f"{subsystem_id}_{guidance_text[:20]}"
        self.confirmations_pending[confirmation_id] = False

        logger.warning(f"HIGH-RISK guidance requires confirmation: {guidance_text}")

        # In production, this would trigger a voice question via VoiceChannelCoordinator
        # For now, return False (requires confirmation)
        return False

    async def get_metrics(self) -> dict:
        """Return safety validator metrics."""
        return {
            "confirmations_pending": len(self.confirmations_pending),
            "confirmed": sum(1 for v in self.confirmations_pending.values() if v),
            "denied": sum(1 for v in self.confirmations_pending.values() if not v),
        }
