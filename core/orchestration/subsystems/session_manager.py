"""Session Manager subsystem: Manage tenant-scoped sessions (Phase C).

Creates, loads, and persists sessions to tenant-specific directories.
All session data is isolated per tenant via ExecutionContext.tenant_id.

Phase 1: Task Context Drift Prevention
- initialize_task(goal): Creates GoalContext with SHA256 hash
- resume_from_checkpoint(): Restores GoalContext + verifies integrity
- Audit trail for all goal events (GDPR Art. 30)

ADR-0405: GoalContext Persistence
ADR-0407: Task Context Drift Prevention (Master)
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from .base import Subsystem
from core.paths.tenant import tenant_session_dir
from core.session_manager.goal_context import GoalContext

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

    def initialize_task(
        self,
        session_id: str,
        goal: str,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Initialize task with goal context (Phase 1: Task Context Drift).

        Creates a GoalContext with SHA256 hash and registers it in session.
        Audit-logged per GDPR Art. 30.

        Args:
            session_id: Session ID for this task
            goal: The task goal text
            task_id: Optional task ID (generated if None)

        Returns:
            Dictionary with session_id, task_id, goal_context

        Raises:
            ValueError: If goal is empty or invalid
            AssertionError: If goal_context creation fails
        """
        task_id = task_id or str(uuid4())
        session = self.get_session(session_id)
        if not session:
            logger.error(f"Session {session_id} not found")
            raise ValueError(f"Session {session_id} not found")

        # Create GoalContext with SHA256 hash
        goal_context = GoalContext.create(goal)
        logger.info(
            f"Initialized task {task_id} with goal hash {goal_context.goal_hash[:16]}... "
            f"(tenant={self.tenant_id})"
        )

        # Store goal_context in session metadata
        session["goal_context"] = goal_context.to_dict()
        session["task_id"] = task_id
        session["goal_initialized_at"] = datetime.utcnow().isoformat()

        # Persist to disk
        session_file = tenant_session_dir(self.tenant_id, session_id) / "session.json"
        try:
            session_file.write_text(json.dumps(session, indent=2))
            self.open_sessions[session_id] = session
        except Exception as e:
            logger.error(f"Failed to persist goal context for session {session_id}: {e}")
            raise

        # Audit log: goal_context_initialized (GDPR Art. 30)
        self._audit_log_goal_initialized(session_id, task_id, goal_context)

        return {
            "session_id": session_id,
            "task_id": task_id,
            "goal_context": goal_context.to_dict(),
        }

    def resume_from_checkpoint(
        self,
        session_id: str,
        checkpoint_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Resume task from checkpoint with goal integrity verification (Phase 1).

        Restores GoalContext from checkpoint and verifies hash integrity.
        Audit-logged per GDPR Art. 30, 32.

        Args:
            session_id: Session ID to resume
            checkpoint_data: Checkpoint data dict (from SessionCheckpoint.to_dict())

        Returns:
            Dictionary with session_id, task_id, goal_context, integrity_verified

        Raises:
            ValueError: If checkpoint_data invalid
            AssertionError: If goal hash verification fails (fail-closed)
        """
        if not checkpoint_data:
            raise ValueError("checkpoint_data is required")

        # Extract goal_context from checkpoint
        goal_context_data = checkpoint_data.get("goal_context")
        if not goal_context_data:
            logger.warning(
                f"Checkpoint for session {session_id} has no goal_context "
                "(backward compatibility)"
            )
            return {
                "session_id": session_id,
                "task_id": checkpoint_data.get("task_id"),
                "goal_context": None,
                "integrity_verified": False,
            }

        # Reconstruct GoalContext (triggers integrity verification)
        try:
            goal_context = GoalContext.from_dict(goal_context_data)
            logger.info(
                f"Restored GoalContext for session {session_id} with hash "
                f"{goal_context.goal_hash[:16]}... (GDPR Art. 32 verified)"
            )
        except AssertionError as e:
            logger.error(f"Goal integrity verification failed for session {session_id}: {e}")
            raise  # Fail-closed: do NOT continue on hash mismatch
        except Exception as e:
            logger.error(f"Failed to restore GoalContext for session {session_id}: {e}")
            raise

        # Update session with restored goal_context
        session = self.get_session(session_id)
        if session:
            session["goal_context"] = goal_context.to_dict()
            session["task_id"] = checkpoint_data.get("task_id")
            session["goal_restored_at"] = datetime.utcnow().isoformat()
            session_file = tenant_session_dir(self.tenant_id, session_id) / "session.json"
            try:
                session_file.write_text(json.dumps(session, indent=2))
                self.open_sessions[session_id] = session
            except Exception as e:
                logger.error(f"Failed to persist resumed goal context for session {session_id}: {e}")
                raise

        # Audit log: goal_context_restored (GDPR Art. 30)
        self._audit_log_goal_restored(session_id, checkpoint_data.get("task_id"), goal_context)

        return {
            "session_id": session_id,
            "task_id": checkpoint_data.get("task_id"),
            "goal_context": goal_context.to_dict(),
            "integrity_verified": True,
        }

    def _audit_log_goal_initialized(
        self,
        session_id: str,
        task_id: str,
        goal_context: GoalContext,
    ) -> None:
        """Log goal initialization to audit trail (GDPR Art. 30).

        Args:
            session_id: Session ID
            task_id: Task ID
            goal_context: Initialized GoalContext
        """
        audit_event = {
            "event_type": "goal_context.initialized",
            "tenant_id": self.tenant_id,
            "session_id": session_id,
            "task_id": task_id,
            "goal_hash": goal_context.goal_hash,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        logger.info(f"AUDIT: {audit_event}")
        if self.hub:
            try:
                self.hub.publish_event("goal_context.initialized", audit_event)
            except Exception as e:
                logger.error(f"Failed to publish goal_context.initialized event: {e}")

    def _audit_log_goal_restored(
        self,
        session_id: str,
        task_id: str,
        goal_context: GoalContext,
    ) -> None:
        """Log goal restoration to audit trail (GDPR Art. 30, 32).

        Args:
            session_id: Session ID
            task_id: Task ID
            goal_context: Restored GoalContext (integrity verified)
        """
        audit_event = {
            "event_type": "goal_context.restored",
            "tenant_id": self.tenant_id,
            "session_id": session_id,
            "task_id": task_id,
            "goal_hash": goal_context.goal_hash,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        logger.info(f"AUDIT: {audit_event}")
        if self.hub:
            try:
                self.hub.publish_event("goal_context.restored", audit_event)
            except Exception as e:
                logger.error(f"Failed to publish goal_context.restored event: {e}")

    def shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("SessionManager shutdown")
