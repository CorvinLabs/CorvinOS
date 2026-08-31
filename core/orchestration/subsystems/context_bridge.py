"""Context Bridge subsystem: Manage session splits and memory transfer."""

import logging
from typing import Any, Dict, List

from .base import Subsystem

logger = logging.getLogger(__name__)


class ContextBridge(Subsystem):
    """Manage context continuity across session splits."""

    def __init__(
        self,
        checkpoint_interval_turns: int = 25,
        memory_tier_sizes: List[int] = None,
        max_checkpoints_per_task: int = 10,
    ):
        self.checkpoint_interval_turns = checkpoint_interval_turns
        self.memory_tier_sizes = memory_tier_sizes or [500, 2000, 8000]
        self.max_checkpoints_per_task = max_checkpoints_per_task
        self.checkpoints: Dict[str, List[Dict[str, Any]]] = {}
        self.turn_count = 0

    @property
    def name(self) -> str:
        return "context_bridge"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to session and memory events."""
        self.hub = hub
        hub.subscribe("task_started", self.on_task_started)
        hub.subscribe("session_split", self.on_session_split)
        logger.info("ContextBridge started")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "task_started":
            task_id = event_data.get("task_id")
            if task_id:
                self.checkpoints[task_id] = []
                self.turn_count = 0

        elif event_name == "session_split":
            await self._transfer_context(event_data)

    async def on_task_started(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Task started."""
        task_id = event_data.get("task_id")
        if task_id:
            self.checkpoints[task_id] = []

    async def on_session_split(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Session split detected."""
        await self._transfer_context(event_data)

    async def _transfer_context(self, event_data: Dict[str, Any]) -> None:
        """Transfer memory to new session."""
        task_id = event_data.get("task_id")
        if not task_id:
            return

        # Create checkpoint
        checkpoint = {
            "timestamp": event_data.get("timestamp"),
            "memory": event_data.get("memory", {}),
            "tier": "standard",
        }

        if task_id not in self.checkpoints:
            self.checkpoints[task_id] = []

        if len(self.checkpoints[task_id]) < self.max_checkpoints_per_task:
            self.checkpoints[task_id].append(checkpoint)

        self.publish_event(
            "context_transferred",
            {
                "task_id": task_id,
                "checkpoint_count": len(self.checkpoints[task_id]),
            },
        )

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle context queries."""
        if request_type == "create_checkpoint":
            task_id = kwargs.get("task_id")
            memory = kwargs.get("memory", {})

            if task_id not in self.checkpoints:
                self.checkpoints[task_id] = []

            checkpoint = {
                "timestamp": kwargs.get("timestamp"),
                "memory": memory,
                "tier": "standard",
            }

            if len(self.checkpoints[task_id]) < self.max_checkpoints_per_task:
                self.checkpoints[task_id].append(checkpoint)
                return {"success": True, "checkpoint_id": len(self.checkpoints[task_id])}

            return {"success": False, "reason": "max checkpoints reached"}

        elif request_type == "retrieve_checkpoint":
            task_id = kwargs.get("task_id")
            checkpoint_id = kwargs.get("checkpoint_id", -1)

            if task_id in self.checkpoints and len(self.checkpoints[task_id]) > checkpoint_id:
                return self.checkpoints[task_id][checkpoint_id]

            return None

        elif request_type == "list_checkpoints":
            task_id = kwargs.get("task_id")
            return self.checkpoints.get(task_id, [])

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("ContextBridge shutdown")
