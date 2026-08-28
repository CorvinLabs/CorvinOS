"""Phase 3.1: Universal Task Status Model + Bridge-Agnostic Publishing.

StatusSnapshot: The single source of truth for task status across all bridges
(Discord, Console, CLI, Chat). Formats itself for each channel automatically.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class TaskState(str, Enum):
    """Task execution state."""
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"

class UserActionType(str, Enum):
    """Type of user input required."""
    DECISION = "decision"
    CONFIRMATION = "confirmation"
    RESOURCE = "resource"
    APPROVAL = "approval"

@dataclass
class UserAction:
    """Explicit signal for when user input is needed."""
    action_type: UserActionType
    prompt: str
    deadline_seconds: Optional[int] = None
    escalation_strategy: str = "escalate_human"  # default_yes, default_no, escalate_human

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.action_type.value,
            "prompt": self.prompt,
            "deadline_seconds": self.deadline_seconds,
            "escalation": self.escalation_strategy
        }

@dataclass
class StatusEvent:
    """Single event in task timeline."""
    timestamp: str
    level: str  # "info", "success", "warning", "error"
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message
        }

@dataclass
class StatusSnapshot:
    """Universal task status (published to all bridges)."""

    # Identity
    task_id: str
    session_id: str
    user_id: str = "default"

    # Current state
    state: TaskState = TaskState.RUNNING
    progress_percent: float = 0.0
    iteration_num: int = 0
    total_iterations: int = 0

    # What's happening now
    current_action: str = "idle"
    latest_message: str = "Task started"

    # Blocking info (if state == awaiting_input | blocked)
    blocking_reason: Optional[str] = None
    user_action_required: Optional[UserAction] = None

    # Breadcrumb trail (last 3 events)
    recent_events: List[StatusEvent] = field(default_factory=list)

    # Next steps
    expected_next_step: str = "Waiting for execution"

    # Checkpoint info
    last_checkpoint_id: Optional[str] = None
    can_resume: bool = False

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "state": self.state.value,
            "progress_percent": self.progress_percent,
            "iteration_num": self.iteration_num,
            "total_iterations": self.total_iterations,
            "current_action": self.current_action,
            "latest_message": self.latest_message,
            "blocking_reason": self.blocking_reason,
            "user_action_required": self.user_action_required.to_dict() if self.user_action_required else None,
            "recent_events": [e.to_dict() for e in self.recent_events],
            "expected_next_step": self.expected_next_step,
            "last_checkpoint_id": self.last_checkpoint_id,
            "can_resume": self.can_resume,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    def to_discord_embed(self) -> Dict[str, Any]:
        """Format for Discord (rich embed with color, fields)."""
        color = {
            TaskState.RUNNING: 0x3498db,  # Blue
            TaskState.COMPLETED: 0x2ecc71,  # Green
            TaskState.FAILED: 0xe74c3c,  # Red
            TaskState.AWAITING_INPUT: 0xf39c12,  # Orange
            TaskState.BLOCKED: 0xe67e22,  # Dark orange
            TaskState.IDLE: 0x95a5a6,  # Gray
            TaskState.QUEUED: 0x9b59b6  # Purple
        }[self.state]

        fields = [
            {"name": "Task ID", "value": self.task_id, "inline": True},
            {"name": "Progress", "value": f"{self.progress_percent:.1f}% ({self.iteration_num}/{self.total_iterations})", "inline": True},
            {"name": "State", "value": self.state.value.upper(), "inline": True},
            {"name": "Current Action", "value": self.current_action, "inline": False},
            {"name": "Latest", "value": self.latest_message, "inline": False},
            {"name": "Next", "value": self.expected_next_step, "inline": False}
        ]

        if self.blocking_reason:
            fields.append({"name": "⚠️ Blocking", "value": self.blocking_reason, "inline": False})

        if self.user_action_required:
            fields.append({"name": "❓ Awaiting Input", "value": self.user_action_required.prompt, "inline": False})

        if self.can_resume and self.last_checkpoint_id:
            fields.append({"name": "💾 Resume", "value": f"`corvin resume {self.task_id}`", "inline": False})

        return {
            "title": f"Task: {self.task_id}",
            "color": color,
            "fields": fields,
            "footer": {"text": f"Session: {self.session_id} | Updated: {self.updated_at}"}
        }

    def to_console_tile(self) -> Dict[str, Any]:
        """Format for Console (task sidebar widget)."""
        return {
            "task_id": self.task_id,
            "state": self.state.value,
            "progress": self.progress_percent,
            "message": self.latest_message,
            "action": self.current_action,
            "awaiting_user": self.user_action_required is not None,
            "checkpoint_id": self.last_checkpoint_id
        }

    def to_cli_summary(self) -> str:
        """Format for CLI (text summary)."""
        status_emoji = {
            TaskState.RUNNING: "▶️",
            TaskState.COMPLETED: "✅",
            TaskState.FAILED: "❌",
            TaskState.AWAITING_INPUT: "⏸️",
            TaskState.BLOCKED: "🚫",
            TaskState.IDLE: "⏱️",
            TaskState.QUEUED: "📋"
        }[self.state]

        lines = [
            f"\n{status_emoji} Task: {self.task_id}",
            f"   State: {self.state.value.upper()} | Progress: {self.progress_percent:.1f}% ({self.iteration_num}/{self.total_iterations})",
            f"   Action: {self.current_action}",
            f"   Message: {self.latest_message}"
        ]

        if self.blocking_reason:
            lines.append(f"   ⚠️  Blocking: {self.blocking_reason}")

        if self.user_action_required:
            lines.append(f"   ❓ Input: {self.user_action_required.prompt}")

        if self.can_resume:
            lines.append(f"   💾 Resume: corvin resume {self.task_id}")

        return "\n".join(lines)

    def to_chat_line(self) -> str:
        """Format for chat (compact inline status)."""
        progress_bar = "▓" * int(self.progress_percent / 10) + "░" * (10 - int(self.progress_percent / 10))
        return (
            f"**Task {self.task_id}** | {self.state.value.upper()} | "
            f"[{progress_bar}] {self.progress_percent:.1f}% | "
            f"{self.current_action}"
        )

class StatusPublisher:
    """Publish StatusSnapshot to all subscribed bridges (Discord, Console, CLI, Chat)."""

    def __init__(self, max_history_per_task: int = 100):
        """
        Args:
            max_history_per_task: Max snapshots to retain per task (prevents unbounded growth)
        """
        self.subscribers: Dict[str, Callable] = {}
        self.history: List[StatusSnapshot] = []
        self.max_history_per_task = max_history_per_task
        self._latest_by_task: Dict[str, StatusSnapshot] = {}  # O(1) lookup for get_latest

    def subscribe(self, bridge: str, callback: Callable):
        """
        Register a bridge to receive status updates.

        Args:
            bridge: name (discord, console, cli, chat)
            callback: async func(bridge_name, formatted_status) → None
        """
        self.subscribers[bridge] = callback
        logger.info(f"StatusPublisher subscribed: {bridge}")

    async def publish(self, snapshot: StatusSnapshot):
        """
        Broadcast StatusSnapshot to all bridges.

        Each bridge receives its preferred format automatically.
        Enforces history retention limits to prevent unbounded memory growth.
        """
        snapshot.updated_at = datetime.now().isoformat()
        self.history.append(snapshot)
        self._latest_by_task[snapshot.task_id] = snapshot

        # Enforce retention: keep only max_history_per_task for each task
        task_snapshots = [s for s in self.history if s.task_id == snapshot.task_id]
        if len(task_snapshots) > self.max_history_per_task:
            # Remove oldest snapshot for this task
            oldest = task_snapshots[0]
            self.history.remove(oldest)
            logger.debug(f"StatusPublisher pruned oldest snapshot for {snapshot.task_id}")

        # Fan-out to all subscribers
        for bridge_name, callback in self.subscribers.items():
            try:
                # Each bridge formats its own way
                if bridge_name == "discord":
                    formatted = snapshot.to_discord_embed()
                elif bridge_name == "console":
                    formatted = snapshot.to_console_tile()
                elif bridge_name == "cli":
                    formatted = snapshot.to_cli_summary()
                else:  # chat, default
                    formatted = snapshot.to_chat_line()

                await callback(bridge_name, formatted)
                logger.debug(f"StatusPublisher published to {bridge_name}")

            except Exception as e:
                logger.error(f"StatusPublisher failed for {bridge_name}: {e}")

    def get_latest(self, task_id: str) -> Optional[StatusSnapshot]:
        """Get latest status for a task (O(1) lookup via index)."""
        return self._latest_by_task.get(task_id)

    def get_history(self, task_id: str, limit: int = 10) -> List[StatusSnapshot]:
        """Get recent status history for a task (limited to recent snapshots)."""
        matching = [s for s in self.history if s.task_id == task_id]
        return matching[-limit:] if matching else []

# Global publisher instance (injected into VibeEngine)
_publisher = StatusPublisher()

def get_publisher() -> StatusPublisher:
    """Get the global status publisher."""
    return _publisher

def set_publisher(publisher: StatusPublisher):
    """Override the global publisher (for testing)."""
    global _publisher
    _publisher = publisher
