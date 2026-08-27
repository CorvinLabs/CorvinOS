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
        self._active_phases = {}  # task_id → {phase_id, start_time}

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
        self._active_phases[task_id] = {
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
            del self._active_phases[task_id]

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

        while not phase_task.done():
            now = datetime.now()

            # Send heartbeat if interval passed
            if now >= heartbeat_deadline:
                elapsed = (now - start_time).total_seconds()
                remaining = timeout_s - elapsed
                await on_heartbeat({
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
                await on_stall({
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
                    await on_heartbeat({
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


# Singleton
_heartbeat = None


def get_task_heartbeat() -> TaskHeartbeat:
    global _heartbeat
    if _heartbeat is None:
        _heartbeat = TaskHeartbeat()
    return _heartbeat
