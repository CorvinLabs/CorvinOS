"""SessionManager EventBus Integration — S3.2 wiring.

Publishes session lifecycle events to SubsystemHub:
  - session_split_triggered (when SessionLifecycleManager detects split)
  - context_reduced (when ContextReducer completes reduction)
  - session_recovered (when RecoveryEngine completes recovery)

ADR-0348: Event Bus Pattern
ADR-0541: Session Bridging EventStore Protocol (amended for EventBus)
"""

import logging
from typing import Any, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class SessionSplitEvent:
    """session_split_triggered event."""
    task_id: str
    split_trigger_type: str  # e.g., 'context_window_exceeded', 'cost_limit_reached'
    turn_number: int
    checkpoint_id: str
    timestamp: str


@dataclass
class ContextReducedEvent:
    """context_reduced event."""
    task_id: str
    original_context_tokens: int
    reduced_context_tokens: int
    reduction_ratio: float  # e.g., 0.91 for 91% reduction
    tier_summary: Dict[str, int]  # e.g., {'system': 200, 'relevant': 800, 'historical': 50}
    timestamp: str


@dataclass
class SessionRecoveredEvent:
    """session_recovered event."""
    task_id: str
    recovery_pattern: str  # e.g., 'replay', 'adapt', 'backtrack', 'pause'
    recovered_turn: int
    success: bool
    reason: Optional[str] = None
    timestamp: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class SessionManagerEventBusAdapter:
    """Adapter: SessionManager components → SubsystemHub EventBus.

    Lifecycle:
    1. SessionManager components call adapter methods (publish_split, publish_reduced, etc.)
    2. Adapter formats event data
    3. Adapter publishes to hub via hub.publish_event()

    No coupling: SessionManager doesn't import SubsystemHub.
    Hub is injected at SessionManager init time.
    """

    def __init__(self, hub: Optional[Any] = None):
        """Initialize adapter.

        Args:
            hub: SubsystemHub instance (or None; injected later via set_hub)
        """
        self.hub = hub

    def set_hub(self, hub: Any) -> None:
        """Inject SubsystemHub after initialization (for testing/flexibility)."""
        self.hub = hub

    def publish_split_triggered(
        self,
        task_id: str,
        split_trigger_type: str,
        turn_number: int,
        checkpoint_id: str,
    ) -> None:
        """Publish session_split_triggered event.

        Called by: SessionLifecycleManager.detect_split_trigger()
        """
        if self.hub is None:
            logger.debug("EventBus hub not set; skipping session_split_triggered")
            return

        event_data = {
            "task_id": task_id,
            "split_trigger_type": split_trigger_type,
            "turn_number": turn_number,
            "checkpoint_id": checkpoint_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        self.hub.publish_event("session_split_triggered", event_data)
        logger.info(
            f"Published session_split_triggered: task={task_id}, "
            f"trigger={split_trigger_type}, turn={turn_number}"
        )

    def publish_context_reduced(
        self,
        task_id: str,
        original_tokens: int,
        reduced_tokens: int,
        tier_summary: Dict[str, int],
    ) -> None:
        """Publish context_reduced event.

        Called by: ContextReducer after reduction completes.

        Args:
            tier_summary: e.g., {'system': 200, 'relevant': 800, 'historical': 50}
        """
        if self.hub is None:
            logger.debug("EventBus hub not set; skipping context_reduced")
            return

        reduction_ratio = (
            (original_tokens - reduced_tokens) / original_tokens
            if original_tokens > 0
            else 0.0
        )

        event_data = {
            "task_id": task_id,
            "original_context_tokens": original_tokens,
            "reduced_context_tokens": reduced_tokens,
            "reduction_ratio": reduction_ratio,
            "tier_summary": tier_summary,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        self.hub.publish_event("context_reduced", event_data)
        logger.info(
            f"Published context_reduced: task={task_id}, "
            f"reduction={reduction_ratio:.1%} ({original_tokens}→{reduced_tokens} tokens)"
        )

    def publish_session_recovered(
        self,
        task_id: str,
        recovery_pattern: str,
        recovered_turn: int,
        success: bool,
        reason: Optional[str] = None,
    ) -> None:
        """Publish session_recovered event.

        Called by: RecoveryEngine after recovery attempt completes.
        """
        if self.hub is None:
            logger.debug("EventBus hub not set; skipping session_recovered")
            return

        event_data = {
            "task_id": task_id,
            "recovery_pattern": recovery_pattern,
            "recovered_turn": recovered_turn,
            "success": success,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        self.hub.publish_event("session_recovered", event_data)
        logger.info(
            f"Published session_recovered: task={task_id}, "
            f"pattern={recovery_pattern}, success={success}"
        )


# Singleton instance (injected by SessionManager)
_eventbus_adapter: Optional[SessionManagerEventBusAdapter] = None


def get_eventbus_adapter() -> SessionManagerEventBusAdapter:
    """Get or create the global SessionManager EventBus adapter."""
    global _eventbus_adapter
    if _eventbus_adapter is None:
        _eventbus_adapter = SessionManagerEventBusAdapter()
    return _eventbus_adapter


def set_eventbus_hub(hub: Any) -> None:
    """Inject SubsystemHub into the global adapter (called at SessionManager init)."""
    adapter = get_eventbus_adapter()
    adapter.set_hub(hub)
