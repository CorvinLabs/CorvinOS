"""Session Manager subsystem: Manage tenant-scoped sessions (Phase C).

Creates, loads, and persists sessions to tenant-specific directories.
All session data is isolated per tenant via ExecutionContext.tenant_id.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from .base import Subsystem
from core.paths.tenant import tenant_session_dir

logger = logging.getLogger(__name__)


class SessionManager(Subsystem):
    """Manage sessions per tenant.

    Phase C: All sessions stored in tenant-scoped directory.
    """

    def __init__(
        self,
        context: Optional[Any] = None,
    ):
        """Initialize SessionManager.

        Args:
            context: ExecutionContext with tenant_id for tenant-scoped operations
        """
        # Phase C: Store ExecutionContext for tenant-native operations
        self.context = context
        self.tenant_id = context.tenant_id if context else "_default"

        # In-memory cache of open sessions
        self.open_sessions: Dict[str, Dict[str, Any]] = {}
        self.hub: Optional[Any] = None

    @property
    def name(self) -> str:
        return "session_manager"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub: Any) -> None:
        """Initialize subsystem and subscribe to events.

        Args:
            hub: SubsystemHub instance
        """
        self.hub = hub
        hub.subscribe("session_created", self.on_session_created)
        hub.subscribe("session_closed", self.on_session_closed)
        logger.info(f"SessionManager started (tenant={self.tenant_id})")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to published events (fire-and-forget).

        Args:
            event_name: Name of event
            event_data: Event payload
        """
        if event_name == "session_created":
            await self.on_session_created(event_name, event_data)
        elif event_name == "session_closed":
            await self.on_session_closed(event_name, event_data)

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle synchronous requests from other subsystems.

        Args:
            request_type: Type of request
            **kwargs: Request parameters

        Returns:
            Request result
        """
        match request_type:
            case "create_session":
                return self.create_session(
                    session_id=kwargs.get("session_id"),
                    channel_id=kwargs.get("channel_id"),
                    metadata=kwargs.get("metadata"),
                )
            case "get_session":
                return self.get_session(session_id=kwargs.get("session_id"))
            case "list_sessions":
                return self.list_sessions()
            case "delete_session":
                return self.delete_session(session_id=kwargs.get("session_id"))
            case _:
                raise ValueError(f"Unknown request type: {request_type}")

    async def on_session_created(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Handle session creation event.

        Args:
            event_name: Event name
            event_data: Event data with session_id, channel_id
        """
        session_id = event_data.get("session_id")
        channel_id = event_data.get("channel_id")
        if session_id:
            self.create_session(session_id, channel_id, event_data)

    async def on_session_closed(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Handle session closure event.

        Args:
            event_name: Event name
            event_data: Event data with session_id
        """
        session_id = event_data.get("session_id")
        if session_id and session_id in self.open_sessions:
            self.open_sessions.pop(session_id, None)

    def create_session(
        self,
        session_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create session in tenant-scoped directory.

        Args:
            session_id: Session ID (generated if None)
            channel_id: Channel ID (e.g., "discord", "slack")
            metadata: Optional metadata

        Returns:
            Session dict with id, tenant_id, created_at
        """
        session_id = session_id or str(uuid4())
        session_dir = tenant_session_dir(self.tenant_id, session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        session = {
            "id": session_id,
            "tenant_id": self.tenant_id,
            "channel_id": channel_id,
            "created_at": datetime.utcnow().isoformat(),
            "metadata": metadata or {},
        }

        # Write session file
        session_file = session_dir / "session.json"
        try:
            session_file.write_text(json.dumps(session, indent=2))
            self.open_sessions[session_id] = session
            logger.info(f"Created session {session_id} in tenant {self.tenant_id}")
        except Exception as e:
            logger.error(f"Failed to create session {session_id}: {e}")
            raise

        return session

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load session from tenant directory.

        Args:
            session_id: Session ID

        Returns:
            Session dict or None if not found
        """
        # Check cache first
        if session_id in self.open_sessions:
            return self.open_sessions[session_id]

        # Load from disk
        session_file = tenant_session_dir(self.tenant_id, session_id) / "session.json"
        if not session_file.exists():
            logger.warning(f"Session {session_id} not found in tenant {self.tenant_id}")
            return None

        try:
            session = json.loads(session_file.read_text())
            self.open_sessions[session_id] = session
            return session
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None

    def list_sessions(self) -> list[Dict[str, Any]]:
        """List all sessions for this tenant.

        Returns:
            List of session dicts
        """
        sessions = []
        tenant_home = tenant_session_dir(self.tenant_id, "")
        if not tenant_home.parent.exists():
            return []

        try:
            for session_dir in tenant_home.parent.iterdir():
                if session_dir.is_dir():
                    session_file = session_dir / "session.json"
                    if session_file.exists():
                        try:
                            session = json.loads(session_file.read_text())
                            sessions.append(session)
                        except Exception as e:
                            logger.warning(f"Failed to load session from {session_dir}: {e}")
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")

        return sessions

    def delete_session(self, session_id: str) -> bool:
        """Delete session directory and metadata.

        Args:
            session_id: Session ID

        Returns:
            True if successful, False otherwise
        """
        session_dir = tenant_session_dir(self.tenant_id, session_id)
        try:
            import shutil
            if session_dir.exists():
                shutil.rmtree(session_dir)
            self.open_sessions.pop(session_id, None)
            logger.info(f"Deleted session {session_id} from tenant {self.tenant_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False

    def shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("SessionManager shutdown")
