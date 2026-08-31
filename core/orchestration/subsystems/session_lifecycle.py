"""
Session Lifecycle Manager — Brain integration for session management.

Responsibilities:
1. Track session creation, activity, reset, destroy
2. Emit session events to Brain graph
3. Maintain session state consistency with graph
4. Handle race conditions during state mutations (feedback-recurring-race-fix-the-primitive-not-the-callsite)

ADR-0296/0298: Session design in Brain
"""

import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from .base import Subsystem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionState:
    """Immutable session state (frozen to prevent race conditions)."""
    session_id: str
    tenant_id: str
    status: str  # "created" | "active" | "reset" | "destroyed"
    created_at: str
    last_activity_at: str
    reset_count: int = 0
    error_state: Optional[Dict[str, Any]] = None


class SessionLifecycleManager(Subsystem):
    """Brain subsystem for session lifecycle management.

    Provides:
    - Session creation and tracking
    - Activity monitoring
    - Reset detection and handling
    - State consistency (immutable snapshots for thread-safety)
    """

    def __init__(self, context: Optional[Any] = None):
        self.context = context
        self.tenant_id = context.tenant_id if context else "_default"
        self.sessions: Dict[str, SessionState] = {}
        self.hub = None

    @property
    def name(self) -> str:
        return "session_lifecycle"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub: Any) -> None:
        """Initialize subsystem and register event listeners."""
        self.hub = hub
        # Subscribe to session events
        hub.subscribe("session_created", self.on_session_created)
        hub.subscribe("session_reset", self.on_session_reset)
        hub.subscribe("session_destroyed", self.on_session_destroyed)
        logger.info(f"SessionLifecycleManager started (tenant={self.tenant_id})")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Handle session lifecycle events."""
        if event_name == "session_created":
            await self.on_session_created(event_name, event_data)
        elif event_name == "session_reset":
            await self.on_session_reset(event_name, event_data)
        elif event_name == "session_destroyed":
            await self.on_session_destroyed(event_name, event_data)

    async def on_session_created(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Create a new session entry in graph."""
        session_id = event_data.get("session_id", str(uuid4()))
        tenant_id = event_data.get("tenant_id", self.tenant_id)

        # Create immutable session state (race-safe)
        session_state = SessionState(
            session_id=session_id,
            tenant_id=tenant_id,
            status="created",
            created_at=datetime.utcnow().isoformat(),
            last_activity_at=datetime.utcnow().isoformat(),
        )

        # Store in registry (thread-safe: immutable dataclass)
        self.sessions[session_id] = session_state

        logger.info(f"Session created: {session_id} (tenant={tenant_id})")

        # Emit graph event
        if self.hub:
            self.hub.publish("session_lifecycle_change", {
                "event": "session_created",
                "session": asdict(session_state),
            })

    async def on_session_reset(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Handle session reset event."""
        session_id = event_data.get("session_id")
        if not session_id or session_id not in self.sessions:
            logger.warning(f"Session reset: unknown session {session_id}")
            return

        old_state = self.sessions[session_id]
        # Create new state with reset status (immutable replacement)
        new_state = SessionState(
            session_id=old_state.session_id,
            tenant_id=old_state.tenant_id,
            status="reset",
            created_at=old_state.created_at,
            last_activity_at=datetime.utcnow().isoformat(),
            reset_count=old_state.reset_count + 1,
            error_state=event_data.get("error_state"),
        )

        # Replace state (atomic: no partial updates, no race windows)
        self.sessions[session_id] = new_state

        logger.info(
            f"Session reset: {session_id} "
            f"(reset_count={new_state.reset_count}, reason={event_data.get('reason')})"
        )

        # Emit graph event
        if self.hub:
            self.hub.publish("session_lifecycle_change", {
                "event": "session_reset",
                "session": asdict(new_state),
                "reason": event_data.get("reason", "unknown"),
            })

    async def on_session_destroyed(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Handle session destruction."""
        session_id = event_data.get("session_id")
        if not session_id or session_id not in self.sessions:
            return

        old_state = self.sessions[session_id]
        # Create final state
        final_state = SessionState(
            session_id=old_state.session_id,
            tenant_id=old_state.tenant_id,
            status="destroyed",
            created_at=old_state.created_at,
            last_activity_at=datetime.utcnow().isoformat(),
            reset_count=old_state.reset_count,
        )

        # Remove from registry
        del self.sessions[session_id]

        logger.info(f"Session destroyed: {session_id}")

        # Emit final event
        if self.hub:
            self.hub.publish("session_lifecycle_change", {
                "event": "session_destroyed",
                "session": asdict(final_state),
            })

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle synchronous requests from other subsystems."""
        if request_type == "get_session_state":
            session_id = kwargs.get("session_id")
            return self.sessions.get(session_id)
        elif request_type == "list_sessions":
            return list(self.sessions.values())
        return None

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("SessionLifecycleManager shutdown")

    def clear_session_cache(self, session_id: Optional[str] = None) -> None:
        """Drop tracking state for the session that was reset.

        MUST NOT be named ``on_session_reset``: that name is already this
        class's async event-bus handler (subscribed in ``startup()`` and
        dispatched from ``on_event()``), and a second definition under the
        same name silently replaced it — leaving the bus calling a sync
        method with ``(event_name, event_data)``.

        One process serves many chats, so a ``/new`` in one of them must not
        touch any other. Passing ``session_id`` removes exactly that entry;
        omitting it clears everything, which is only ever correct for a
        whole-process teardown — never for a user-initiated reset.
        """
        try:
            if session_id is not None:
                if self.sessions.pop(session_id, None) is not None:
                    logger.info(
                        "SessionLifecycleManager cleared session %s", session_id
                    )
                return
            self.sessions.clear()
            logger.info("SessionLifecycleManager cleared ALL session state")
        except Exception as e:
            logger.error(f"SessionLifecycleManager clear_session_cache failed: {e}")
