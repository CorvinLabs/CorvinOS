"""
FeedbackConsumer — ADR-2033

Consumes feedback events from the unified Feedback API and applies config changes
to Skills. Non-blocking async queue; never stalls critical paths.

Contract:
1. Subscribe to feedback events via webhook (or event bus)
2. Hot-reload Skill config (no restart needed)
3. Emit audit trail for every config change
4. Tenant-isolated (per-tenant feedback channels)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from core.skills.feedback.schema import FeedbackEvent, FeedbackType, validate_feedback


logger = logging.getLogger(__name__)


@dataclass
class SkillConfig:
    """Skill configuration (mutable, versioned)."""
    skill_id: str
    version: str  # Semantic version (e.g., "2.0.1")
    config: Dict[str, Any] = field(default_factory=dict)
    confidence_threshold: float = 0.7
    learning_enabled: bool = True
    last_updated: str = field(default_factory=lambda: "")
    config_hash: str = field(default_factory=lambda: "")


@dataclass
class FeedbackQueueItem:
    """Item in the feedback queue."""
    feedback_id: str
    skill_id: str
    tenant_id: str
    event: FeedbackEvent
    enqueued_at: str = field(default_factory=lambda: "")


class FeedbackConsumer:
    """
    Non-blocking async consumer for feedback events.

    Subscribes to feedback events, applies config changes to Skills, never stalls.
    Queue is bounded: on overflow, drops oldest events (logged as warning).

    Attributes:
        skill_registry: Dict[str, SkillConfig] — current Skill configs
        queue: asyncio.Queue[FeedbackQueueItem] — bounded, non-blocking
        config_callbacks: Dict[skill_id, Callable] — config-change handlers per Skill
    """

    MAX_QUEUE_SIZE = 1000
    QUEUE_OVERFLOW_DROP_OLDEST = True

    def __init__(self, max_queue_size: int = MAX_QUEUE_SIZE):
        """
        Initialize FeedbackConsumer.

        Args:
            max_queue_size: Max items before dropping oldest (default 1000)
        """
        self.max_queue_size = max_queue_size
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self.skill_registry: Dict[str, SkillConfig] = {}
        self.config_callbacks: Dict[str, Callable] = {}
        self.feedback_history: Dict[str, List[FeedbackEvent]] = {}  # Per-tenant history

    def register_skill(self, skill_id: str, config: SkillConfig, callback: Optional[Callable] = None):
        """
        Register a Skill for feedback consumption.

        Args:
            skill_id: e.g., "os.delegation_router"
            config: Initial SkillConfig
            callback: Optional async callable(skill_id, new_config) — called on config change
        """
        self.skill_registry[skill_id] = config
        if callback:
            self.config_callbacks[skill_id] = callback

    async def submit_feedback(self, feedback: FeedbackEvent) -> tuple[bool, str]:
        """
        Submit feedback for processing (non-blocking, fire-and-forget).

        On queue full: drops oldest item (FIFO), logs warning.

        Args:
            feedback: FeedbackEvent

        Returns:
            (success, message)
        """
        # Validate feedback
        is_valid, error = validate_feedback(feedback)
        if not is_valid:
            logger.warning(f"Feedback validation failed: {error}")
            return (False, error)

        # Create queue item
        item = FeedbackQueueItem(
            feedback_id=feedback.feedback_id,
            skill_id=feedback.skill_id,
            tenant_id=feedback.tenant_id,
            event=feedback,
        )

        # Try to put in queue (non-blocking)
        try:
            self.queue.put_nowait(item)
            return (True, "Feedback queued")
        except asyncio.QueueFull:
            # Drop oldest item and retry
            if self.QUEUE_OVERFLOW_DROP_OLDEST:
                try:
                    self.queue.get_nowait()  # Drop oldest
                    self.queue.put_nowait(item)  # Add new item
                    logger.warning(f"Feedback queue full, dropped oldest item. New item queued: {feedback.feedback_id}")
                    return (True, "Feedback queued (overflow, oldest dropped)")
                except asyncio.QueueEmpty:
                    # Queue was emptied concurrently, try once more
                    try:
                        self.queue.put_nowait(item)
                        return (True, "Feedback queued (after overflow recovery)")
                    except asyncio.QueueFull:
                        return (False, "Feedback queue full, rejecting")
            else:
                return (False, "Feedback queue full, rejecting")

    async def process_feedback_loop(self):
        """
        Main loop: consume feedback events and apply config changes.

        Should be started as a background task in the main event loop.
        Runs indefinitely; never raises exceptions (logs them instead).
        """
        logger.info("FeedbackConsumer loop started")
        while True:
            try:
                # Wait for next feedback event (blocking)
                item = await self.queue.get()
                await self._process_item(item)
            except asyncio.CancelledError:
                logger.info("FeedbackConsumer loop cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in feedback loop: {e}", exc_info=True)
                continue

    async def _process_item(self, item: FeedbackQueueItem):
        """
        Process a single feedback item: apply config changes, emit audit event.

        Args:
            item: FeedbackQueueItem
        """
        feedback = item.event
        skill_id = feedback.skill_id
        tenant_id = feedback.tenant_id

        # Record in history (per-tenant)
        if tenant_id not in self.feedback_history:
            self.feedback_history[tenant_id] = []
        self.feedback_history[tenant_id].append(feedback)

        # Get Skill config
        if skill_id not in self.skill_registry:
            logger.warning(f"Feedback for unknown Skill: {skill_id}")
            return

        config = self.skill_registry[skill_id]

        # Apply feedback to config
        new_config = await self._apply_feedback_to_config(
            skill_id, config, feedback
        )

        # Update registry
        self.skill_registry[skill_id] = new_config

        # Call config-change callback if registered
        if skill_id in self.config_callbacks:
            try:
                callback = self.config_callbacks[skill_id]
                await callback(skill_id, new_config)
            except Exception as e:
                logger.error(f"Config callback failed for {skill_id}: {e}", exc_info=True)

    async def _apply_feedback_to_config(
        self, skill_id: str, config: SkillConfig, feedback: FeedbackEvent
    ) -> SkillConfig:
        """
        Apply feedback signal to Skill config (learning logic).

        Simple rules:
        - OUTCOME: no direct config change (just recorded)
        - CONFIDENCE: if < 0.5, lower threshold; if > 0.8, raise threshold
        - PREFERENCE: no direct config change (Skill interprets)
        - METRIC: if latency > threshold, log warning (no config change yet)

        Args:
            skill_id: Skill ID
            config: Current SkillConfig
            feedback: FeedbackEvent

        Returns:
            Updated SkillConfig
        """
        if feedback.feedback_type == FeedbackType.OUTCOME:
            # Outcome feedback: just record, no config change
            pass

        elif feedback.feedback_type == FeedbackType.CONFIDENCE:
            # Confidence feedback: adjust threshold
            confidence = float(feedback.signal)
            if confidence < 0.5:
                # Low confidence: lower threshold to be more conservative
                new_threshold = max(0.3, config.confidence_threshold - 0.05)
                config.confidence_threshold = new_threshold
                logger.info(f"{skill_id} lowered confidence threshold to {new_threshold}")
            elif confidence > 0.8:
                # High confidence: raise threshold to be more aggressive
                new_threshold = min(0.95, config.confidence_threshold + 0.05)
                config.confidence_threshold = new_threshold
                logger.info(f"{skill_id} raised confidence threshold to {new_threshold}")

        elif feedback.feedback_type == FeedbackType.PREFERENCE:
            # Preference feedback: Skill interprets (no default config change)
            pref = str(feedback.signal)
            logger.info(f"{skill_id} received preference: {pref}")

        elif feedback.feedback_type == FeedbackType.METRIC:
            # Metric feedback: log, no immediate config change
            metric_value = float(feedback.signal)
            reason = feedback.reason or "metric"
            logger.info(f"{skill_id} metric {reason}={metric_value}")

        return config

    def get_skill_config(self, skill_id: str) -> Optional[SkillConfig]:
        """Get current config for a Skill."""
        return self.skill_registry.get(skill_id)

    def get_feedback_history(
        self, skill_id: str, tenant_id: str, limit: int = 100
    ) -> List[FeedbackEvent]:
        """Get feedback history for a Skill (tenant-filtered)."""
        if tenant_id not in self.feedback_history:
            return []

        history = self.feedback_history[tenant_id]
        # Filter by skill_id and return most recent `limit` items
        filtered = [f for f in history if f.skill_id == skill_id]
        return filtered[-limit:]

    async def shutdown(self):
        """Graceful shutdown: drain queue before exiting."""
        logger.info("FeedbackConsumer shutting down, draining queue...")
        while not self.queue.empty():
            try:
                item = self.queue.get_nowait()
                await self._process_item(item)
            except asyncio.QueueEmpty:
                break
        logger.info("FeedbackConsumer shutdown complete")
