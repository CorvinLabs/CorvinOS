"""ExecutionContext v2 — Live task state shared across Brain subsystems.

Manages nested scope hierarchy, decision history, and context field access.
Enables subsystems to query/update state and track execution traces.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from .decision_record import DecisionRecord


@dataclass(frozen=False)
class ContextStackFrame:
    """Single frame in scope stack."""

    level: str  # "task", "worker", "file", "subtask", etc.
    id: str  # unique identifier for this frame
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        """String representation for stack traces."""
        if self.metadata:
            meta_str = " ".join(f"{k}={v}" for k, v in self.metadata.items())
            return f"{self.level}:{self.id} [{meta_str}]"
        return f"{self.level}:{self.id}"


class ContextStack:
    """Nested scope hierarchy for tasks.

    Tracks execution depth and context across nested operations.
    Enables scoped cleanup and hierarchical decision logging.
    """

    def __init__(self):
        self.stack: list[ContextStackFrame] = []

    def push(self, level: str, id: str, **metadata) -> None:
        """Push new scope onto stack."""
        frame = ContextStackFrame(level, id, metadata if metadata else {})
        self.stack.append(frame)

    def pop(self, level: Optional[str] = None) -> Optional[ContextStackFrame]:
        """Pop scope from stack.

        Args:
            level: If provided, verify level matches top of stack before popping.

        Returns:
            The popped frame, or None if stack is empty.

        Raises:
            ValueError: If level is provided and doesn't match top of stack.
        """
        if not self.stack:
            return None

        if level is not None and self.stack[-1].level != level:
            raise ValueError(
                f"Stack level mismatch: expected {level}, got {self.stack[-1].level}"
            )

        return self.stack.pop()

    @property
    def current_scope(self) -> str:
        """Get current scope string for context."""
        if self.stack:
            return self.stack[-1].id
        return "root"

    @property
    def depth(self) -> int:
        """Get current stack depth."""
        return len(self.stack)

    def __str__(self) -> str:
        """String representation of entire stack."""
        if not self.stack:
            return "root"
        return " → ".join(f.id for f in self.stack)

    def __repr__(self) -> str:
        """Detailed representation including metadata."""
        if not self.stack:
            return "ContextStack(root)"
        frames_str = " → ".join(str(f) for f in self.stack)
        return f"ContextStack({frames_str})"


@dataclass
class ExecutionContext:
    """Live task state shared across Brain subsystems.

    Tracks execution progress, decisions, budget, and guidance overrides.
    Must be mutable for subsystems to update state; is NOT thread-safe.
    """

    task_id: str
    tenant_id: str
    task_template: dict
    context_stack: ContextStack
    decision_history: list[DecisionRecord] = field(default_factory=list)
    budget_remaining: float = 0.0
    time_remaining: int = 0  # seconds
    model: str = ""
    strategy: str = ""
    strategy_confidence: float = 0.5  # 0.0–1.0
    guidance_overrides: dict = field(default_factory=dict)
    checkpoints: list[dict] = field(default_factory=list)

    def get_field(self, key: str) -> Any:
        """Query context field by name.

        Args:
            key: Field name (e.g., 'budget_remaining', 'model').

        Returns:
            Field value, or None if not found.
        """
        return getattr(self, key, None)

    def set_field(self, key: str, value: Any) -> None:
        """Update context field by name.

        Args:
            key: Field name to update.
            value: New value for field.

        Raises:
            AttributeError: If field doesn't exist on ExecutionContext.
        """
        if not hasattr(self, key):
            raise AttributeError(f"ExecutionContext has no field '{key}'")
        setattr(self, key, value)

    def record_decision(
        self,
        subsystem: str,
        decision_type: str,
        value: str,
        reasoning: str = "",
        confidence: float = 0.5,
        guidance_applied: bool = False,
    ) -> DecisionRecord:
        """Record a decision in audit history.

        Args:
            subsystem: Name of subsystem making the decision.
            decision_type: Type of decision (e.g., 'strategy_selection').
            value: The decision value.
            reasoning: Justification for the decision.
            confidence: Confidence level (0.0–1.0).
            guidance_applied: Whether guidance influenced this decision.

        Returns:
            The created DecisionRecord.
        """
        record = DecisionRecord(
            timestamp=DecisionRecord.now_iso(),
            subsystem=subsystem,
            decision_type=decision_type,
            value=value,
            reasoning=reasoning,
            context_stack=str(self.context_stack),
            confidence=confidence,
            guidance_applied=guidance_applied,
        )
        self.decision_history.append(record)
        return record

    def checkpoint(self, name: str, data: dict) -> None:
        """Create a checkpoint for recovery/analysis.

        Args:
            name: Checkpoint identifier.
            data: Checkpoint data to persist.
        """
        checkpoint = {
            "name": name,
            "timestamp": DecisionRecord.now_iso(),
            "context_stack": str(self.context_stack),
            "data": data,
        }
        self.checkpoints.append(checkpoint)

    def to_dict(self) -> dict:
        """Serialize for audit/persistence.

        Note: decision_history and checkpoints are excluded to keep the
        serialization lightweight; they're persisted separately.
        """
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "context_stack": str(self.context_stack),
            "budget_remaining": self.budget_remaining,
            "time_remaining": self.time_remaining,
            "model": self.model,
            "strategy": self.strategy,
            "strategy_confidence": self.strategy_confidence,
            "decision_history_count": len(self.decision_history),
            "checkpoint_count": len(self.checkpoints),
        }

    def to_full_dict(self) -> dict:
        """Serialize including all decision history and checkpoints.

        Use this for comprehensive audit/logging.
        """
        full_dict = self.to_dict()
        full_dict["decision_history"] = [
            d.to_dict() for d in self.decision_history
        ]
        full_dict["checkpoints"] = self.checkpoints
        return full_dict
