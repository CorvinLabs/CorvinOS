"""
ADR-0400: Graph Event Emission

Integrates graph building with event bus for real-time graph updates.
Events emitted by SessionLifecycleManager, LoopEngineer, ContextReducer.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
import logging
import json

logger = logging.getLogger(__name__)


@dataclass
class GraphEvent:
    """Immutable graph event."""
    event_type: str  # "checkpoint_saved", "decision_made", "context_reduced", "error_occurred"
    task_id: str
    timestamp: str  # ISO format
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict (JSON-safe)."""
        return {
            "event_type": self.event_type,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "data": self.data
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphEvent":
        """Construct from dict."""
        return cls(
            event_type=data["event_type"],
            task_id=data["task_id"],
            timestamp=data["timestamp"],
            data=data["data"]
        )


class GraphEventEmitter:
    """
    Emit and collect graph events from subsystems.

    Implements observer pattern: subsystems call emit(), listeners registered via subscribe().
    """

    def __init__(self):
        """Initialize event emitter."""
        self.events: List[GraphEvent] = []
        self.subscribers: Dict[str, List[Callable]] = {}
        logger.info("GraphEventEmitter initialized")

    def subscribe(self, event_type: str, listener: Callable) -> None:
        """
        Subscribe listener to event type.

        Args:
            event_type: Event type to listen for (or "*" for all)
            listener: Callable that receives (GraphEvent) → None
        """
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(listener)
        logger.debug(f"Subscribed listener to {event_type}")

    def emit(self, event: GraphEvent) -> None:
        """
        Emit event to all subscribers.

        Args:
            event: GraphEvent to emit
        """
        self.events.append(event)
        logger.debug(f"Emitted event: {event.event_type} (task={event.task_id})")

        # Notify subscribers for this event type
        listeners = self.subscribers.get(event.event_type, [])
        listeners += self.subscribers.get("*", [])  # Universal listeners

        for listener in listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Listener failed for {event.event_type}: {e}")

    def get_events(self, task_id: str) -> List[GraphEvent]:
        """Get all events for a task."""
        return [e for e in self.events if e.task_id == task_id]

    def get_events_by_type(self, event_type: str) -> List[GraphEvent]:
        """Get all events of a type."""
        return [e for e in self.events if e.event_type == event_type]

    def clear(self) -> None:
        """Clear all events (for testing)."""
        self.events.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Return event statistics."""
        event_counts = {}
        for event in self.events:
            event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1

        return {
            "total_events": len(self.events),
            "event_types": event_counts,
            "subscribers": {k: len(v) for k, v in self.subscribers.items()}
        }


# Global event emitter instance (singleton pattern)
_global_emitter: Optional[GraphEventEmitter] = None


def get_event_emitter() -> GraphEventEmitter:
    """Get or create global event emitter."""
    global _global_emitter
    if _global_emitter is None:
        _global_emitter = GraphEventEmitter()
    return _global_emitter


def emit_checkpoint_saved(
    task_id: str,
    checkpoint_id: str,
    iteration_num: int,
    trigger: str
) -> None:
    """
    Emit checkpoint_saved event.

    Called from SessionLifecycleManager.evaluate_triggers()
    """
    emitter = get_event_emitter()
    event = GraphEvent(
        event_type="checkpoint_saved",
        task_id=task_id,
        timestamp=datetime.now().isoformat(),
        data={
            "checkpoint_id": checkpoint_id,
            "iteration_num": iteration_num,
            "trigger": trigger
        }
    )
    emitter.emit(event)
    logger.info(f"Checkpoint saved event emitted: {checkpoint_id}")


def emit_decision_made(
    task_id: str,
    decision_text: str,
    iteration: int,
    alternatives: List[str],
    outcome: Optional[str] = None
) -> None:
    """
    Emit decision_made event.

    Called from LoopEngineer.on_decision()
    """
    emitter = get_event_emitter()
    event = GraphEvent(
        event_type="decision_made",
        task_id=task_id,
        timestamp=datetime.now().isoformat(),
        data={
            "decision_text": decision_text,
            "iteration": iteration,
            "alternatives": alternatives,
            "outcome": outcome
        }
    )
    emitter.emit(event)
    logger.info(f"Decision made event emitted: {decision_text[:50]}...")


def emit_context_reduced(
    task_id: str,
    reduction_pct: int,
    tier_counts: Dict[str, int],
    ml_confidence: Optional[float] = None
) -> None:
    """
    Emit context_reduced event.

    Called from ContextReducer.reduce()
    """
    emitter = get_event_emitter()
    event = GraphEvent(
        event_type="context_reduced",
        task_id=task_id,
        timestamp=datetime.now().isoformat(),
        data={
            "reduction_pct": reduction_pct,
            "tier_counts": tier_counts,
            "ml_confidence": ml_confidence
        }
    )
    emitter.emit(event)
    logger.info(f"Context reduced event emitted: {reduction_pct}% reduction")


def emit_error_occurred(
    task_id: str,
    error_type: str,
    error_message: str,
    iteration: int,
    recovery_strategy: Optional[str] = None
) -> None:
    """
    Emit error_occurred event.

    Called from recovery/error handling subsystems.
    """
    emitter = get_event_emitter()
    event = GraphEvent(
        event_type="error_occurred",
        task_id=task_id,
        timestamp=datetime.now().isoformat(),
        data={
            "error_type": error_type,
            "error_message": error_message,
            "iteration": iteration,
            "recovery_strategy": recovery_strategy
        }
    )
    emitter.emit(event)
    logger.warning(f"Error occurred event emitted: {error_type}")


def emit_subgoal_created(
    task_id: str,
    subgoal_id: str,
    description: str,
    parent_goal: Optional[str] = None
) -> None:
    """
    Emit subgoal_created event.

    Called when LoopEngineer breaks down a goal.
    """
    emitter = get_event_emitter()
    event = GraphEvent(
        event_type="subgoal_created",
        task_id=task_id,
        timestamp=datetime.now().isoformat(),
        data={
            "subgoal_id": subgoal_id,
            "description": description,
            "parent_goal": parent_goal
        }
    )
    emitter.emit(event)
    logger.info(f"Subgoal created event emitted: {subgoal_id}")
