"""Event Emitter — non-blocking learning event emission (ADR-0314 + ADR-0315)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .event_schema import LearningEvent, LearningEventType
from .event_persistence import EventStore


class EventEmitter:
    """Emit learning events without blocking skill execution."""

    def __init__(self, tenant_home: Path, tenant_id: str, max_queue_size: int = 1000):
        """Initialize emitter.

        Args:
            tenant_home: Tenant root directory (deprecated, kept for compatibility)
            tenant_id: Tenant ID (for isolation)
            max_queue_size: Max events in queue before dropping (fire-and-forget)
        """
        self.tenant_home = tenant_home
        self.tenant_id = tenant_id
        self.max_queue_size = max_queue_size
        self.store = EventStore(tenant_id)
        self.event_queue: asyncio.Queue[LearningEvent] = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the event processing worker loop."""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._process_events())

    async def stop(self) -> None:
        """Stop the event processing worker loop."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def emit(self, event: LearningEvent) -> None:
        """Emit a learning event (non-blocking).

        Args:
            event: Event to emit

        Raises:
            ValueError: If tenant_id mismatch
        """
        if event.tenant_id != self.tenant_id:
            raise ValueError(f"Tenant mismatch: event.tenant_id={event.tenant_id}, expected {self.tenant_id}")

        try:
            self.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            # Fire-and-forget: drop event if queue is full, but log it
            import logging
            logging.warning(
                f"EventEmitter queue full, dropping event: type={event.event_type.value}, "
                f"skill={event.skill_name}, session={event.session_id}"
            )

    async def _process_events(self) -> None:
        """Background worker: persist queued events."""
        while True:
            try:
                event = await self.event_queue.get()
                await self.store.write_event(event, self.tenant_id)
                self.event_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                # Log silently, continue processing
                continue

    async def flush(self) -> None:
        """Wait for all pending events to be written."""
        await self.event_queue.join()

    async def get_event_count(self) -> int:
        """Get total persisted event count."""
        return await self.store.get_event_count(tenant_id=self.tenant_id)

    async def emit_decision(
        self,
        decision_id: str,
        choice_type: str,
        candidates: list[str],
        chosen: str,
        session_id: str,
        confidence_score: Optional[float] = None,
        user_input: Optional[str] = None,
        reasoning: Optional[str] = None,
        instance_id: str = "unknown",
    ) -> None:
        """Emit a decision record learning event (ADR-0316).

        Args:
            decision_id: Unique decision identifier
            choice_type: Type of choice
            candidates: Available options
            chosen: Selected option
            session_id: Session ID
            confidence_score: Optional confidence score (ADR-0315)
            user_input: User's original query
            reasoning: Why this choice was made
            instance_id: Instance identifier
        """
        event = LearningEvent(
            event_type=LearningEventType.DECISION_RECORD,
            tenant_id=self.tenant_id,
            instance_id=instance_id,
            skill_name=None,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            payload={
                "decision_id": decision_id,
                "choice_type": choice_type,
                "candidates": candidates,
                "chosen": chosen,
                "confidence_score": confidence_score,
                "user_input": user_input,
                "reasoning": reasoning,
            },
        )
        await self.emit(event)

    async def emit_confidence_score(
        self,
        skill_name: str,
        session_id: str,
        relevance: float,
        reliability: float,
        combined: float,
        band: str,
        reasoning: Optional[str] = None,
        instance_id: str = "unknown",
    ) -> None:
        """Emit a confidence score learning event (ADR-0315).

        Args:
            skill_name: Which skill
            session_id: Session ID
            relevance: Relevance score (0.0–1.0)
            reliability: Reliability score (0.0–1.0)
            combined: Combined score (0.0–1.0)
            band: Band name ("very_high", "high", etc.)
            reasoning: Debug reasoning
            instance_id: Instance identifier
        """
        event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id=self.tenant_id,
            instance_id=instance_id,
            skill_name=skill_name,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            payload={
                "relevance_score": relevance,
                "reliability_score": reliability,
                "combined_score": combined,
                "band": band,
                "reasoning": reasoning,
            },
        )
        await self.emit(event)

    async def emit_outcome(
        self,
        outcome_id: str,
        decision_id: str,
        session_id: str,
        outcome: str,
        feedback_text: Optional[str] = None,
        rating: Optional[int] = None,
        instance_id: str = "unknown",
    ) -> None:
        """Emit an outcome feedback learning event (ADR-0317).

        Args:
            outcome_id: Unique outcome identifier
            decision_id: ID of decision being evaluated
            session_id: Session ID
            outcome: "success", "partial", or "failure"
            feedback_text: User's feedback
            rating: Optional numeric rating (1-5)
            instance_id: Instance identifier
        """
        event = LearningEvent(
            event_type=LearningEventType.OUTCOME_OBSERVED,
            tenant_id=self.tenant_id,
            instance_id=instance_id,
            skill_name=None,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            payload={
                "outcome_id": outcome_id,
                "decision_id": decision_id,
                "outcome": outcome,
                "feedback_text": feedback_text,
                "rating": rating,
            },
        )
        await self.emit(event)

    async def emit_preference(
        self,
        preference_type: str,
        preference_value: str,
        session_id: Optional[str] = None,
        instance_id: str = "unknown",
    ) -> None:
        """Emit a preference change learning event (ADR-0318).

        Args:
            preference_type: Type of preference (decision_style, verbosity, etc.)
            preference_value: New value
            session_id: Optional session ID
            instance_id: Instance identifier
        """
        event = LearningEvent(
            event_type=LearningEventType.PREFERENCE_SET,
            tenant_id=self.tenant_id,
            instance_id=instance_id,
            skill_name=None,
            session_id=session_id or "global",
            timestamp_utc=datetime.utcnow(),
            payload={
                "preference_type": preference_type,
                "preference_value": preference_value,
            },
        )
        await self.emit(event)

    async def emit_metric(
        self,
        metric_id: str,
        metric_type: str,
        value: float,
        session_id: str,
        skill_name: Optional[str] = None,
        tags: Optional[dict] = None,
        instance_id: str = "unknown",
    ) -> None:
        """Emit a metric learning event (ADR-0320).

        Args:
            metric_id: Unique metric identifier
            metric_type: Type of metric (accuracy, latency, confidence, etc.)
            value: Metric value
            session_id: Session ID
            skill_name: Optional skill name
            tags: Optional metadata tags
            instance_id: Instance identifier
        """
        event = LearningEvent(
            event_type=LearningEventType.METRIC_AGGREGATED,
            tenant_id=self.tenant_id,
            instance_id=instance_id,
            skill_name=skill_name,
            session_id=session_id,
            timestamp_utc=datetime.utcnow(),
            payload={
                "metric_id": metric_id,
                "metric_type": metric_type,
                "value": value,
                "tags": tags or {},
            },
        )
        await self.emit(event)

    async def read_events(
        self,
        event_type: Optional[LearningEventType] = None,
        skill_name: Optional[str] = None,
        session_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[LearningEvent]:
        """Read persisted events with filtering."""
        return await self.store.read_events(
            tenant_id=self.tenant_id,
            event_type=event_type,
            skill_name=skill_name,
            session_id=session_id,
            since=since,
            limit=limit,
        )
