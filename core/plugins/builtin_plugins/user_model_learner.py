"""User Model Learner Plugin — Learns user preferences and patterns.

Category: memory | Type: learning_backend
Tracks user behavior patterns and preference evolution.
"""

import threading
from typing import Optional, Any


class UserModelLearner:
    """Plugin: learns user preferences and patterns."""

    def __init__(self):
        """Initialize learner."""
        self._user_models: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._initialized = False

    async def initialize(self, ctx) -> bool:
        """Initialize the plugin."""
        self._initialized = True
        return True

    async def execute(self, op: str, **kwargs) -> dict:
        """Execute a learning operation.

        Operations:
        - record_interaction: Record user interaction
        - get_user_model: Retrieve learned model
        - update_preferences: Update user preferences
        """
        if not self._initialized:
            return {"success": False, "error": "not initialized"}

        op_lower = op.lower()

        if op_lower == "record_interaction":
            user_id = kwargs.get("user_id")
            interaction = kwargs.get("interaction", {})

            try:
                with self._lock:
                    if user_id not in self._user_models:
                        self._user_models[user_id] = {"interactions": []}
                    self._user_models[user_id]["interactions"].append(interaction)
                return {"success": True, "recorded": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "get_user_model":
            user_id = kwargs.get("user_id")

            try:
                with self._lock:
                    model = self._user_models.get(user_id, {})
                return {"success": True, "model": model}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "update_preferences":
            user_id = kwargs.get("user_id")
            preferences = kwargs.get("preferences", {})

            try:
                with self._lock:
                    if user_id not in self._user_models:
                        self._user_models[user_id] = {}
                    self._user_models[user_id]["preferences"] = preferences
                return {"success": True, "updated": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": f"unknown operation: {op}"}

    async def health_check(self) -> bool:
        """Check plugin health."""
        return self._initialized

    async def shutdown(self) -> None:
        """Shutdown the plugin."""
        with self._lock:
            self._user_models.clear()
        self._initialized = False
