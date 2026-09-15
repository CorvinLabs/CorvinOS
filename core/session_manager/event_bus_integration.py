"""
S3.2: EventBus Integration — Wires SessionManager to Brain v0.2 pub/sub
"""
from typing import Callable, Dict, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class SessionEvent:
    """Immutable session event (pub/sub)"""
    event_type: str  # session_split_triggered, context_reduced, session_recovered
    task_id: str
    iteration: int
    data: Dict[str, Any]


class EventBus:
    """Simple pub/sub for session events"""

    def __init__(self):
        self._subscribers: Dict[str, list[Callable]] = {}

    def publish(self, event_type: str, data: Dict[str, Any]):
        """Publish event to all subscribers"""
        event = SessionEvent(
            event_type=event_type,
            task_id=data.get("task_id", "unknown"),
            iteration=data.get("iteration", 0),
            data=data
        )
        logger.info(f"Publishing: {event_type} | task={event.task_id}")

        if event_type in self._subscribers:
            for callback in self._subscribers[event_type]:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"Subscriber failed: {e}")

    def subscribe(self, event_type: str, callback: Callable):
        """Subscribe to event type"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
        logger.info(f"Subscribed to {event_type}")


class SessionEventHandlers:
    """Built-in handlers for session events"""

    @staticmethod
    def handle_split_triggered(event: SessionEvent):
        """Log and audit split trigger"""
        logger.info(f"Split triggered at iteration {event.iteration}: {event.data}")

    @staticmethod
    def handle_context_reduced(event: SessionEvent):
        """Log context reduction"""
        reduction_pct = event.data.get("reduction_pct", 0)
        logger.info(f"Context reduced by {reduction_pct}%")

    @staticmethod
    def handle_session_recovered(event: SessionEvent):
        """Log recovery completion"""
        logger.info(f"Session recovered at iteration {event.iteration}")


def wire_event_bus(session_manager, event_bus: EventBus):
    """Wire SessionManager to EventBus (called by S3 bootstrap)"""
    session_manager.set_event_bus(event_bus)

    # Register built-in handlers
    event_bus.subscribe("session_split_triggered", SessionEventHandlers.handle_split_triggered)
    event_bus.subscribe("context_reduced", SessionEventHandlers.handle_context_reduced)
    event_bus.subscribe("session_recovered", SessionEventHandlers.handle_session_recovered)

    logger.info("EventBus wired to SessionManager")
    return event_bus
