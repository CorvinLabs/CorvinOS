"""Vibe Event Publisher — L6 → L5 (Brain) coordination (Phase 3, ADR-0423).

Bridges Vibe Engineering (L6) and Brain subsystems (L5) via ContextBus.
Listens to decision requests, classifies via GuidanceClassifier, publishes events
that Brain subsystems can subscribe to for coordinated execution.

Event types published:
  - vibe.guidance_requested: Task decision classification needed
  - vibe.guidance_refactor: Reorganize task structure
  - vibe.guidance_parallelize: Execute parallel branches
  - vibe.guidance_optimize_latency: Accelerate execution
  - vibe.guidance_optimize_cost: Reduce resource consumption
  - vibe.guidance_error_recovery: Handle failure gracefully
  - vibe.guidance_context_split: Split task for context budget
  - vibe.guidance_feature_gating: Safe rollout with feature flag

Behavior:
  - Non-blocking, fire-and-forget (async-safe)
  - Queues guidance events for asynchronous processing
  - Logs all decisions for audit trail
  - Integrates with ContextBus for FIFO ordering

Thread-safe for concurrent task execution across multiple tenants.
"""

import asyncio
import logging
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from core.context_engineering.context_bus import (
    ContextBus,
    get_execution_context,
    get_current_tenant_id,
)
from core.context_engineering.execution_context import ExecutionContext
from .guidance_classifier import (
    GuidanceClassifier,
    DecisionContext,
    GuidanceCategory,
)

logger = logging.getLogger(__name__)


class GuidanceEventType(str, Enum):
    """Event types published by Vibe Event Publisher."""

    REQUESTED = "vibe.guidance_requested"
    REFACTOR = "vibe.guidance_refactor"
    PARALLELIZE = "vibe.guidance_parallelize"
    OPTIMIZE_LATENCY = "vibe.guidance_optimize_latency"
    OPTIMIZE_COST = "vibe.guidance_optimize_cost"
    ERROR_RECOVERY = "vibe.guidance_error_recovery"
    CONTEXT_SPLIT = "vibe.guidance_context_split"
    FEATURE_GATING = "vibe.guidance_feature_gating"


@dataclass
class GuidanceEvent:
    """Event payload for guidance publication."""

    task_id: str
    tenant_id: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    category: str = ""  # GuidanceCategory.value
    confidence: float = 0.5
    rationale: str = ""
    recommended_action: str = ""
    fallback_used: bool = False
    severity: str = "info"
    context_snapshot: Dict[str, Any] = field(default_factory=dict)
    supporting_metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "category": self.category,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "recommended_action": self.recommended_action,
            "fallback_used": self.fallback_used,
            "severity": self.severity,
            "context_snapshot": self.context_snapshot,
            "supporting_metrics": self.supporting_metrics,
        }


