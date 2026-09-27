"""
Workflow Background Runner — Always non-blocking workflow execution.

Ensures workflows start in background mode, never blocking the calling session.
Caller receives async ack; workflow result is available via status endpoint or callback.

Part of ADR-2083: Non-Interactive Bridge Autonomy Design.
"""

import uuid
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

from corvin_operator.bridges.autonomy_detector import (
    detect_autonomy_mode,
    BridgeAutonomyMode,
    get_bridge_type,
)


logger = logging.getLogger(__name__)


@dataclass
class WorkflowStartAck:
    """Acknowledgment for a workflow start request."""
    run_id: str                    # Unique workflow run ID
    status: str                    # "queued" | "running" | "blocked"
    bridge_type: str               # Which bridge started this
    autonomy_mode: str             # "background" (always)
    created_at: str                # ISO timestamp
    message: str                   # Human-readable ack message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "bridge_type": self.bridge_type,
            "autonomy_mode": self.autonomy_mode,
            "created_at": self.created_at,
            "message": self.message,
        }


class WorkflowBackgroundRunner:
    """
    Start workflows in background (always non-blocking).

    **Contract:**
    - Workflow starts immediately in background
    - Caller receives async acknowledgment (run_id + status)
    - Caller does NOT wait for workflow completion
    - Workflow result is queried separately (via run_id)
    - All execution is audit-logged
    """

    def __init__(self):
        self.bridge_type = get_bridge_type().value
        self.autonomy_mode = detect_autonomy_mode().value

    def start(
        self,
        script: str,
        args: Optional[Dict[str, Any]] = None,
        description: str = ""
    ) -> WorkflowStartAck:
        """
        Start a workflow in background mode (non-blocking).

        Args:
            script: Workflow script content (or script path)
            args: Optional input args passed to the workflow
            description: Human-readable description (for audit)

        Returns:
            WorkflowStartAck with run_id + status
            (Caller should NOT wait for workflow to complete)
        """
        # Generate unique run ID
        run_id = f"wf_{uuid.uuid4().hex[:8]}"

        logger.info(
            f"Starting workflow in background: run_id={run_id}, "
            f"bridge={self.bridge_type}, autonomy_mode={self.autonomy_mode}"
        )

        # Launch workflow in background (implementation detail — assumed to work)
        # In real code, this would spawn a subprocess, systemd unit, or queue to job processor
        try:
            self._launch_background_task(run_id, script, args, description)
            status = "queued"
            ack_message = self._get_ack_message(run_id, status)
        except Exception as e:
            logger.error(f"Failed to queue workflow {run_id}: {e}")
            status = "blocked"
            ack_message = f"Workflow konnte nicht gestartet werden: {str(e)}"

        # Build acknowledgment
        ack = WorkflowStartAck(
            run_id=run_id,
            status=status,
            bridge_type=self.bridge_type,
            autonomy_mode=self.autonomy_mode,
            created_at=datetime.utcnow().isoformat() + "Z",
            message=ack_message,
        )

        # Audit log
        self._audit_log_workflow_start(ack, description)

        return ack

    def _launch_background_task(
        self,
        run_id: str,
        script: str,
        args: Optional[Dict] = None,
        description: str = ""
    ) -> None:
        """
        Launch workflow as background task.

        Implementation: would integrate with:
        - systemd timer (for recurring workflows)
        - background job queue (for async execution)
        - subprocess with nohup (for simple cases)

        For now, this is a stub that logs the intention.
        """
        logger.info(
            f"Queue workflow to background processor: {run_id}, "
            f"args={args}, description={description}"
        )
        # Real implementation would interact with a job queue or scheduler

    def _get_ack_message(self, run_id: str, status: str) -> str:
        """Generate bridge-specific acknowledgment message."""
        bridge = self.bridge_type.lower()

        if bridge == "discord":
            return (
                f"Workflow gestartet: `{run_id}`\n"
                f"Status: läuft im Hintergrund\n"
                f"(Benachrichtigung kommt, wenn fertig)"
            )
        elif bridge == "slack":
            return (
                f"Workflow started: `{run_id}`\n"
                f"Status: running in background\n"
                f"(Notification will arrive when complete)"
            )
        else:
            return (
                f"Workflow: {run_id}\n"
                f"Autonomy: background\n"
                f"(Result will be available via status query)"
            )

    def _audit_log_workflow_start(self, ack: WorkflowStartAck, description: str) -> None:
        """Audit log the workflow start request."""
        logger.info(
            f"AUDIT: workflow_started, "
            f"run_id={ack.run_id}, "
            f"status={ack.status}, "
            f"bridge={ack.bridge_type}, "
            f"description={description}"
        )
        # Real implementation would write to audit chain (ADR-0232)

    @staticmethod
    def get_workflow_result(run_id: str) -> Optional[Dict[str, Any]]:
        """
        Query workflow result by run_id.

        This is called AFTER the workflow completes (asynchronously).
        Can be called from a different session than the one that started it.
        """
        logger.debug(f"Querying workflow result: {run_id}")
        # Real implementation would fetch from workflow state store
        # For now, return a stub
        return None


def format_workflow_ack_for_user(ack: WorkflowStartAck) -> str:
    """Format WorkflowStartAck for display to user."""
    return ack.message
