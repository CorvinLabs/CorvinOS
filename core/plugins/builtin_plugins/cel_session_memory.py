"""CEL Session Memory Plugin — Context expression language session state.

Category: memory | Type: session_backend
Manages CEL (context expression language) session state and ephemeral memory.
"""

import threading
from typing import Optional, Any


class CELSessionMemory:
    """Plugin: manages CEL session state."""

    def __init__(self):
        """Initialize session memory."""
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._initialized = False

    async def initialize(self, ctx) -> bool:
        """Initialize the plugin."""
        self._initialized = True
        return True

    async def execute(self, op: str, **kwargs) -> dict:
        """Execute a session operation.

        Operations:
        - create_session: Create new CEL session
        - get_session: Retrieve session state
        - update_session: Update session values
        - delete_session: Remove session
        """
        if not self._initialized:
            return {"success": False, "error": "not initialized"}

        op_lower = op.lower()

        if op_lower == "create_session":
            session_id = kwargs.get("session_id")
            try:
                with self._lock:
                    self._sessions[session_id] = {}
                return {"success": True, "session_id": session_id}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "get_session":
            session_id = kwargs.get("session_id")
            try:
                with self._lock:
                    state = self._sessions.get(session_id, {})
                return {"success": True, "state": state}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "update_session":
            session_id = kwargs.get("session_id")
            updates = kwargs.get("updates", {})
            try:
                with self._lock:
                    if session_id in self._sessions:
                        self._sessions[session_id].update(updates)
                    else:
                        self._sessions[session_id] = updates
                return {"success": True, "updated": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "delete_session":
            session_id = kwargs.get("session_id")
            try:
                with self._lock:
                    self._sessions.pop(session_id, None)
                return {"success": True, "deleted": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": f"unknown operation: {op}"}

    async def health_check(self) -> bool:
        """Check plugin health."""
        return self._initialized

    async def shutdown(self) -> None:
        """Shutdown the plugin."""
        with self._lock:
            self._sessions.clear()
        self._initialized = False
