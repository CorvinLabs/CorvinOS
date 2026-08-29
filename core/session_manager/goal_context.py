"""GoalContext: Persistent goal + SHA256 integrity hash (Phase 1: Task Context Drift).

Enables goal persistence across session splits + integrity verification.
Prevents task context drift by validating goal unchanged when resuming.

ADR-0405: GoalContext Persistence
ADR-0407: Task Context Drift Prevention (Master)
GDPR Art. 30, 32: Every goal event (init, checkpoint, restore) is audit-logged.
"""

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoalContext:
    """Immutable goal context with SHA256 integrity hash.

    Attributes:
        goal: The task goal text
        goal_hash: SHA256 hash of goal (for integrity verification)
        created_at: Timestamp when goal was created
    """

    goal: str
    goal_hash: str
    created_at: str  # ISO 8601 timestamp

    @classmethod
    def create(cls, goal: str) -> "GoalContext":
        """Create new GoalContext with SHA256 hash.

        Args:
            goal: The task goal text

        Returns:
            GoalContext with computed hash

        Raises:
            ValueError: If goal is empty or not a string
        """
        if not isinstance(goal, str):
            raise ValueError(f"Goal must be a string, got {type(goal)}")
        if not goal.strip():
            raise ValueError("Goal cannot be empty")

        goal_hash = hashlib.sha256(goal.encode("utf-8")).hexdigest()
        created_at = datetime.utcnow().isoformat() + "Z"

        logger.debug(f"Created GoalContext: hash={goal_hash[:16]}...")
        return cls(goal=goal, goal_hash=goal_hash, created_at=created_at)

    def verify_integrity(self) -> bool:
        """Verify goal hash integrity (GDPR Art. 32).

        Returns:
            True if hash matches goal, False if corrupted

        Raises:
            AssertionError: If hash does not match (fail-closed)
        """
        computed_hash = hashlib.sha256(self.goal.encode("utf-8")).hexdigest()
        if computed_hash != self.goal_hash:
            raise AssertionError(
                f"Goal integrity check failed: expected {self.goal_hash[:16]}..., "
                f"computed {computed_hash[:16]}..."
            )
        return True

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict.

        Returns:
            Dictionary with goal, goal_hash, created_at
        """
        return {
            "goal": self.goal,
            "goal_hash": self.goal_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GoalContext":
        """Reconstruct GoalContext from dict.

        Args:
            data: Dictionary with goal, goal_hash, created_at

        Returns:
            GoalContext instance

        Raises:
            ValueError: If required fields missing
            AssertionError: If hash verification fails
        """
        if not data.get("goal"):
            raise ValueError("goal field is required")
        if not data.get("goal_hash"):
            raise ValueError("goal_hash field is required")
        if not data.get("created_at"):
            raise ValueError("created_at field is required")

        goal_ctx = cls(
            goal=data["goal"],
            goal_hash=data["goal_hash"],
            created_at=data["created_at"],
        )

        # Verify integrity on restoration (GDPR Art. 32)
        goal_ctx.verify_integrity()
        return goal_ctx

    def to_audit_event(self) -> dict:
        """Convert to audit.jsonl format (GDPR Art. 30, 32).

        Returns:
            Dictionary with event_type, goal_hash (never raw goal text)
        """
        return {
            "event_type": "goal_context.created",
            "goal_hash": self.goal_hash,
            "created_at": self.created_at,
        }
