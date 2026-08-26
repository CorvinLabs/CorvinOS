"""TaskOrchestrator: DAG-based phase executor (ADR-0402)."""

from dataclasses import dataclass, field
from typing import Dict, List, Callable, Optional, Literal
import asyncio
from datetime import datetime
from enum import Enum

from .task_registry import (
    TaskMetadata, PhaseMetadata, PhaseStatus, TaskStatus,
    TaskRegistryPersistence, get_default_registry
)

# Import notification router (optional, fail-gracefully if not available)
try:
    from .notification_router import NotificationRouter
    _notification_router = NotificationRouter()
except ImportError:
    _notification_router = None


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

        # Build phase lookup
        phase_lookup = {p.phase_id: p for p in spec.phases}

        # Topological sort (ready phases have no unfinished dependencies)
        async def get_ready_phases():
            task = await self.registry.get_task(spec.task_id, spec.tenant_id)
            return [
                phase_lookup[phase_id]
                for phase_id in phase_lookup
                if (task.phases[phase_id].status == PhaseStatus.PENDING
                    and all(task.phases[dep].status == PhaseStatus.COMPLETED
                            for dep in phase_lookup[phase_id].depends_on))
            ]

        # Execute until all phases complete
        while True:
            ready = await get_ready_phases()
            if not ready:
                # Check if all done
                task = await self.registry.get_task(spec.task_id, spec.tenant_id)
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

        # Task complete
        task = await self.registry.get_task(spec.task_id, spec.tenant_id)
        final_task = TaskMetadata(
            task_id=task.task_id,
            title=task.title,
            status=TaskStatus.COMPLETED,
            phases=task.phases,
            created_at=task.created_at,
            updated_at=datetime.now(),
            tenant_id=task.tenant_id,
            parent_task_id=task.parent_task_id,
        )
        await self.registry.append_task(final_task)

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

    async def _execute_phase(self, task_id: str, phase: Phase, tenant_id: str) -> Dict:
        """Execute one phase with timeout."""
        # Mark as running
        task = await self.registry.get_task(task_id, tenant_id)
        running_phase = PhaseMetadata(
            phase_id=phase.phase_id,
            status=PhaseStatus.RUNNING,
            started_at=datetime.now(),
            retry_count=0,
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

        try:
            result = await asyncio.wait_for(phase.handler(), timeout=phase.timeout_s)
            return result
        except asyncio.TimeoutError as e:
            raise RuntimeError(f"Phase {phase.phase_id} timeout after {phase.timeout_s}s") from e

    async def _handle_phase_success(self, task_id: str, phase: Phase, result: Dict, tenant_id: str):
        """Handle successful phase completion."""
        task = await self.registry.get_task(task_id, tenant_id)
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
        task = await self.registry.get_task(task_id, tenant_id)
        retry_count = task.phases[phase.phase_id].retry_count + 1

        if retry_count < phase.retry_count:
            # Retry: reset to PENDING
            retry_phase = PhaseMetadata(
                phase_id=phase.phase_id,
                status=PhaseStatus.PENDING,
                retry_count=retry_count,
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
            await self._emit_event("phase.retry", {
                "task_id": task_id,
                "phase_id": phase.phase_id,
                "retry_count": retry_count,
                "error": str(error),
            })
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
