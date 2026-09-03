"""Infinite-Session Task Engine — Graph-DAG Executor for Autonomous Long-Running Tasks.

Multi-phase task orchestration with:
- Graph-DAG execution (topological sort, dependency resolution)
- Transparent session bridging (state snapshots, immutable EventStore)
- Immutable audit trail (hash-chained events, zero gaps)
- Tenant-scoped isolation (ADR-0007)
- Load-bearing constraints: fail-closed, no silent operations

Reference: ADR-0540–0545, CONCEPT-0026

Example:
    task_def = TaskDefinition.from_json(task_json)
    executor = TaskExecutor(tenant_id="_default")
    executor.register_skill("skill-1", skill_fn)
    result = executor.run(task_def)
    assert result.success
    assert executor.event_store.verify_chain(task_def.task_id)
"""

from .executor import TaskExecutor
from .task_def import TaskDefinition, Phase, Gate
from .models import AuditEvent, Snapshot, ExecutionResult

__all__ = [
    "TaskExecutor",
    "TaskDefinition",
    "Phase",
    "Gate",
    "AuditEvent",
    "Snapshot",
    "ExecutionResult",
]
