"""Notification Daemon (ADR-0830, ADR-0661) — Phase 2 Foundation

Smart Handoff System: Events → Operators + Plugins (real-time).
"""

import asyncio
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Any
from datetime import datetime
from enum import Enum


class EventPriority(Enum):
    CRITICAL = "critical"  # Must handle immediately
    HIGH = "high"  # Within 1 minute
    NORMAL = "normal"  # Within 5 minutes
    LOW = "low"  # Within 1 hour


class EventTopic(Enum):
    SKILL_EXECUTED = "skill.executed"
    SKILL_ERROR = "skill.error"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    MARKETPLACE_EVENT = "marketplace.event"
    LEARNING_UPDATE = "learning.update"
    QUOTA_EXCEEDED = "quota.exceeded"
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"


@dataclass
class NotificationEvent:
    """Immutable notification event."""
    topic: EventTopic
    priority: EventPriority
    payload: Dict[str, Any]
    source: str  # Plugin/subsystem that emitted
    timestamp: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class NotificationDaemon:
    """Smart handoff: events routed to operators + plugins."""
    
    name = "notification_daemon"
    version = "1.0.0"
    
    def __init__(self):
        self.subscribers: Dict[EventTopic, List[Callable]] = {}
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.running = False
        self.event_history: List[NotificationEvent] = []
        self.max_history = 10000
    
    def subscribe(self, topic: EventTopic, handler: Callable) -> None:
        """Subscribe to event topic."""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        self.subscribers[topic].append(handler)
    
    def unsubscribe(self, topic: EventTopic, handler: Callable) -> None:
        """Unsubscribe from topic."""
        if topic in self.subscribers:
            self.subscribers[topic] = [h for h in self.subscribers[topic] if h != handler]
    
    async def emit(self, event: NotificationEvent) -> bool:
        """Emit event to queue (non-blocking)."""
        try:
            self.event_queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            # Log and drop (fail-closed)
            return False
    
    async def start(self) -> None:
        """Start daemon (process events)."""
        self.running = True
        asyncio.create_task(self._process_loop())
    
    async def stop(self) -> None:
        """Stop daemon gracefully."""
        self.running = False
        # Drain queue
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
    
    async def _process_loop(self) -> None:
        """Main event processing loop."""
        while self.running:
            try:
                # Get event with timeout (check running status regularly)
                event = await asyncio.wait_for(
                    self.event_queue.get(),
                    timeout=1.0
                )
                
                # Record in history
                self._record_event(event)
                
                # Route to subscribers
                if event.topic in self.subscribers:
                    handlers = self.subscribers[event.topic]
                    # Execute all handlers (fire-and-forget, but collect errors)
                    tasks = [self._safe_handler_call(h, event) for h in handlers]
                    await asyncio.gather(*tasks, return_exceptions=True)
            
            except asyncio.TimeoutError:
                # No event in timeout window, continue
                continue
            except Exception as e:
                # Log error, continue processing
                print(f"Daemon error: {e}")
                continue
    
    async def _safe_handler_call(self, handler: Callable, event: NotificationEvent) -> None:
        """Call handler with error isolation."""
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as e:
            # Handler error doesn't crash daemon
            print(f"Handler error for {event.topic}: {e}")
    
    def _record_event(self, event: NotificationEvent) -> None:
        """Record event in immutable history (bounded)."""
        self.event_history.append(event)
        if len(self.event_history) > self.max_history:
            self.event_history.pop(0)
    
    def get_history(self, topic: Optional[EventTopic] = None, limit: int = 100) -> List[NotificationEvent]:
        """Get recent events."""
        events = self.event_history[-limit:]
        if topic:
            events = [e for e in events if e.topic == topic]
        return events


# Global daemon instance (singleton)
_daemon_instance = None


def get_daemon() -> NotificationDaemon:
    """Get or create global daemon."""
    global _daemon_instance
    if _daemon_instance is None:
        _daemon_instance = NotificationDaemon()
    return _daemon_instance


async def emit_event(topic: EventTopic, priority: EventPriority, payload: Dict, source: str) -> bool:
    """Helper: emit event globally."""
    daemon = get_daemon()
    event = NotificationEvent(
        topic=topic,
        priority=priority,
        payload=payload,
        source=source
    )
    return await daemon.emit(event)
