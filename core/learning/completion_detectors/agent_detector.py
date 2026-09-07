"""Phase 1/2: AgentDetector — reuses existing completion_notify.py queue.

Agent background tasks (/task) are spawned detached and register with
completion_notify.py queue. This detector polls the queue and emits
CompletionEvent.

No new implementation needed — wraps existing completion_notify.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from .completion_event import (
    CompletionEvent,
    CompletionStatus,
    CompletionTaskType,
)
from .detector_base import BaseCompletionDetector

_log = logging.getLogger("core.learning.completion_detectors.agent")


class AgentDetector(BaseCompletionDetector):
    """Polls completion_notify.py queue for Agent task completions.

    Agent tasks (/task <instruction>) are registered in completion_notify.py
    with status PENDING. When bg_task_worker finishes, it marks DONE and calls
    deliver_ready(). This detector reads the DONE queue and emits CompletionEvent.
    """

    def __init__(self, config: dict, audit_backend, corvin_home: str = "~/.corvin"):
        super().__init__(config)
        self.audit_backend = audit_backend
        self.corvin_home = Path(corvin_home).expanduser()
        self.pending_dir = self.corvin_home / "pending_notifications"

    async def poll_once(self):
        """Poll pending_notifications for DONE tasks."""
        if not self.pending_dir.exists():
            _log.debug(f"Pending directory not found: {self.pending_dir}")
            return

        # Read all DONE files (marked by completion_notify.py)
        for done_file in sorted(self.pending_dir.glob("done_*.json")):
            try:
                notification = json.loads(done_file.read_text())

                task_id = notification.get("task_id")
                status_str = notification.get("status", "done")
                text = notification.get("text", "")
                ok = notification.get("ok", True)

                if not task_id:
                    continue

                # Dedup check
                final_status = CompletionStatus.COMPLETE if ok else CompletionStatus.FAILED
                if not self._has_transition(task_id, final_status.value):
                    continue

                # Emit CompletionEvent
                await self._emit_agent_completion(
                    task_id=task_id,
                    status=final_status,
                    output_summary=text,
                    metadata=notification,
                )

                # Mark as processed (safe to delete after emit succeeds)
                try:
                    done_file.unlink()
                except OSError:
                    pass

            except Exception as e:
                _log.error(f"Error processing {done_file}: {e}", exc_info=True)

    async def _emit_agent_completion(
        self,
        task_id: str,
        status: CompletionStatus,
        output_summary: str,
        metadata: Dict[str, Any],
    ) -> bool:
        """Emit CompletionEvent for Agent task."""
        tenant_id = metadata.get("tenant_id", "_default")

        event = CompletionEvent(
            task_id=task_id,
            task_type=CompletionTaskType.AGENT,
            status=status,
            duration_sec=metadata.get("duration_sec", 0.0),
            phase_reached=None,  # Agents have no phases
            output_summary=output_summary,
            metadata={
                "agent_result": output_summary,
                "instruction": metadata.get("instruction", ""),
                "origin_channel": metadata.get("channel", "unknown"),
            },
            tenant_id=tenant_id,
            origin={"channel": metadata.get("channel", "unknown")},
        )

        try:
            event.validate()
        except ValueError as e:
            _log.error(f"Invalid CompletionEvent: {e}")
            return False

        try:
            success = await self._emit_event(event, self.audit_backend)
            if success:
                _log.info(f"Emitted completion for agent task {task_id}")
                return True
            else:
                _log.error(f"Audit emit failed for {task_id}")
                return False

        except Exception as e:
            _log.error(f"Error emitting agent completion: {e}", exc_info=True)
            return False
