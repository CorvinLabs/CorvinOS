"""TaskOrchestrator: DAG-based phase executor (ADR-0402)."""

from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional, Literal
import asyncio
import os
import logging
from datetime import datetime
from enum import Enum

from .task_registry import (
    TaskMetadata, PhaseMetadata, PhaseStatus, TaskStatus,
    TaskRegistryPersistence, get_default_registry
)

logger = logging.getLogger(__name__)

# Import notification router (optional, fail-gracefully if not available)
try:
    from .notification_router import NotificationRouter
    _notification_router = NotificationRouter()
except ImportError as e:
    logger.warning(
        f"[task_orchestrator] Failed to import NotificationRouter: {e}. "
        f"Task notifications will not be sent to Discord."
    )
    _notification_router = None
except Exception as e:
    logger.warning(
        f"[task_orchestrator] Failed to initialize NotificationRouter: {e}. "
        f"Task notifications will not be sent to Discord."
    )
    _notification_router = None

# Import task heartbeat for long-running phase monitoring
try:
    from .task_heartbeat import get_task_heartbeat
    _heartbeat = get_task_heartbeat()
except ImportError:
    _heartbeat = None


@dataclass(frozen=True)
class Phase:
    """Phase definition (immutable)."""
    phase_id: str
    handler: Callable  # async def handler() -> Dict
    timeout_s: int = 3600
    depends_on: List[str] = field(default_factory=list)
    on_failure: Literal['retry', 'escalate', 'skip'] = 'retry'
    retry_count: int = 3


@dataclass
class TaskSpec:
    """Task specification."""
    task_id: str
    title: str
    phases: List[Phase]
    tenant_id: str = "_default"
    parent_task_id: Optional[str] = None


