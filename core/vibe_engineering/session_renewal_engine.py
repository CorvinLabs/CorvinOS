"""SessionRenewerEngine: Automatic session renewal on token limit."""

from datetime import datetime
from typing import Optional, Dict
import asyncio

from .task_registry import TaskRegistryPersistence, get_default_registry
from .measurement import get_measurement_collector


class SessionRenewerEngine:
    """Handles automatic session renewal when token budget exhausted."""

    def __init__(self, registry: Optional[TaskRegistryPersistence] = None):
        self.registry = registry or get_default_registry()
        self.measurement = get_measurement_collector()
        self._session_stack = {}  # task_id → [session_ids]

    async def on_token_burn_trigger(self, task_id: str, old_session_id: str,
                                     task_state: Dict, context_essentials: Dict) -> str:
        """
        Token limit reached (95%). Create child session and resume.

        Returns: new_session_id
        """
        start_time = datetime.now()

        # Create checkpoint (immutable Original Context preserved)
        checkpoint = {
            "task_id": task_id,
            "old_session_id": old_session_id,
            "task_state": task_state,
            "context_essentials": context_essentials,
            "recovery_reason": "TOKEN_BURN_95%",
            "checkpoint_time": start_time.isoformat(),
        }

        # Generate new session ID
        import uuid
        new_session_id = f"session-{uuid.uuid4()}"

        # Track session hierarchy
        if task_id not in self._session_stack:
            self._session_stack[task_id] = []
        self._session_stack[task_id].append(new_session_id)

        # Record metric
        elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
        self.measurement.record_session_renewal_latency(task_id, elapsed_ms)

        return new_session_id

    def get_session_ancestry(self, task_id: str) -> list:
        """Get chain of sessions for a task."""
        return self._session_stack.get(task_id, [])
