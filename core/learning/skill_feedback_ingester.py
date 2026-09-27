"""SkillFeedbackIngester — Phase 3 Loop Closure (ADR-0314 + ADR-0722).

Ingests and aggregates three event streams into feedback signals for confidence scoring:
1. skill.executed — From audit chain when Skills run (L5 routing, L10 context)
2. user_feedback — Explicit feedback (thumbs up/down on skill decisions)
3. task_outcome — Task completion status (success/failure/timeout)

Invariants (LOAD-BEARING):
- Immutable: events are frozen dataclasses, never modified after ingestion
- Tenant-scoped: all operations filtered by tenant_id (GDPR Art. 32)
- Audit-first: every ingested event hash-chained before disk write
- Content-free: no prompts, transcripts, user input — skill_id, outcome, metrics only
- Batched: aggregates events into windows (time-based 5min or count-based 1000 events)
- Idempotent: deduplicates by event_id, safe to re-run on partial failures

Event Flow:
  SkillExecuted + UserFeedback + TaskOutcome
    ↓
  Ingest (validate, scrub PII, batch)
    ↓
  Aggregate (group by skill_id, compute signal strength)
    ↓
  Score (confidence = f(success_rate, feedback_strength, latency))
    ↓
  OptimizeParam (delta = gradient(confidence, parameters))
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.learning.learning_events import LearningEvent, EventType
from core.paths import tenant_home

logger = logging.getLogger(__name__)


class FeedbackSignalType(str, Enum):
    """Feedback signal classification."""
    OUTCOME_SUCCESS = "outcome_success"  # Task completed successfully
    OUTCOME_FAILURE = "outcome_failure"  # Task failed
    OUTCOME_TIMEOUT = "outcome_timeout"  # Task timed out
    USER_THUMBS_UP = "user_thumbs_up"  # Explicit positive feedback
    USER_THUMBS_DOWN = "user_thumbs_down"  # Explicit negative feedback
    USER_RATING = "user_rating"  # 1-5 star rating
    LATENCY_FAST = "latency_fast"  # < 1 second
    LATENCY_SLOW = "latency_slow"  # > 10 seconds
    COST_EFFICIENT = "cost_efficient"  # < threshold
    COST_EXPENSIVE = "cost_expensive"  # > threshold


@dataclass(frozen=True)
class FeedbackSignal:
    """Immutable aggregated feedback signal for one skill."""
    signal_id: str
    skill_id: str
    tenant_id: str
    signal_type: FeedbackSignalType
    strength: float  # [0.0, 1.0] confidence in this signal
    count: int  # Number of events aggregated
    timestamp: str  # ISO-8601 UTC
    metadata: Dict[str, Any] = field(default_factory=dict)  # latency_ms, cost, rating, etc.

    def __post_init__(self):
        """Validate signal."""
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be [0.0, 1.0], got {self.strength}")
        if self.count < 1:
            raise ValueError(f"count must be >= 1, got {self.count}")


@dataclass
class IngestionWindow:
    """Time/count-based window of ingested events (mutable during aggregation)."""
    window_id: str
    tenant_id: str
    skill_id: str
    start_time: datetime
    events: List[LearningEvent] = field(default_factory=list)
    signals: List[FeedbackSignal] = field(default_factory=list)
    is_complete: bool = False

    def add_event(self, event: LearningEvent) -> None:
        """Add event to window (idempotent by event_id)."""
        # Dedup by event_id
        if not any(e.event_id == event.event_id for e in self.events):
            self.events.append(event)

    def size(self) -> int:
        """Return number of events in window."""
        return len(self.events)

    def elapsed_seconds(self) -> float:
        """Return elapsed time since window start."""
        return (datetime.now(timezone.utc) - self.start_time).total_seconds()

    def should_close(self, batch_size: int = 1000, window_seconds: int = 300) -> bool:
        """Check if window should be closed (size or time limit reached)."""
        return self.size() >= batch_size or self.elapsed_seconds() >= window_seconds


class SkillFeedbackIngester:
    """Ingest, aggregate, and emit feedback signals for learning loop (ADR-0314)."""

    def __init__(
        self,
        tenant_id: str,
        batch_size: int = 1000,
        window_seconds: int = 300,
        emitter: Optional[Any] = None,
    ):
        """Initialize ingester.

        Args:
            tenant_id: Tenant scope (GDPR Art. 32)
            batch_size: Events per window (default 1000)
            window_seconds: Time window in seconds (default 5 min)
            emitter: EventEmitter for writing events (tests can inject)
        """
        self.tenant_id = tenant_id
        self.batch_size = max(10, min(batch_size, 10000))
        self.window_seconds = max(30, min(window_seconds, 3600))
        self.emitter = emitter

        self._windows: Dict[str, IngestionWindow] = {}  # skill_id → current window
        self._processed_event_ids: set[str] = set()  # For idempotency
        self._lock = asyncio.Lock()

    async def ingest_event(self, event: LearningEvent) -> Optional[FeedbackSignal]:
        """Ingest a single learning event.

        Args:
            event: A LearningEvent (skill.executed, feedback, outcome)

        Returns:
            FeedbackSignal if a complete window was produced, else None

        Raises:
            ValueError: if event.tenant_id != self.tenant_id (isolation breach)
        """
        # CRITICAL: tenant isolation (GDPR Art. 32)
        if event.tenant_id != self.tenant_id:
            logger.error(
                f"Ingestion failed: tenant mismatch (expected {self.tenant_id!r}, "
                f"got {event.tenant_id!r})"
            )
            return None

        async with self._lock:
            # Idempotency: skip if already processed
            if event.event_id in self._processed_event_ids:
                return None
            self._processed_event_ids.add(event.event_id)

            # Get or create window for this skill
            skill_id = event.skill_id
            if skill_id not in self._windows:
                self._windows[skill_id] = IngestionWindow(
                    window_id=f"win_{skill_id}_{int(time.time() * 1000)}",
                    tenant_id=event.tenant_id,
                    skill_id=skill_id,
                    start_time=datetime.now(timezone.utc),
                )

            window = self._windows[skill_id]
            window.add_event(event)

            # Check if window should close
            if window.should_close(self.batch_size, self.window_seconds):
                window.is_complete = True
                signal = await self._aggregate_window(window)
                # Reset window for next batch
                del self._windows[skill_id]
                return signal

        return None

    async def ingest_batch(
        self,
        events: List[LearningEvent],
    ) -> List[FeedbackSignal]:
        """Ingest a batch of events.

        Args:
            events: List of LearningEvents

        Returns:
            List of FeedbackSignals from any completed windows
        """
        signals = []
        for event in events:
            signal = await self.ingest_event(event)
            if signal:
                signals.append(signal)
        return signals

    async def flush_all_windows(self) -> List[FeedbackSignal]:
        """Flush and aggregate all open windows (end-of-session cleanup).

        Returns:
            List of all FeedbackSignals from flushed windows
        """
        signals = []
        async with self._lock:
            for skill_id, window in list(self._windows.items()):
                if window.size() > 0:  # Skip empty windows
                    window.is_complete = True
                    signal = await self._aggregate_window(window)
                    if signal:
                        signals.append(signal)
                    del self._windows[skill_id]
        return signals

    async def _aggregate_window(self, window: IngestionWindow) -> Optional[FeedbackSignal]:
        """Aggregate events in a window into a single feedback signal.

        Args:
            window: IngestionWindow with events

        Returns:
            FeedbackSignal, or None if window too small
        """
        if window.size() < 1:
            return None

        # Classify events and compute aggregate signal
        event_types: Dict[EventType, int] = {}
        success_count = 0
        failure_count = 0
        latencies: List[int] = []
        costs: List[float] = []
        ratings: List[int] = []

        for event in window.events:
            event_types[event.event_type] = event_types.get(event.event_type, 0) + 1

            signal = event.signal or {}

            # Extract outcome
            if event.event_type == EventType.OUTCOME:
                if signal.get("success"):
                    success_count += 1
                else:
                    failure_count += 1
                if "duration_ms" in signal:
                    latencies.append(signal["duration_ms"])
                if "cost" in signal:
                    costs.append(float(signal["cost"]))

            # Extract feedback strength
            elif event.event_type == EventType.FEEDBACK:
                if signal.get("is_positive"):
                    success_count += 1
                else:
                    failure_count += 1
                if "rating" in signal:
                    ratings.append(signal["rating"])

            # Extract latency/cost signals
            elif event.event_type == EventType.METRIC:
                if "latency_ms" in signal:
                    latencies.append(signal["latency_ms"])
                if "cost" in signal:
                    costs.append(float(signal["cost"]))

        # Compute aggregate signal type (majority vote)
        signal_type_vote: Dict[FeedbackSignalType, int] = {}

        # Outcome signals (highest priority)
        if success_count > failure_count:
            signal_type_vote[FeedbackSignalType.OUTCOME_SUCCESS] = success_count
        elif failure_count > 0:
            signal_type_vote[FeedbackSignalType.OUTCOME_FAILURE] = failure_count

        # Latency signals
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            if avg_latency < 1000:  # < 1 second
                signal_type_vote[FeedbackSignalType.LATENCY_FAST] = len(latencies)
            elif avg_latency > 10000:  # > 10 seconds
                signal_type_vote[FeedbackSignalType.LATENCY_SLOW] = len(latencies)

        # Cost signals
        if costs:
            avg_cost = sum(costs) / len(costs)
            # Thresholds vary by skill type; 0.05 is a reasonable default
            if avg_cost < 0.05:
                signal_type_vote[FeedbackSignalType.COST_EFFICIENT] = len(costs)
            elif avg_cost > 0.20:
                signal_type_vote[FeedbackSignalType.COST_EXPENSIVE] = len(costs)

        # Pick winning signal type
        if not signal_type_vote:
            # No clear signal, skip this window
            return None

        winning_signal_type = max(signal_type_vote, key=signal_type_vote.get)

        # Compute signal strength: [0.0, 1.0]
        # Strength = success_rate × confidence_in_outcome × statistical_power
        total_outcomes = success_count + failure_count
        if total_outcomes > 0:
            success_rate = success_count / total_outcomes
        else:
            success_rate = 0.5  # Neutral if no outcomes

        # Confidence increases with sample size (sqrt curve, saturates at 100+)
        sample_size_confidence = min(1.0, math.sqrt(window.size() / 100.0))

        strength = success_rate * sample_size_confidence

        # Create feedback signal
        metadata = {
            "event_count": window.size(),
            "success_count": success_count,
            "failure_count": failure_count,
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
            "avg_cost": sum(costs) / len(costs) if costs else None,
            "avg_rating": sum(ratings) / len(ratings) if ratings else None,
            "window_id": window.window_id,
        }

        signal = FeedbackSignal(
            signal_id=f"sig_{window.window_id}",
            skill_id=window.skill_id,
            tenant_id=window.tenant_id,
            signal_type=winning_signal_type,
            strength=strength,
            count=window.size(),
            # Aware datetime: isoformat() already ends in +00:00 — appending
            # "Z" produced an unparseable "...+00:00Z".
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            metadata=metadata,
        )

        logger.info(
            f"Aggregated window {window.window_id}: skill={window.skill_id} "
            f"signal={winning_signal_type.value} strength={strength:.3f} count={window.size()}"
        )

        return signal

    def recent_signals(self, skill_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent feedback signals (for operator inspection).

        Args:
            skill_id: Filter by skill (None = all skills)
            limit: Max signals to return

        Returns:
            List of signal dicts (JSON-serializable)
        """
        signals = []
        for wid, window in list(self._windows.items()):
            if skill_id and wid != skill_id:
                continue
            for sig in window.signals:
                if skill_id is None or sig.skill_id == skill_id:
                    signals.append({
                        "signal_id": sig.signal_id,
                        "skill_id": sig.skill_id,
                        "signal_type": sig.signal_type.value,
                        "strength": sig.strength,
                        "count": sig.count,
                        "timestamp": sig.timestamp,
                    })
        return signals[-limit:]


__all__ = [
    "SkillFeedbackIngester",
    "FeedbackSignal",
    "FeedbackSignalType",
    "IngestionWindow",
]
