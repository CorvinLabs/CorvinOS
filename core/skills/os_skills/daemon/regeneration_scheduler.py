"""RegenerationScheduler — queues skills for regeneration when weights change."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
from enum import Enum
import uuid


class RegenerationReason(Enum):
    """Why a skill is being regenerated."""
    WEIGHT_CHANGE = "weight_change"  # Loss improved significantly
    NEW_SOURCE = "new_source"  # New data source available
    ERROR_RECOVERY = "error_recovery"  # Skill had too many errors
    PRIORITY_BOOST = "priority_boost"  # Manually prioritized


@dataclass(frozen=True)
class RegenerationQueueItem:
    """Immutable: a skill queued for regeneration."""
    item_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    skill_id: str = ""
    reason: str = "weight_change"  # RegenerationReason.value
    priority: float = 0.5  # 0–1, where 1 is highest
    loss_delta: Optional[float] = None  # How much loss improved
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict = field(default_factory=dict)

    def validate(self) -> bool:
        """Validate item."""
        if not self.skill_id:
            return False
        if not (0 <= self.priority <= 1):
            return False
        return True


class RegenerationScheduler:
    """
    Queues skills for regeneration when weights change significantly.

    Strategy:
    1. Monitor weight updates (from WeightLearner)
    2. If Δloss > threshold (e.g., 0.05), queue skill for regen
    3. Batch updates (don't regen per-update, wait for batch)
    4. Prioritize by influence + loss_delta
    5. Execute regeneations in background (async)

    Prevents thrashing (constant regeneration) while responding quickly to improvements.
    """

    def __init__(self, loss_delta_threshold: float = 0.05, batch_size: int = 5):
        """
        Initialize scheduler.

        Args:
            loss_delta_threshold: Min loss change to trigger regen
            batch_size: How many skills to regen at once
        """
        self.loss_threshold = loss_delta_threshold
        self.batch_size = batch_size

        self.queue: List[RegenerationQueueItem] = []
        self.processed: List[RegenerationQueueItem] = []
        self.skill_last_regen: Dict[str, str] = {}  # {skill_id: timestamp}

    def should_regenerate(self, skill_id: str, loss_delta: float) -> bool:
        """
        Determine if a skill should be regenerated.

        Args:
            skill_id: The skill
            loss_delta: How much loss changed (negative = improvement)

        Returns:
            True if loss_delta exceeds threshold
        """
        # Regenerate if loss improved significantly
        return abs(loss_delta) > self.loss_threshold

    def queue_regeneration(
        self,
        skill_id: str,
        reason: str,
        priority: float = 0.5,
        loss_delta: Optional[float] = None,
    ) -> Optional[RegenerationQueueItem]:
        """
        Queue a skill for regeneration.

        Args:
            skill_id: Skill to regenerate
            reason: Why (WEIGHT_CHANGE, NEW_SOURCE, ERROR_RECOVERY)
            priority: Priority (0–1)
            loss_delta: Loss improvement (if applicable)

        Returns:
            QueueItem if queued, None if duplicate/skipped
        """
        # Check if already queued
        if any(item.skill_id == skill_id for item in self.queue):
            return None  # Already queued

        item = RegenerationQueueItem(
            skill_id=skill_id,
            reason=reason,
            priority=priority,
            loss_delta=loss_delta,
            metadata={"queued_at": datetime.utcnow().isoformat()},
        )

        if not item.validate():
            return None

        self.queue.append(item)
        return item

    def get_next_batch(self) -> List[RegenerationQueueItem]:
        """
        Get next batch of skills to regenerate.

        Sorted by priority (highest first).

        Returns:
            List of items (up to batch_size)
        """
        if not self.queue:
            return []

        # Sort by priority (highest first)
        sorted_queue = sorted(self.queue, key=lambda x: x.priority, reverse=True)

        # Take up to batch_size
        batch = sorted_queue[:self.batch_size]

        # Remove from queue
        for item in batch:
            self.queue.remove(item)

        # Mark as processed
        self.processed.extend(batch)

        return batch

    def mark_completed(self, skill_id: str) -> None:
        """Mark a regeneration as completed."""
        self.skill_last_regen[skill_id] = datetime.utcnow().isoformat()

    def get_queue_status(self) -> Dict:
        """Get current queue status."""
        return {
            "queued_count": len(self.queue),
            "processed_count": len(self.processed),
            "queue": [
                {
                    "skill_id": item.skill_id,
                    "priority": item.priority,
                    "reason": item.reason,
                }
                for item in self.queue
            ],
        }

    def get_skills_waiting(self) -> List[str]:
        """Get list of skill IDs waiting for regen."""
        return [item.skill_id for item in self.queue]

    def is_queued(self, skill_id: str) -> bool:
        """Check if a skill is queued for regen."""
        return any(item.skill_id == skill_id for item in self.queue)

    def clear_queue(self) -> None:
        """Clear the queue (for testing)."""
        self.queue = []

    def reset(self) -> None:
        """Clear all state."""
        self.queue = []
        self.processed = []
        self.skill_last_regen = {}
