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
        original_goal: The original task goal text
        goal_hash: SHA256 hash of goal (for integrity verification)
        created_at: Timestamp when goal was created
        session_id: Session ID where goal was created
        tenant_id: Tenant ID for multi-tenant isolation
        last_validated_at: Timestamp of last validation
        validation_confidence: Confidence score from last validation (0.0-1.0)
    """

    original_goal: str
    goal_hash: str
    created_at: str  # ISO 8601 timestamp
    session_id: str = ""
    tenant_id: str = "default"
    last_validated_at: Optional[str] = None
    validation_confidence: float = 1.0

    @classmethod
    def create(cls, goal: str, session_id: str = "", tenant_id: str = "default") -> "GoalContext":
        """Create new GoalContext with SHA256 hash.

        Args:
            goal: The task goal text
            session_id: Session ID (optional)
            tenant_id: Tenant ID (default: 'default')

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
        return cls(
            original_goal=goal,
            goal_hash=goal_hash,
            created_at=created_at,
            session_id=session_id,
            tenant_id=tenant_id,
            last_validated_at=None,
            validation_confidence=1.0,
        )

    def verify_integrity(self) -> bool:
        """Verify goal hash integrity (GDPR Art. 32).

        Returns:
            True if hash matches goal, False if corrupted

        Raises:
            AssertionError: If hash does not match (fail-closed)
        """
        computed_hash = hashlib.sha256(self.original_goal.encode("utf-8")).hexdigest()
        if computed_hash != self.goal_hash:
            raise AssertionError(
                f"Goal integrity check failed: expected {self.goal_hash[:16]}..., "
                f"computed {computed_hash[:16]}..."
            )
        return True

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict.

        Returns:
            Dictionary with original_goal, goal_hash, created_at, session_id, tenant_id
        """
        return {
            "original_goal": self.original_goal,
            "goal_hash": self.goal_hash,
            "created_at": self.created_at,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "last_validated_at": self.last_validated_at,
            "validation_confidence": self.validation_confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GoalContext":
        """Reconstruct GoalContext from dict.

        Args:
            data: Dictionary with original_goal, goal_hash, created_at

        Returns:
            GoalContext instance

        Raises:
            ValueError: If required fields missing
            AssertionError: If hash verification fails
        """
        if not data.get("original_goal"):
            raise ValueError("original_goal field is required")
        if not data.get("goal_hash"):
            raise ValueError("goal_hash field is required")
        if not data.get("created_at"):
            raise ValueError("created_at field is required")

        goal_ctx = cls(
            original_goal=data["original_goal"],
            goal_hash=data["goal_hash"],
            created_at=data["created_at"],
            session_id=data.get("session_id", ""),
            tenant_id=data.get("tenant_id", "default"),
            last_validated_at=data.get("last_validated_at"),
            validation_confidence=data.get("validation_confidence", 1.0),
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
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
        }