class VibeEventPublisher:
    """Publishes guidance events from L6 (Vibe) to L5 (Brain) via ContextBus.

    Coordinates decision classification with Brain subsystems through FIFO
    event ordering. Non-blocking, async-safe.

    Usage:
        publisher = VibeEventPublisher()
        await publisher.start()
        # In decision loop:
        await publisher.publish_guidance_event(decision_context)
    """

    def __init__(self, context_bus: Optional[ContextBus] = None,
                 classifier: Optional[GuidanceClassifier] = None):
        """Initialize publisher.

        Args:
            context_bus: ContextBus instance (uses global singleton if None)
            classifier: GuidanceClassifier instance (creates new if None)
        """
        self.context_bus = context_bus or ContextBus.get_instance()
        self.classifier = classifier or GuidanceClassifier(enable_llm=False)
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._published_events = 0
        self._failed_publishes = 0

    async def start(self) -> None:
        """Start event publisher worker.

        Must be called before publishing any events.
        """
        if self._is_running:
            return

        self._is_running = True
        self._worker_task = asyncio.create_task(self._process_publication_queue())
        logger.info("VibeEventPublisher started")

    async def stop(self) -> None:
        """Stop event publisher gracefully."""
        if not self._is_running:
            return

        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("VibeEventPublisher stopped")

    async def _process_publication_queue(self) -> None:
        """Process guidance events in background (FIFO order).

        Runs continuously, pulling events from queue and publishing to ContextBus
        in order. Non-blocking; events are queued asynchronously.
        """
        while self._is_running:
            try:
                event_type, payload = await self._event_queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing publication queue: {e}")
                continue

            try:
                if self.context_bus:
                    await self.context_bus.publish(event_type, payload)
                    self._published_events += 1
                    logger.debug(
                        f"Published {event_type} for task {payload.get('task_id')}"
                    )
                else:
                    logger.warning("ContextBus not available, dropping event")
                    self._failed_publishes += 1
            except Exception as e:
                logger.error(f"Failed to publish event: {e}")
                self._failed_publishes += 1

            self._event_queue.task_done()

    async def publish_guidance_event(
        self,
        decision_context: DecisionContext,
        request_callback: Optional[Callable[[GuidanceEvent], None]] = None,
    ) -> GuidanceEvent:
        """Classify decision and publish guidance event (async, non-blocking).

        Two-stage process:
          1. Classify decision using GuidanceClassifier
          2. Queue event for async publication to ContextBus

        Args:
            decision_context: DecisionContext with task state
            request_callback: Optional callback after event queued (for testing)

        Returns:
            GuidanceEvent that was queued for publication

        Raises:
            RuntimeError: If publisher not started or ContextBus unavailable
        """
        if not self._is_running:
            raise RuntimeError("VibeEventPublisher not started; call await start()")

        # Get current execution context for audit trail
        exec_ctx = get_execution_context()
        current_tenant = get_current_tenant_id()

        # Validate tenant consistency
        if exec_ctx and exec_ctx.tenant_id != current_tenant:
            logger.warning(
                f"Tenant mismatch: context={exec_ctx.tenant_id}, "
                f"current={current_tenant}"
            )

        # Stage 1: Classify decision
        guidance_decision = await self.classifier.classify(decision_context)

        # Stage 2: Build event payload
        event = GuidanceEvent(
            task_id=decision_context.task_id,
            tenant_id=decision_context.tenant_id,
            category=guidance_decision.category.value,
            confidence=guidance_decision.confidence,
            rationale=guidance_decision.rationale,
            recommended_action=guidance_decision.recommended_action,
            fallback_used=guidance_decision.fallback_used,
            severity=guidance_decision.severity,
            context_snapshot=decision_context.to_dict(),
            supporting_metrics=guidance_decision.supporting_metrics,
        )

        # Map category to event type
        event_type = self._map_category_to_event_type(guidance_decision.category)

        # Queue for async publication (fire-and-forget)
        try:
            await self._event_queue.put((event_type, event.to_dict()))
            logger.info(
                f"Queued {event_type} for task {decision_context.task_id} "
                f"(confidence={guidance_decision.confidence:.2f})"
            )

            # Optional callback for testing/monitoring
            if request_callback:
                try:
                    request_callback(event)
                except Exception as e:
                    logger.warning(f"Request callback failed: {e}")

            return event
        except Exception as e:
            logger.error(f"Failed to queue guidance event: {e}")
            self._failed_publishes += 1
            raise RuntimeError(f"Failed to queue guidance event: {e}")

    def _map_category_to_event_type(
        self, category: GuidanceCategory
    ) -> GuidanceEventType:
        """Map GuidanceCategory to GuidanceEventType.

        Args:
            category: GuidanceCategory from classifier

        Returns:
            Corresponding GuidanceEventType for event publication
        """
        mapping = {
            GuidanceCategory.REFACTOR: GuidanceEventType.REFACTOR,
            GuidanceCategory.PARALLELIZE: GuidanceEventType.PARALLELIZE,
            GuidanceCategory.OPTIMIZE_LATENCY: GuidanceEventType.OPTIMIZE_LATENCY,
            GuidanceCategory.OPTIMIZE_COST: GuidanceEventType.OPTIMIZE_COST,
            GuidanceCategory.ERROR_RECOVERY: GuidanceEventType.ERROR_RECOVERY,
            GuidanceCategory.CONTEXT_SPLIT: GuidanceEventType.CONTEXT_SPLIT,
            GuidanceCategory.FEATURE_GATING: GuidanceEventType.FEATURE_GATING,
        }
        return mapping.get(category, GuidanceEventType.FEATURE_GATING)

    async def subscribe_to_guidance(
        self, category: GuidanceCategory, callback: Callable
    ) -> None:
        """Subscribe to guidance events of a specific category.

        Enables Brain subsystems to react to guidance events.

        Args:
            category: GuidanceCategory to subscribe to
            callback: Async function (payload: dict) -> None

        Requires: ContextBus to be running and publisher started
        """
        if not self.context_bus:
            raise RuntimeError("ContextBus not available")

        event_type = self._map_category_to_event_type(category)
        self.context_bus.subscribe(event_type.value, callback)
        logger.info(f"Subscribed to {event_type.value}")

    def get_stats(self) -> Dict[str, Any]:
        """Get publisher statistics (for monitoring).

        Returns:
            Dict with event counts, queue depth, etc.
        """
        queue_depth = 0
        try:
            queue_depth = self._event_queue.qsize()
        except Exception:
            pass

        return {
            "is_running": self._is_running,
            "published_events": self._published_events,
            "failed_publishes": self._failed_publishes,
            "queue_depth": queue_depth,
            "publication_success_rate": (
                self._published_events / (self._published_events + self._failed_publishes)
                if (self._published_events + self._failed_publishes) > 0
                else 0.0
            ),
        }


# Global singleton instance (for backward compatibility)
_GLOBAL_PUBLISHER: Optional[VibeEventPublisher] = None


def get_publisher() -> VibeEventPublisher:
    """Get global VibeEventPublisher singleton.

    Creates one if not yet initialized.

    Returns:
        Global VibeEventPublisher instance
    """
    global _GLOBAL_PUBLISHER
    if _GLOBAL_PUBLISHER is None:
        _GLOBAL_PUBLISHER = VibeEventPublisher()
    return _GLOBAL_PUBLISHER


def set_publisher(publisher: Optional[VibeEventPublisher]) -> None:
    """Set global VibeEventPublisher singleton.

    Args:
        publisher: New publisher instance, or None to reset
    """
    global _GLOBAL_PUBLISHER
    _GLOBAL_PUBLISHER = publisher