class TaskOrchestrator:
    """Stateless DAG executor coordinating task phases."""

    def __init__(self, registry: Optional[TaskRegistryPersistence] = None):
        self.registry = registry or get_default_registry()
        self._event_handlers = {}

    def on_event(self, event_type: str, handler: Callable):
        """Subscribe to events (phase.completed, phase.failed, task.completed, task.failed)."""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)

    async def _emit_event(self, event_type: str, data: Dict):
        """Emit event to all subscribers."""
        for handler in self._event_handlers.get(event_type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                print(f"Event handler error ({event_type}): {e}")

    async def execute(self, spec: TaskSpec) -> TaskMetadata:
        """
        Execute task DAG:
        1. Register task
        2. Topological sort phases
        3. Execute ready phases (asyncio.gather)
        4. Emit events, handle failures
        """
        # Create initial task
        task = TaskMetadata(
            task_id=spec.task_id,
            title=spec.title,
            status=TaskStatus.RUNNING,
            phases={p.phase_id: PhaseMetadata(phase_id=p.phase_id, status=PhaseStatus.PENDING)
                    for p in spec.phases},
            tenant_id=spec.tenant_id,
            parent_task_id=spec.parent_task_id,
        )
        await self.registry.append_task(task)
        return await self._drive(spec)

    async def resume(self, spec: TaskSpec) -> TaskMetadata:
        """Continue a task that a previous process left unfinished.

        Nothing used to be able to do this: the DAG lived only in the
        `execute()` call stack, so a process death stranded the registry with
        RUNNING phases forever and no way to pick them up. `resume()` reads the
        persisted state, re-arms the phases that were in flight when the
        process died (RUNNING → PENDING, keeping their retry count so a
        crash-looping phase still exhausts its budget), and drives the same
        loop. Idempotent: COMPLETED phases are never re-run, so a resume can
        never redo committed work.

        A phase that FAILED terminally (its retry budget was spent) is NOT
        re-armed — resume continues an INTERRUPTED run, it does not overturn a
        verdict the retry policy already reached. Such a task resumes straight
        back to FAILED. Re-running it is a new `execute()` with a new task id.
        """
        task = await self.registry.get_task(spec.task_id, spec.tenant_id)
        if task is None:
            # Nothing persisted — a resume of an unknown task is just a start.
            return await self.execute(spec)
        if task.status == TaskStatus.COMPLETED:
            return task

        phases = dict(task.phases)
        rearmed = []
        for p in spec.phases:
            meta = phases.get(p.phase_id)
            if meta is None:
                phases[p.phase_id] = PhaseMetadata(phase_id=p.phase_id,
                                                   status=PhaseStatus.PENDING)
            elif meta.status == PhaseStatus.RUNNING:
                phases[p.phase_id] = PhaseMetadata(
                    phase_id=p.phase_id, status=PhaseStatus.PENDING,
                    retry_count=meta.retry_count,
                    error="interrupted: the previous process died mid-phase",
                )
                rearmed.append(p.phase_id)
        await self.registry.append_task(TaskMetadata(
            task_id=task.task_id, title=task.title, status=TaskStatus.RUNNING,
            phases=phases, created_at=task.created_at,
            updated_at=datetime.now(), tenant_id=task.tenant_id,
            parent_task_id=task.parent_task_id,
        ))
        if rearmed and _notification_router:
            try:
                await _notification_router.on_phase_retry({
                    "task_id": spec.task_id,
                    "phase_id": ", ".join(rearmed),
                    "retry_count": 0, "max_retries": 0,
                    "error": "interrupted run resumed",
                })
            except Exception as e:  # noqa: BLE001
                print(f"resume notification failed: {e}")
        await self._emit_event("task.resumed", {
            "task_id": spec.task_id, "rearmed": rearmed,
        })
        return await self._drive(spec)

    async def _drive(self, spec: TaskSpec) -> TaskMetadata:
        """Run the DAG to a terminal state. Shared by execute() and resume()."""
        # Build phase lookup
        phase_lookup = {p.phase_id: p for p in spec.phases}
        # A FAILED phase declared `on_failure='skip'` must not block its
        # dependents — that is what 'skip' means. Without this the DAG stalled
        # on the very failure the author said to tolerate, and the whole task
        # was reported as failed.
        skippable = {p.phase_id for p in spec.phases if p.on_failure == 'skip'}

        def _dep_satisfied(task, dep: str) -> bool:
            st = task.phases[dep].status
            return st == PhaseStatus.COMPLETED or (
                st == PhaseStatus.FAILED and dep in skippable)

        # Topological sort (ready phases have no unfinished dependencies)
        async def get_ready_phases():
            task = await self._require_task(spec.task_id, spec.tenant_id)
            return [
                phase_lookup[phase_id]
                for phase_id in phase_lookup
                if (task.phases[phase_id].status == PhaseStatus.PENDING
                    and all(_dep_satisfied(task, dep)
                            for dep in phase_lookup[phase_id].depends_on))
            ]

        # Execute until all phases complete
        while True:
            ready = await get_ready_phases()
            if not ready:
                # Check if all done
                task = await self._require_task(spec.task_id, spec.tenant_id)
                if all(p.status in (PhaseStatus.COMPLETED, PhaseStatus.FAILED)
                       for p in task.phases.values()):
                    break
                else:
                    # Stalled: some phase failed, no ready phases
                    # Notify user of stall condition
                    if _notification_router:
                        failed_phases = [p.phase_id for p in task.phases.values()
                                        if p.status == PhaseStatus.FAILED]
                        await _notification_router.on_phase_failed({
                            "task_id": spec.task_id,
                            "phase_id": "TASK_STALLED",
                            "error": f"Task stalled: phases {failed_phases} failed, no recovery path",
                        })

                    await self._emit_event("task.failed", {
                        "task_id": spec.task_id,
                        "reason": "phase_failed_no_ready"
                    })
                    return task

            # Execute ready phases concurrently
            results = await asyncio.gather(
                *[self._execute_phase(spec.task_id, p, spec.tenant_id) for p in ready],
                return_exceptions=True
            )

            # Process results
            for phase, result in zip(ready, results):
                if isinstance(result, Exception):
                    await self._handle_phase_failure(spec.task_id, phase, result, spec.tenant_id)
                else:
                    await self._handle_phase_success(spec.task_id, phase, result, spec.tenant_id)

            # `escalate` means STOP — don't keep dispatching sibling phases of a
            # task that has already been declared failed.
            task = await self._require_task(spec.task_id, spec.tenant_id)
            if task.status == TaskStatus.FAILED:
                await self._emit_event("task.failed", {
                    "task_id": spec.task_id, "reason": "phase_escalated",
                })
                return task

        # Every phase reached a terminal state. That is NOT the same as success:
        # the original code stamped COMPLETED unconditionally here, so a DAG
        # whose last phase failed was reported as a completed task — a silent
        # false success on exactly the runs that need an honest verdict.
        task = await self._require_task(spec.task_id, spec.tenant_id)
        hard_failures = [
            pid for pid, meta in task.phases.items()
            if meta.status == PhaseStatus.FAILED and pid not in skippable
        ]
        final_status = TaskStatus.FAILED if hard_failures else TaskStatus.COMPLETED
        final_task = TaskMetadata(
            task_id=task.task_id,
            title=task.title,
            status=final_status,
            phases=task.phases,
            created_at=task.created_at,
            updated_at=datetime.now(),
            tenant_id=task.tenant_id,
            parent_task_id=task.parent_task_id,
        )
        await self.registry.append_task(final_task)

        if final_status == TaskStatus.FAILED:
            if _notification_router:
                await _notification_router.on_phase_failed({
                    "task_id": spec.task_id,
                    "phase_id": ", ".join(hard_failures),
                    "error": "phase(s) exhausted their retry budget",
                })
            await self._emit_event("task.failed", {
                "task_id": spec.task_id,
                "reason": "phase_retries_exhausted",
                "failed_phases": hard_failures,
            })
            return final_task

        # Notify user
        if _notification_router:
            await _notification_router.on_task_completed({
                "task_id": spec.task_id,
                "phases": len(task.phases)
            })

        await self._emit_event("task.completed", {
            "task_id": spec.task_id,
            "phases": len(task.phases)
        })
        return final_task

    async def _require_task(self, task_id: str, tenant_id: str) -> TaskMetadata:
        """Read a task, or raise with a diagnosable message.

        `registry.get_task` returns None for an unknown/purged task; every call
        site then did `task.phases[...]` and died with a bare
        `AttributeError: 'NoneType'`, which says nothing about which task went
        missing.
        """
        task = await self.registry.get_task(task_id, tenant_id)
        if task is None:
            raise RuntimeError(
                f"task {task_id!r} (tenant {tenant_id!r}) is not in the registry"
            )
        return task

    @staticmethod
    def _backoff_for(retry_count: int) -> float:
        """Seconds to wait before a failed phase becomes eligible again."""
        try:
            base = float(os.environ.get("VIBE_RETRY_BACKOFF_BASE", "2"))
            cap = float(os.environ.get("VIBE_RETRY_BACKOFF_MAX", "60"))
        except ValueError:
            base, cap = 2.0, 60.0
        return min(cap, base * (2 ** max(0, retry_count - 1)))

    async def _execute_phase(self, task_id: str, phase: Phase, tenant_id: str) -> Dict:
        """Execute one phase with timeout + heartbeat monitoring."""
        # Mark as running
        task = await self._require_task(task_id, tenant_id)
        # PRESERVE retry_count. Resetting it to 0 here (the original code) meant
        # _handle_phase_failure always computed 0 + 1 = 1, so `retry_count <
        # phase.retry_count` was permanently true: a phase that always fails
        # retried FOREVER, in a hot loop with no backoff, and the task could
        # never reach a terminal state. That is the single defect that stopped
        # long DAG runs from ever finishing.
        prior = task.phases.get(phase.phase_id)
        running_phase = PhaseMetadata(
            phase_id=phase.phase_id,
            status=PhaseStatus.RUNNING,
            started_at=datetime.now(),
            retry_count=prior.retry_count if prior else 0,
        )
        task = TaskMetadata(
            task_id=task.task_id,
            title=task.title,
            status=task.status,
            phases={**task.phases, phase.phase_id: running_phase},
            created_at=task.created_at,
            updated_at=datetime.now(),
            tenant_id=task.tenant_id,
            parent_task_id=task.parent_task_id,
        )
        await self.registry.append_task(task)

        # Phase notification callbacks
        async def on_heartbeat(data):
            """Periodic heartbeat during phase execution."""
            if _notification_router:
                await _notification_router.on_phase_heartbeat({
                    "task_id": task_id,
                    "phase_id": phase.phase_id,
                    **data
                })

        async def on_stall(data):
            """Phase is running too long (stall detection)."""
            if _notification_router:
                await _notification_router.on_phase_stalled({
                    "task_id": task_id,
                    "phase_id": phase.phase_id,
                    **data
                })

        try:
            # Execute with heartbeat monitoring (if available)
            if _heartbeat:
                result = await _heartbeat.monitor_phase(
                    task_id, phase.phase_id,
                    phase.handler,
                    phase.timeout_s,
                    on_heartbeat, on_stall
                )
            else:
                # Fallback to simple wait_for if heartbeat not available
                result = await asyncio.wait_for(phase.handler(), timeout=phase.timeout_s)
            return result
        except asyncio.TimeoutError as e:
            raise RuntimeError(f"Phase {phase.phase_id} timeout after {phase.timeout_s}s") from e

    async def _handle_phase_success(self, task_id: str, phase: Phase, result: Dict, tenant_id: str):
        """Handle successful phase completion."""
        task = await self._require_task(task_id, tenant_id)
        completed_phase = PhaseMetadata(
            phase_id=phase.phase_id,
            status=PhaseStatus.COMPLETED,
            started_at=task.phases[phase.phase_id].started_at,
            completed_at=datetime.now(),
            retry_count=task.phases[phase.phase_id].retry_count,
            result=result,
        )
        task = TaskMetadata(
            task_id=task.task_id,
            title=task.title,
            status=task.status,
            phases={**task.phases, phase.phase_id: completed_phase},
            created_at=task.created_at,
            updated_at=datetime.now(),
            tenant_id=task.tenant_id,
            parent_task_id=task.parent_task_id,
        )
        await self.registry.append_task(task)

        # Notify user
        if _notification_router:
            await _notification_router.on_phase_completed({
                "task_id": task_id,
                "phase_id": phase.phase_id,
                "result": result,
            })

        await self._emit_event("phase.completed", {
            "task_id": task_id,
            "phase_id": phase.phase_id,
            "result": result,
        })

    async def _handle_phase_failure(self, task_id: str, phase: Phase, error: Exception, tenant_id: str):
        """Handle phase failure with retry logic."""
        task = await self._require_task(task_id, tenant_id)
        retry_count = task.phases[phase.phase_id].retry_count + 1

        if retry_count < phase.retry_count:
            # Retry: reset to PENDING so the scheduler picks it up again.
            retry_phase = PhaseMetadata(
                phase_id=phase.phase_id,
                status=PhaseStatus.PENDING,
                retry_count=retry_count,
                error=str(error),
            )
            task = TaskMetadata(
                task_id=task.task_id,
                title=task.title,
                status=task.status,
                phases={**task.phases, phase.phase_id: retry_phase},
                created_at=task.created_at,
                updated_at=datetime.now(),
                tenant_id=task.tenant_id,
                parent_task_id=task.parent_task_id,
            )
            await self.registry.append_task(task)

            # Tell the user healing is happening. Without this a retrying task
            # is indistinguishable from a hung one.
            if _notification_router:
                try:
                    await _notification_router.on_phase_retry({
                        "task_id": task_id,
                        "phase_id": phase.phase_id,
                        "retry_count": retry_count,
                        "max_retries": phase.retry_count,
                        "error": str(error),
                    })
                except Exception as e:  # noqa: BLE001
                    print(f"retry notification failed: {e}")

            await self._emit_event("phase.retry", {
                "task_id": task_id,
                "phase_id": phase.phase_id,
                "retry_count": retry_count,
                "error": str(error),
            })
            # Exponential backoff BEFORE the phase becomes eligible again.
            # Without it the execute() loop re-dispatches a failing phase
            # immediately, turning a transient outage into a CPU-bound retry
            # storm that also burns the engine budget.
            await asyncio.sleep(self._backoff_for(retry_count))
        else:
            # Failed
            failed_phase = PhaseMetadata(
                phase_id=phase.phase_id,
                status=PhaseStatus.FAILED,
                retry_count=retry_count,
                error=str(error),
            )
            task = TaskMetadata(
                task_id=task.task_id,
                title=task.title,
                status=TaskStatus.FAILED if phase.on_failure == 'escalate' else task.status,
                phases={**task.phases, phase.phase_id: failed_phase},
                created_at=task.created_at,
                updated_at=datetime.now(),
                tenant_id=task.tenant_id,
                parent_task_id=task.parent_task_id,
            )
            await self.registry.append_task(task)

            # Notify user
            if _notification_router:
                await _notification_router.on_phase_failed({
                    "task_id": task_id,
                    "phase_id": phase.phase_id,
                    "error": str(error),
                })

            await self._emit_event("phase.failed", {
                "task_id": task_id,
                "phase_id": phase.phase_id,
                "error": str(error),
                "on_failure": phase.on_failure,
            })
