"""
Interrupt protocol for operator control of running tasks.

Operations: pause, resume, redirect, cancel

State machine:
  RUNNING → PAUSED → RUNNING → COMPLETED
  RUNNING → REDIRECTED (engine change mid-task)
  RUNNING → CANCELLED
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class TaskState(Enum):
    """Task execution state."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    RESUMED = "resumed"
    REDIRECTED = "redirected"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class InterruptCommand:
    """Immutable interrupt command."""
    command_id: str
    task_id: str
    operation: str  # "pause", "resume", "redirect", "cancel"
    timestamp: datetime
    issued_by_operator_id: str
    new_engine: Optional[str] = None  # For redirect
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize."""
        return {
            "command_id": self.command_id,
            "task_id": self.task_id,
            "operation": self.operation,
            "timestamp": self.timestamp.isoformat(),
            "issued_by": self.issued_by_operator_id,
            "new_engine": self.new_engine,
            "reason": self.reason,
        }


class InterruptController:
    """
    Control running tasks via interrupt protocol.

    Supports:
    - PAUSE: Hold task execution (max 5 minutes)
    - RESUME: Continue paused task
    - REDIRECT: Switch to different engine
    - CANCEL: Abort task
    """

    def __init__(self):
        """Initialize controller."""
        self.task_states: Dict[str, TaskState] = {}
        self.active_commands: Dict[str, InterruptCommand] = {}
        self.rate_limit: Dict[str, int] = {}  # task_id -> command count

    def can_issue_command(self, task_id: str, max_per_second: int = 1) -> bool:
        """Rate limit check."""
        count = self.rate_limit.get(task_id, 0)
        return count < max_per_second

    def issue_pause(
        self,
        command_id: str,
        task_id: str,
        operator_id: str,
        reason: str = "",
    ) -> Optional[InterruptCommand]:
        """
        Pause a running task.

        Args:
            command_id: Unique command ID
            task_id: Task to pause
            operator_id: Operator issuing command
            reason: Reason for pause

        Returns:
            InterruptCommand if successful, None if not allowed
        """
        state = self.task_states.get(task_id)
        if state not in [TaskState.RUNNING, None]:
            return None  # Can't pause if not running

        if not self.can_issue_command(task_id):
            return None  # Rate limit

        command = InterruptCommand(
            command_id=command_id,
            task_id=task_id,
            operation="pause",
            timestamp=datetime.utcnow(),
            issued_by_operator_id=operator_id,
            reason=reason,
        )

        self.active_commands[command_id] = command
        self.task_states[task_id] = TaskState.PAUSED
        self.rate_limit[task_id] = self.rate_limit.get(task_id, 0) + 1

        return command

    def issue_resume(
        self,
        command_id: str,
        task_id: str,
        operator_id: str,
    ) -> Optional[InterruptCommand]:
        """Resume paused task."""
        state = self.task_states.get(task_id)
        if state != TaskState.PAUSED:
            return None

        command = InterruptCommand(
            command_id=command_id,
            task_id=task_id,
            operation="resume",
            timestamp=datetime.utcnow(),
            issued_by_operator_id=operator_id,
        )

        self.active_commands[command_id] = command
        self.task_states[task_id] = TaskState.RESUMED

        return command

    def issue_redirect(
        self,
        command_id: str,
        task_id: str,
        operator_id: str,
        new_engine: str,
    ) -> Optional[InterruptCommand]:
        """Redirect to different engine mid-task."""
        state = self.task_states.get(task_id)
        if state not in [TaskState.RUNNING, TaskState.PAUSED]:
            return None

        if new_engine not in ["claude", "local_llama2", "hermes"]:
            return None

        command = InterruptCommand(
            command_id=command_id,
            task_id=task_id,
            operation="redirect",
            timestamp=datetime.utcnow(),
            issued_by_operator_id=operator_id,
            new_engine=new_engine,
            reason=f"Redirected to {new_engine}",
        )

        self.active_commands[command_id] = command
        self.task_states[task_id] = TaskState.REDIRECTED

        return command

    def issue_cancel(
        self,
        command_id: str,
        task_id: str,
        operator_id: str,
        reason: str = "",
    ) -> Optional[InterruptCommand]:
        """Cancel running/paused task."""
        state = self.task_states.get(task_id)
        if state not in [TaskState.RUNNING, TaskState.PAUSED, TaskState.RESUMED]:
            return None

        command = InterruptCommand(
            command_id=command_id,
            task_id=task_id,
            operation="cancel",
            timestamp=datetime.utcnow(),
            issued_by_operator_id=operator_id,
            reason=reason,
        )

        self.active_commands[command_id] = command
        self.task_states[task_id] = TaskState.CANCELLED

        return command

    def get_command(self, command_id: str) -> Optional[InterruptCommand]:
        """Get command by ID."""
        return self.active_commands.get(command_id)

    def get_task_state(self, task_id: str) -> TaskState:
        """Get current task state."""
        return self.task_states.get(task_id, TaskState.PENDING)
