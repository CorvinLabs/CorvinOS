"""TaskHeartbeat: Periodic status updates for long-running phases (ADR-0377)."""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict
from dataclasses import dataclass


@dataclass
class HeartbeatConfig:
    """Heartbeat configuration."""
    interval_s: int = 300          # Every 5 minutes
    stall_threshold_s: int = 900   # 15 minutes = stall
    timeout_grace_s: int = 60      # Warn 1 minute before timeout


class TaskHeartbeat:
    """
    Sends periodic status updates for long-running phases.
    Detects stalls (phase running too long, no progress).
    """

    def __init__(self, config: Optional[HeartbeatConfig] = None):
        self.config = config or HeartbeatConfig()
        self._active_phases = {}  # (task_id, phase_id) → {phase_id, start_time}

    async def monitor_phase(self, task_id: str, phase_id: str,
                           phase_handler: Callable,
                           timeout_s: int,
                           on_heartbeat: Callable,
                           on_stall: Callable) -> Dict:
        """
        Monitor a phase execution with heartbeat + stall detection.

        Runs phase_handler while sending periodic heartbeats.
        If phase takes too long, notify user via on_stall.
        """
        start_time = datetime.now()
        # Keyed by (task_id, phase_id), NOT task_id alone. The orchestrator runs
        # every ready phase of a task concurrently via asyncio.gather, so a
        # task-only key made concurrent phases overwrite each other and the
        # second one to finish raised KeyError out of the `finally` below —
        # turning a SUCCESSFUL phase into a failure.
        key = (task_id, phase_id)
        self._active_phases[key] = {
            "phase_id": phase_id,
            "start_time": start_time,
            "timeout_s": timeout_s,
        }

        try:
            # Run phase with heartbeat monitor
            result = await asyncio.wait_for(
                self._monitor_with_heartbeat(
                    task_id, phase_id, phase_handler, timeout_s,
                    on_heartbeat, on_stall
                ),
                timeout=timeout_s + self.config.timeout_grace_s
            )
            return result
        finally:
            # pop, not del: idempotent under any unwind path.
            self._active_phases.pop(key, None)

    async def _monitor_with_heartbeat(self, task_id: str, phase_id: str,
                                      phase_handler: Callable,
                                      timeout_s: int,
                                      on_heartbeat: Callable,
                                      on_stall: Callable) -> Dict:
        """
        Run phase while emitting heartbeat + stall detection.
        """
        start_time = datetime.now()
        timeout_deadline = start_time + timedelta(seconds=timeout_s)
        heartbeat_deadline = start_time + timedelta(seconds=self.config.interval_s)
        stall_deadline = start_time + timedelta(seconds=self.config.stall_threshold_s)
        warn_deadline = timeout_deadline - timedelta(seconds=self.config.timeout_grace_s)

        phase_task = asyncio.create_task(phase_handler())
        try:
            return await self._heartbeat_loop(
                phase_task, task_id, phase_id, timeout_s, start_time,
                timeout_deadline, heartbeat_deadline, stall_deadline,
                warn_deadline, on_heartbeat, on_stall,
            )
        except (asyncio.CancelledError, asyncio.TimeoutError):
            # The outer wait_for gave up. Without this the phase coroutine
            # keeps running detached FOREVER — the "timed out" phase is still
            # burning the engine, the budget and the CPU, invisibly.
            phase_task.cancel()
            try:
                await phase_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            raise

    async def _heartbeat_loop(self, phase_task, task_id: str, phase_id: str,
                              timeout_s: int, start_time, timeout_deadline,
                              heartbeat_deadline, stall_deadline, warn_deadline,
                              on_heartbeat: Callable, on_stall: Callable) -> Dict:
        """Emit heartbeats until *phase_task* finishes; return its result."""
        while not phase_task.done():
            now = datetime.now()

            # Send heartbeat if interval passed
            if now >= heartbeat_deadline:
                elapsed = (now - start_time).total_seconds()
                remaining = timeout_s - elapsed
                await self._safe(on_heartbeat, {
                    "task_id": task_id,
                    "phase_id": phase_id,
                    "elapsed_s": int(elapsed),
                    "remaining_s": int(max(0, remaining)),
                    "status": "running",
                })
                heartbeat_deadline = now + timedelta(seconds=self.config.interval_s)

            # Detect stall (running too long)
            if now >= stall_deadline and now < warn_deadline:
                elapsed = (now - start_time).total_seconds()
                await self._safe(on_stall, {
                    "task_id": task_id,
                    "phase_id": phase_id,
                    "elapsed_s": int(elapsed),
                    "threshold_s": self.config.stall_threshold_s,
                    "reason": f"Phase running {int(elapsed)}s (threshold: {self.config.stall_threshold_s}s)",
                })
                stall_deadline = datetime.max  # Only notify once

            # Warn about approaching timeout
            if now >= warn_deadline:
                remaining = (timeout_deadline - now).total_seconds()
                if remaining > 0:
                    await self._safe(on_heartbeat, {
                        "task_id": task_id,
                        "phase_id": phase_id,
                        "elapsed_s": int((now - start_time).total_seconds()),
                        "remaining_s": int(remaining),
                        "status": "warning_timeout_approaching",
                    })
                warn_deadline = datetime.max  # Only warn once

            # Small sleep to avoid busy-waiting
            await asyncio.sleep(1)

        # Phase completed (or raised exception)
        return await phase_task

    @staticmethod
    async def _safe(callback: Callable, payload: Dict) -> None:
        """Run a notification callback without letting it kill the phase.

        A raising or hanging notifier used to propagate straight out of the
        heartbeat loop and fail the phase it was merely reporting on. Bounded
        as well as guarded: a notifier that blocks forever would otherwise
        stall the loop that is supposed to detect stalls.
        """
        try:
            result = callback(payload)
            if asyncio.iscoroutine(result):
                await asyncio.wait_for(result, timeout=30)
        except Exception:  # noqa: BLE001 — includes TimeoutError
            pass


# Singleton
_heartbeat = None


def get_task_heartbeat() -> TaskHeartbeat:
    global _heartbeat
    if _heartbeat is None:
        _heartbeat = TaskHeartbeat()
    return _heartbeat
