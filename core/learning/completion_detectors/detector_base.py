"""Base class for all completion detectors — Phase 1-3.

Each detector (Agent, Workflow, Loop, Skill) polls/subscribes to a different
source and emits CompletionEvent to the unified event bus.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from .completion_event import CompletionEvent

_log = logging.getLogger("core.learning.completion_detectors")


class BaseCompletionDetector(ABC):
    """Abstract base for all completion detectors.

    Subclasses implement poll_once() or subscribe() depending on source type.
    All emit CompletionEvent via the unified audit/event system.
    """

    def __init__(self, config: dict):
        """Initialize detector.

        Args:
            config: {
                "poll_interval_sec": 3,
                "timeout_sec": 0.1,
                "max_retries": 3,
            }
        """
        self.config = config
        self.poll_interval_sec = config.get("poll_interval_sec", 3)
        self.timeout_sec = config.get("timeout_sec", 0.1)
        self.max_retries = config.get("max_retries", 3)

        # Dedup tracking: {task_id: last_status} to prevent duplicate emissions
        self._seen_transitions: dict[str, str] = {}

    async def run(self):
        """Main detector loop — runs forever until cancelled."""
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                _log.info(f"{self.__class__.__name__} cancelled")
                break
            except Exception as e:
                _log.error(f"{self.__class__.__name__} error: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval_sec)

    @abstractmethod
    async def poll_once(self):
        """Poll source once and emit CompletionEvent if terminal state reached.

        Subclasses implement this to:
        1. Check their source (file, event bus, queue, audit chain)
        2. Detect terminal state transition
        3. Emit CompletionEvent via audit_backend.emit()
        """
        pass

    def _has_transition(self, task_id: str, new_status: str) -> bool:
        """Check if this is a new status transition (for dedup)."""
        old_status = self._seen_transitions.get(task_id, "UNKNOWN")
        if old_status != new_status:
            self._seen_transitions[task_id] = new_status
            return True
        return False

    async def _emit_event(self, event: CompletionEvent, audit_backend) -> bool:
        """Emit CompletionEvent to audit chain (fail-closed).

        Returns:
            True if audit write succeeded, False if failed (notification blocked).
        """
        try:
            event.validate()
        except ValueError as e:
            _log.error(f"Invalid CompletionEvent: {e}")
            return False

        try:
            await audit_backend.emit(event)
            _log.info(f"Emitted CompletionEvent for {event.task_id}")
            return True
        except Exception as e:
            _log.error(f"Audit emit failed for {event.task_id}: {e}")
            # Fail-closed: notification is BLOCKED if audit fails
            return False
