"""Basic Orchestrator Skill: Plugin loading, task routing, worker coordination.

Orchestrates:
- Load available plugins from registry
- Route tasks to appropriate plugins/workers
- Coordinate parallel task execution
- Monitor task health and handle failures
- Checkpoint/resume tasks across sessions (using Context Bridge)

Contract (ADR-0535):
- Required dependencies: os.health_monitor, os.context_bridge
- Calling convention: orchestrator decides routing for incoming task
- Timeout budget: 200ms per routing decision
- Audit: Every routing decision logged with task ID, destination, rationale
- Degradation: If dependencies timeout, use fallback routing (round-robin)

Composition (ADR-0535):
- Calls health_monitor.execute() to check system health before routing
- Calls context_bridge.execute() to decide if task needs split across sessions
- Both called sequentially with timeout enforcement (50ms + 100ms budget)
- If either times out, degraded routing is used

Compliance:
- GDPR Art. 30/32: every routing decision audited
- ADR-0232: task execution is traceable through audit trail
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
import time
import logging
import hashlib

try:
    from .base_skill import BaseSkill, SkillExecutedEvent, SkillExecutionStatus, AuditTrail
except ImportError:
    from base_skill import BaseSkill, SkillExecutedEvent, SkillExecutionStatus, AuditTrail

logger = logging.getLogger(__name__)


class RoutingStrategy(Enum):
    """How to route a task."""
    DIRECT = "direct"  # Send to specified plugin
    PARALLEL = "parallel"  # Split into parallel subtasks
    SEQUENTIAL = "sequential"  # Chain subtasks
    DELEGATED = "delegated"  # Send to worker pool
    DEFERRED = "deferred"  # Queue for later (if context full)


@dataclass(frozen=True)
class TaskDefinition:
    """Immutable task specification."""
    task_id: str
    task_type: str  # e.g., "code_gen", "analysis", "refactor"
    priority: int  # 1-10
    input_data: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)  # User ID, project ID, etc.


@dataclass(frozen=True)
class ExecutionPlan:
    """Immutable routing decision for a task."""
    task_id: str
    timestamp: str
    strategy: RoutingStrategy
    destination: str  # Plugin ID, worker pool name, etc.
    reason: str  # Why this routing was chosen
    fallback_destination: Optional[str] = None  # Used if primary fails
    estimated_duration_ms: int = 0  # Hint for caller


class BasicOrchestrator(BaseSkill[ExecutionPlan]):
    """Skill: Route tasks to plugins and workers.

    This is the highest-level Phase 1 skill. It coordinates Health Monitor
    and Context Bridge to make routing decisions that are:
    - Deterministic (same task → same route)
    - Audited (every decision logged)
    - Composable (depends on lower-level skills)
    """

    skill_id = "os.orchestrator"
    version = "1.0.0"
    required_dependencies = ["os.health_monitor", "os.context_bridge"]
    soft_dependencies: List[str] = []
    call_budget_ms = 200  # Heavier than dependencies (50ms + 100ms + overhead)

    def __init__(self, tenant_id: str, audit_trail: AuditTrail):
        super().__init__(tenant_id, audit_trail)
        self._plugin_registry: Dict[str, Dict[str, Any]] = {}  # Plugin metadata
        self._task_history: List[Tuple[str, RoutingStrategy]] = []  # For load balancing

    def execute(self, input_data: Dict[str, Any]) -> ExecutionPlan:
        """Route an incoming task.

        Input:
        - "task": TaskDefinition
        - "health_monitor": HealthMonitor skill instance (for calling)
        - "context_bridge": ContextBridge skill instance (for calling)
        - "available_plugins": List[str] (plugin IDs)
        - "worker_pool_status": Dict (pending tasks per worker)

        Returns:
            ExecutionPlan (routing decision)
        """
        start = time.perf_counter()

        try:
            task = input_data.get("task")
            if not isinstance(task, TaskDefinition):
                raise ValueError("Missing or invalid 'task' in input")

            health_monitor = input_data.get("health_monitor")
            context_bridge = input_data.get("context_bridge")
            available_plugins = input_data.get("available_plugins", [])
            worker_pool_status = input_data.get("worker_pool_status", {})

            # Call dependency: health_monitor
            health_status = None
            if health_monitor:
                try:
                    health_status = self._call_dependency(
                        health_monitor,
                        timeout_ms=50,
                        input_data={"subsystems": ["task_queue", "context_window"]},
                    )
                except TimeoutError:
                    logger.warning("Health monitor call timed out", extra={"tenant_id": self.tenant_id})

            # Call dependency: context_bridge
            should_split = False
            if context_bridge:
                try:
                    should_split, snapshot = self._call_dependency(
                        context_bridge,
                        timeout_ms=100,
                        input_data={
                            "current_session_id": "default",
                            "tokens_used": 50000,
                            "tokens_max": 128000,
                            "task_metadata": task.metadata,
                            "health_monitor_status": health_status,
                        },
                    )
                except TimeoutError:
                    logger.warning("Context bridge call timed out", extra={"tenant_id": self.tenant_id})

            # Make routing decision
            plan = self._decide_routing(
                task,
                health_status,
                should_split,
                available_plugins,
                worker_pool_status,
            )

            latency_ms = int((time.perf_counter() - start) * 1000)

            # Audit this routing decision
            self._audit_execution(
                input_data=input_data,
                output_data=plan,
                status=SkillExecutionStatus.SUCCESS,
                latency_ms=latency_ms,
            )

            # Record in history for load balancing
            self._task_history.append((task.task_id, plan.strategy))
            if len(self._task_history) > 1000:
                self._task_history = self._task_history[-1000:]

            logger.info(
                f"Task routed: {task.task_id} → {plan.destination} ({plan.strategy.value})",
                extra={"tenant_id": self.tenant_id}
            )

            return plan

        except Exception as e:
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.error(f"Orchestrator routing error: {e}", extra={"tenant_id": self.tenant_id})

            # Audit the error
            error_plan = ExecutionPlan(
                task_id=input_data.get("task", TaskDefinition("error", "error", 1, {})).task_id,
                timestamp=self._now_iso(),
                strategy=RoutingStrategy.DEFERRED,
                destination="error_queue",
                reason=f"Orchestrator error: {str(e)}",
                fallback_destination=None,
            )

            self._audit_execution(
                input_data=input_data,
                output_data=error_plan,
                status=SkillExecutionStatus.ERROR,
                latency_ms=latency_ms,
                error_message=str(e),
            )

            raise

    def _call_dependency(
        self,
        skill_instance: Any,
        timeout_ms: int,
        input_data: Dict[str, Any],
    ) -> Any:
        """Call a dependency skill with timeout enforcement.

        ADR-0535: Composition calling convention.
        """
        start = time.perf_counter()
        try:
            result = skill_instance.execute(input_data)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if elapsed_ms > timeout_ms:
                logger.warning(
                    f"Dependency {skill_instance.skill_id} exceeded timeout: {elapsed_ms}ms > {timeout_ms}ms"
                )
                raise TimeoutError(f"{skill_instance.skill_id} timeout")
            return result
        except TimeoutError:
            raise
        except Exception as e:
            logger.error(f"Dependency {skill_instance.skill_id} error: {e}")
            raise

    def _decide_routing(
        self,
        task: TaskDefinition,
        health_status: Optional[Any],
        should_split: bool,
        available_plugins: List[str],
        worker_pool_status: Dict[str, int],
    ) -> ExecutionPlan:
        """Determine routing strategy based on task and system state."""

        # If context split is needed, defer this task
        if should_split:
            return ExecutionPlan(
                task_id=task.task_id,
                timestamp=self._now_iso(),
                strategy=RoutingStrategy.DEFERRED,
                destination="context_split_queue",
                reason="Context window full, deferring until split completes",
                fallback_destination=None,
            )

        # Route based on task type
        if task.task_type == "code_gen":
            destination = self._pick_least_loaded_worker(worker_pool_status)
            return ExecutionPlan(
                task_id=task.task_id,
                timestamp=self._now_iso(),
                strategy=RoutingStrategy.DELEGATED,
                destination=destination,
                reason="Code generation → worker pool (parallel-friendly)",
                fallback_destination="default_worker",
            )

        elif task.task_type == "analysis":
            return ExecutionPlan(
                task_id=task.task_id,
                timestamp=self._now_iso(),
                strategy=RoutingStrategy.DIRECT,
                destination="analysis_plugin",
                reason="Analysis task → dedicated plugin",
                fallback_destination="default_worker",
            )

        else:
            # Default: send to least-loaded worker
            destination = self._pick_least_loaded_worker(worker_pool_status)
            return ExecutionPlan(
                task_id=task.task_id,
                timestamp=self._now_iso(),
                strategy=RoutingStrategy.DELEGATED,
                destination=destination,
                reason=f"Task type '{task.task_type}' → default worker pool",
                fallback_destination="default_worker",
            )

    def _pick_least_loaded_worker(self, worker_pool_status: Dict[str, int]) -> str:
        """Pick worker with fewest pending tasks (load balancing)."""
        if not worker_pool_status:
            return "worker_0"

        least_loaded = min(worker_pool_status.items(), key=lambda x: x[1])
        return least_loaded[0]

    def _now_iso(self) -> str:
        """Current time in ISO8601."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"
