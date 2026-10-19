"""Record every signal this process sends to an agent CLI (PLAN bg-task robustness, P0).

Measured 2026-10-18: tasks ended ``exit 143`` (SIGTERM) with no restart, no cancel and no
supervisor action in the same second, and nothing in our logs named the sender. A 143
with no record of who sent it is indistinguishable from a kill by someone else, so every
place that sends SIGTERM/SIGKILL to a CLI writes one audit record **before** the signal.
An exit 143 without a matching record in the window is then classed as external.

The record goes through the same audit writer as the console (``console.action_performed``,
allow-listed in ``security_events``), into the tenant chain. The record is best-effort: a
failing audit write must never block the kill itself, so errors are logged and swallowed.
"""
from __future__ import annotations

import logging
import os
import signal
from typing import Any

logger = logging.getLogger(__name__)

ACTION = "process.signal_sent"


def record_signal(
    *,
    pid: int | None,
    signal_name: str,
    reason: str,
    tenant_id: str = "_default",
    sid_fingerprint: str | None = None,
) -> None:
    """Write one audit record for a signal about to be sent to *pid*. Never raises."""
    try:
        from forge import security_events  # type: ignore[import]
        from forge.paths import tenant_audit_chain  # type: ignore[import]

        details: dict[str, Any] = {
            "action": ACTION,
            "target_kind": "cli_process",
            "target_id": str(pid or 0),
            "tenant_id": tenant_id,
            "sid_fingerprint": sid_fingerprint or "unattributed",
            "trigger": f"{signal_name}:{reason}",
        }
        security_events.write_event(
            tenant_audit_chain(tenant_id), "console.action_performed",
            details=details, severity="INFO",
        )
    except Exception as exc:  # noqa: BLE001 - logging must never block the kill
        logger.warning("process_signals: could not record %s to pid %s (%s): %s",
                       signal_name, pid, reason, type(exc).__name__)


def send_signal(
    pid: int | None,
    sig: int,
    *,
    reason: str,
    tenant_id: str = "_default",
    sid_fingerprint: str | None = None,
) -> None:
    """Record, then send *sig* to *pid* (process-group aware callers still use os.killpg)."""
    name = signal.Signals(sig).name
    record_signal(pid=pid, signal_name=name, reason=reason,
                  tenant_id=tenant_id, sid_fingerprint=sid_fingerprint)
    if pid:
        os.kill(int(pid), sig)
