"""TaskBrain: Main orchestration engine for autonomous task management.

ADR-0347: Brain Subsystem Hub Architecture
CONCEPT-0009: Autonomous Task Orchestration
"""

import asyncio
import logging
from typing import Dict, Any

from .hub import SubsystemHub

logger = logging.getLogger(__name__)


class TaskBrain:
    """Central orchestration engine for long-running tasks.

    Manages:
    - Subsystem registration and lifecycle
    - Event flow between subsystems
    - Task scheduling and execution
    """

    def __init__(
        self,
        poll_interval_s: float = 5.0,
        max_event_queue_size: int = 10000,
    ):
        self.poll_interval_s = poll_interval_s
        self.hub = SubsystemHub(max_event_queue_size=max_event_queue_size)
        self._tasks: Dict[str, Any] = {}

    def register_subsystem(self, subsystem):
        """Register a subsystem with the brain."""
        self.hub.register_subsystem(subsystem)

    async def run_forever(self):
        """Main orchestration loop."""
        logger.info("TaskBrain starting")
        await self.hub.run_forever(poll_interval_s=self.poll_interval_s)

    def stop(self):
        """Stop the brain."""
        logger.info("TaskBrain stopping")
        self.hub.stop()

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("TaskBrain shutting down")
        for name in list(self.hub.subsystems.keys()):
            self.hub.unregister_subsystem(name)
        self.stop()
