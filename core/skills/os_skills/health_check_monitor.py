"""HealthCheckMonitor — K=3 latency-based escalation detection (ADR-2084).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

``p99_projection`` is a HEURISTIC extrapolation of one task's elapsed time
(elapsed x 1.5), not a measured p99 of anything.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class TaskHealthState:
    """Track health of a running task."""
    task_id: str
    model: str
    start_time: float
    sla_target_ms: int = 600
    escalated: bool = False
    p99_projection: Optional[float] = None
    pinned: bool = False  # operator pin: never escalated


class HealthCheckMonitor:
    """Monitor running tasks for latency SLA; trigger escalation if p99 >600ms (K=3)."""

    def __init__(self, sla_target_ms: int = 600, check_interval_ms: int = 100):
        self.sla_target_ms = sla_target_ms
        self.check_interval_ms = check_interval_ms
        self.running_tasks: Dict[str, TaskHealthState] = {}
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.health_check_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start background health check loop."""
        self.health_check_task = asyncio.create_task(self._health_check_loop())

    async def stop(self):
        """Stop health check loop and wait until it has actually stopped.

        Cancelling without awaiting returned while the loop was still running
        (the task was only "cancelling").
        """
        if self.health_check_task:
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                pass

    def register_task(self, task_id: str, model: str, sla_target_ms: Optional[int] = None,
                      pinned: bool = False):
        """Register task for health monitoring (``pinned`` tasks are never escalated)."""
        target = sla_target_ms or self.sla_target_ms
        self.running_tasks[task_id] = TaskHealthState(
            task_id=task_id,
            model=model,
            start_time=time.time(),
            sla_target_ms=target,
            pinned=pinned,
        )

    def unregister_task(self, task_id: str):
        """Unregister task (end of execution)."""
        self.running_tasks.pop(task_id, None)

    async def _health_check_loop(self):
        """Background: check all running tasks for escalation."""
        while True:
            try:
                await asyncio.sleep(self.check_interval_ms / 1000.0)
                await self._check_all_tasks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}", exc_info=True)

    async def _check_all_tasks(self):
        """Check each task for escalation need."""
        current_time = time.time()

        for task_id, task_state in list(self.running_tasks.items()):
            elapsed_ms = (current_time - task_state.start_time) * 1000
            check_threshold = task_state.sla_target_ms * 0.5

            if task_state.pinned:
                continue  # an operator pin is never overridden
            if elapsed_ms > check_threshold and not task_state.escalated:
                p99_projection = self._estimate_p99(elapsed_ms)
                task_state.p99_projection = p99_projection

                # Compare against THIS task's SLA — a literal 600 ignored
                # every per-task ``sla_target_ms``.
                if p99_projection > task_state.sla_target_ms:
                    await self._trigger_escalation(task_id, task_state, p99_projection)

    def _estimate_p99(self, elapsed_ms: float) -> float:
        """Estimate final p99 latency (heuristic: 50% time → 150% final)."""
        return elapsed_ms * 1.5

    async def _trigger_escalation(self, task_id: str, task_state: TaskHealthState, p99_projection: float):
        """Trigger escalation: old_model → new_model."""
        old_model = task_state.model
        if task_state.pinned:
            return  # an operator pin is never overridden
        new_model = self._escalate_model(old_model)
        if new_model == old_model:
            # Nothing to escalate to (top tier / unknown id): recording an
            # "escalation" opus → opus would be a fabricated event.
            task_state.escalated = True
            return

        task_state.escalated = True
        task_state.model = new_model

        event = {
            "event_type": "escalation_triggered",
            "task_id": task_id,
            "model_old": old_model,
            "model_new": new_model,
            "reason": "latency_sla_risk",
            "elapsed_ms": (time.time() - task_state.start_time) * 1000,
            "p99_projection": p99_projection,
            "timestamp": datetime.utcnow().isoformat(),
        }

        try:
            self.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(f"Event queue full: {task_id}")

        logger.info(f"Escalation: {old_model} → {new_model} (p99={p99_projection:.0f}ms)")

    def _escalate_model(self, current_model: str) -> str:
        """Escalate model: haiku→sonnet→opus (unknown ids are left unchanged)."""
        return {"haiku": "sonnet", "sonnet": "opus", "opus": "opus"}.get(current_model, current_model)

    async def get_escalation_events(self) -> List[Dict]:
        """Drain escalation events from queue."""
        events = []
        while not self.event_queue.empty():
            try:
                events.append(self.event_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events
