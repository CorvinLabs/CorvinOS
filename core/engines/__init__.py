"""Engine subsystem — immutable execution context (Phase 0+).

Only ``ExecutionContext`` and friends live here; they back the console's
Task Context Inspector and context-engineering replay.

Removed 2026-09-07 (ADR-0538 Phase C measured deletion, evidence in the
commit): ``engine_interface`` / ``engine_registry`` / ``claude_engine`` /
``haiku_engine`` were SIMULATORS (canned responses, no real engine call)
reachable from nothing but their own package — zero importers outside
``core/engines`` and the equally caller-less ``core/orchestration/
{fallback_cascade,routing_decision,cost_capability_matrix}``, and no tests.
Real engine routing is ``operator/bridges/shared/engine_registry.py`` (the
WorkerEngine registry) and the ACP ``os.delegation_router`` skill.
"""

from core.engines.execution_context import (
    ExecutionState,
    ExecutionContext,
    ExecutionContextUpdate,
    ExecutionContextStore,
)

__all__ = [
    "ExecutionState",
    "ExecutionContext",
    "ExecutionContextUpdate",
    "ExecutionContextStore",
]
