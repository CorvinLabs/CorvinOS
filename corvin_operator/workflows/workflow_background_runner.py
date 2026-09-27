"""
Workflow Background Runner — Always non-blocking workflow execution.

Ensures workflows start in background mode, never blocking the calling session.
Caller receives async ack; workflow result is available via status endpoint or callback.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). Only
``scripts/adr2083_staging_validation.py`` and its E2E test import it.

There is no background executor behind it: ``_launch_background_task`` used
to log the intention and return, and ``start()`` then answered ``queued`` /
"Workflow started … notification will arrive" for a workflow that never ran,
with an "AUDIT:" log line standing in for the audit chain. It now fails
closed — every start is ``blocked`` with ``reason_code="not_implemented"`` —
and the attempt is recorded in the tenant's hash-chained audit log.

Part of ADR-2083: Non-Interactive Bridge Autonomy Design.
"""

import os
import uuid
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

from corvin_operator.bridges.autonomy_detector import (
    detect_autonomy_mode,
    get_bridge_type,
)

#: Content-free fields of the chain record written for every start attempt.
_AUDIT_EVENT = "workflow.background_start"
_AUDIT_FIELDS = frozenset({"run_id", "status", "bridge_type", "reason_code", "tenant_id"})


logger = logging.getLogger(__name__)


@dataclass
class WorkflowStartAck:
    """Acknowledgment for a workflow start request."""
    run_id: str                    # Unique workflow run ID
    status: str                    # "queued" | "running" | "blocked"
    bridge_type: str               # Which bridge started this
    autonomy_mode: str             # detected bridge autonomy mode
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
        reason_code = ""
        try:
            self._launch_background_task(run_id, script, args, description)
            status = "queued"
            ack_message = self._get_ack_message(run_id, status)
        except NotImplementedError:
            status = "blocked"
            reason_code = "not_implemented"
            ack_message = ("Workflow could not be started: no background workflow "
                           "executor is wired on this install.")
        except Exception as e:
            logger.error(f"Failed to queue workflow {run_id}: {e}")
            status = "blocked"
            reason_code = "launch_failed"
            ack_message = f"Workflow could not be started: {type(e).__name__}"

        # Build acknowledgment
        ack = WorkflowStartAck(
            run_id=run_id,
            status=status,
            bridge_type=self.bridge_type,
            autonomy_mode=self.autonomy_mode,
            created_at=datetime.utcnow().isoformat() + "Z",
            message=ack_message,
        )

        # Audit log (hash-chained, content-free)
        self._audit_log_workflow_start(ack, reason_code)

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

        None of those exists: this raises instead of pretending the workflow
        was queued.
        """
        raise NotImplementedError("no background workflow executor is wired")

    def _get_ack_message(self, run_id: str, status: str) -> str:
        """Generate bridge-specific acknowledgment message."""
        bridge = self.bridge_type.lower()

        if bridge == "discord":
            return (
                f"Workflow started: `{run_id}`\n"
                f"Status: running in background\n"
                f"(Notification will arrive when complete)"
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

    def _audit_log_workflow_start(self, ack: WorkflowStartAck, reason_code: str) -> None:
        """Record the start attempt in the tenant's hash-chained audit log.

        Goes through the bridge core writer (``audit.audit_event`` resolves the
        chain via ``tenant_audit_chain()``). The description is free text and
        never enters the chain. Best-effort: a missing writer is logged.
        """
        tenant_id = (os.environ.get("CORVIN_TENANT_ID") or "").strip() or "_default"
        try:
            try:
                from corvin_operator.bridges.shared.audit import audit_event  # noqa: PLC0415
            except ImportError:
                from audit import audit_event  # type: ignore  # noqa: PLC0415
        except ImportError:
            logger.error("core audit writer unavailable — %s NOT chained", _AUDIT_EVENT)
            return
        try:
            from forge.security_events import register_event_allowlist  # type: ignore  # noqa: PLC0415

            register_event_allowlist(_AUDIT_EVENT, _AUDIT_FIELDS)
        except Exception:  # noqa: BLE001
            pass
        try:
            audit_event(_AUDIT_EVENT, details={
                "run_id": ack.run_id,
                "status": ack.status,
                "bridge_type": ack.bridge_type,
                "reason_code": reason_code,
                "tenant_id": tenant_id,
            }, tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("audit emit failed for %s (%s)", _AUDIT_EVENT, type(exc).__name__)

    @staticmethod
    def get_workflow_result(run_id: str) -> Optional[Dict[str, Any]]:
        """
        Query workflow result by run_id.

        This is called AFTER the workflow completes (asynchronously).
        Can be called from a different session than the one that started it.
        """
        logger.debug(f"Querying workflow result: {run_id}")
        # No workflow state store exists (see module docstring): no run ever
        # has a result.
        return None


def format_workflow_ack_for_user(ack: WorkflowStartAck) -> str:
    """Format WorkflowStartAck for display to user."""
    return ack.message
