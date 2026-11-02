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

# The only two values os.kill delivers as a signal on Windows; every other one terminates.
_WIN_CTRL_EVENTS = tuple(getattr(signal, n) for n in ("CTRL_C_EVENT", "CTRL_BREAK_EVENT")
                         if hasattr(signal, n))


def kill_tree_windows(pid: int) -> bool:
    """Windows: end *pid* AND every descendant (``taskkill /T /F``). True when it succeeded.

    ``TerminateProcess`` (``os.kill``/``Popen.kill``) reaches only the one pid, so a CLI
    started through a ``.cmd`` shim (cmd.exe -> node.exe) or one with live tool children
    leaves them running. False on POSIX, on a pid that is already gone, or when taskkill
    itself fails — the caller then falls back to its single-process kill.
    """
    if os.name != "nt" or not pid or int(pid) <= 0:
        return False
    import subprocess  # noqa: PLC0415 - only needed on this path

    try:
        rc = subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))],
                            capture_output=True, timeout=10, check=False).returncode
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0


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
    try:
        name = signal.Signals(sig).name
    except ValueError:                      # a number this platform has no name for (Windows: 9)
        name = f"SIG{int(sig)}"
    record_signal(pid=pid, signal_name=name, reason=reason,
                  tenant_id=tenant_id, sid_fingerprint=sid_fingerprint)
    if pid:
        # Windows: os.kill with anything but CTRL_C/CTRL_BREAK is TerminateProcess on this ONE
        # pid — there is no graceful SIGTERM there either way. When the CLI runs through an npm
        # .cmd shim that pid is cmd.exe and the real engine (node.exe) kept running after the
        # "stop". End the whole tree instead; os.kill stays the fallback.
        if os.name == "nt" and sig not in _WIN_CTRL_EVENTS and kill_tree_windows(int(pid)):
            return
        os.kill(int(pid), sig)


def hard_kill(proc, *, reason: str, tenant_id: str = "_default",
              sid_fingerprint: str | None = None) -> None:
    """Record a SIGKILL, then end *proc* the way this platform can.

    POSIX: ``os.kill(pid, SIGKILL)``. Windows has no ``signal.SIGKILL`` — until 2026-10-21 the
    console's stop path raised ``AttributeError`` there, masked the original error and left
    the CLI running; ``Popen.kill()`` (TerminateProcess) is the equivalent. The record carries
    the name ``SIGKILL`` on both so an exit can be attributed the same way.
    """
    pid = getattr(proc, "pid", None)
    record_signal(pid=pid, signal_name="SIGKILL", reason=reason,
                  tenant_id=tenant_id, sid_fingerprint=sid_fingerprint)
    sigkill = getattr(signal, "SIGKILL", None)
    if sigkill is not None and pid:
        os.kill(int(pid), sigkill)
    elif os.name == "nt" and pid and kill_tree_windows(int(pid)):
        return                              # proc.kill() alone missed the shim's node.exe
    else:
        proc.kill()
