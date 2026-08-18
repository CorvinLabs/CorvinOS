"""
Decision event stream for real-time task monitoring.

Streams every task decision (engine choice, cost, confidence) to dashboard.
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class DecisionEvent:
    """Immutable decision event."""
    event_id: str
    timestamp: datetime
    task_id: str
    engine_choice: str  # "claude", "local_llama2", "hermes"
    confidence: float  # [0.0..1.0]
    cost_estimate_usd: float
    latency_estimate_ms: float
    routing_reason: str  # Why this engine chosen

    def to_dict(self):
        """Serialize for WebSocket."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "task_id": self.task_id,
            "engine_choice": self.engine_choice,
            "confidence": self.confidence,
            "cost_estimate_usd": self.cost_estimate_usd,
            "latency_estimate_ms": self.latency_estimate_ms,
            "routing_reason": self.routing_reason,
        }


class DecisionStreamCollector:
    """
    Collect and buffer decision events.

    Emits to WebSocket for live dashboard updates.
    """

    def __init__(self, buffer_size: int = 1000):
        """Initialize collector."""
        self.events: list = []
        self.buffer_size = buffer_size

    def record_decision(
        self,
        event_id: str,
        task_id: str,
        engine_choice: str,
        confidence: float,
        cost_estimate_usd: float,
        latency_estimate_ms: float,
        routing_reason: str,
    ) -> DecisionEvent:
        """
        Record a task decision.

        Args:
            event_id: Unique event ID
            task_id: Task ID
            engine_choice: Chosen engine
            confidence: Routing confidence
            cost_estimate_usd: Estimated cost
            latency_estimate_ms: Estimated latency
            routing_reason: Why this engine

        Returns:
            DecisionEvent (immutable)
        """
        event = DecisionEvent(
            event_id=event_id,
            timestamp=datetime.utcnow(),
            task_id=task_id,
            engine_choice=engine_choice,
            confidence=confidence,
            cost_estimate_usd=cost_estimate_usd,
            latency_estimate_ms=latency_estimate_ms,
            routing_reason=routing_reason,
        )

        # Buffer event
        self.events.append(event)
        if len(self.events) > self.buffer_size:
            self.events.pop(0)

        return event

    def get_recent_decisions(self, limit: int = 100) -> list:
        """Get recent decisions."""
        return self.events[-limit:]

    def get_decisions_for_task(self, task_id: str) -> list:
        """Get all decisions for a task."""
        return [e for e in self.events if e.task_id == task_id]

    def get_engine_stats(self) -> dict:
        """Get stats by engine choice."""
        stats = {}
        for event in self.events:
            if event.engine_choice not in stats:
                stats[event.engine_choice] = {
                    "count": 0,
                    "avg_confidence": 0.0,
                    "total_cost": 0.0,
                }
            s = stats[event.engine_choice]
            s["count"] += 1
            s["total_cost"] += event.cost_estimate_usd

        # Average confidence
        for engine in stats:
            engine_events = [e for e in self.events if e.engine_choice == engine]
            if engine_events:
                stats[engine]["avg_confidence"] = sum(e.confidence for e in engine_events) / len(engine_events)

        return stats
