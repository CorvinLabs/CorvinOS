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
            tenant_home: Tenant root directory
            tenant_id: Tenant ID (for isolation)
            max_queue_size: Max events in queue before dropping (fire-and-forget)
        """
        self.tenant_home = tenant_home
        self.tenant_id = tenant_id
        self.max_queue_size = max_queue_size
        self.store = EventStore(tenant_home)
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
            # Fire-and-forget: drop event if queue is full
            pass

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
        return await self.store.get_event_count(self.tenant_id)

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
            self.tenant_id,
            event_type=event_type,
            skill_name=skill_name,
            session_id=session_id,
            since=since,
            limit=limit,
        )
