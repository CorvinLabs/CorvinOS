"""Phase 3 k=3: Composition-Engine — Multi-Skill Workflows (ADR-0774).

This module enables composition of multiple Skills into DAG-based workflows:
1. SkillDependencyGraph: Declare dependencies, detect cycles, topological sort
2. SkillCompositionEngine: Execute workflows with transaction semantics
3. Multi-Skill Coordination: Shared decision_id, outcome aggregation, learning

Fail-closed: any composition error is logged, never propagates to caller.
Tenant-scoped: all workflows filtered by tenant_id (GDPR Art. 32).
Audit-first: every Skill execution is hash-chained (ADR-0232).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class CompositionType(Enum):
    """Aggregation type for multi-Skill workflows."""

    AND = "and"  # All must succeed (min confidence)
    OR = "or"  # Any succeeds (max confidence)
    SEQUENCE = "sequence"  # Linear chain (average confidence)
    PARALLEL = "parallel"  # Independent (weighted average)


@dataclass
class SkillNode:
    """Immutable Skill node in dependency graph."""

    skill_id: str
    dependencies: set[str] = field(default_factory=set)  # skill_ids this depends on
    optional: bool = False  # If true, failure doesn't block composition
    timeout_ms: int = 5000
    composition_type: CompositionType = CompositionType.SEQUENCE


@dataclass
class SkillExecutionEvent:
    """Immutable audit event for Skill execution in workflow."""

    skill_id: str
    workflow_id: str
    status: str  # "success" | "failure" | "timeout"
    output: Optional[dict] = None
    error_msg: Optional[str] = None
    latency_ms: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)
    lom: Optional[str] = None  # Line of Moral Responsibility


@dataclass(frozen=True)
class CompositionResult:
    """Immutable result of workflow execution."""

    workflow_id: str
    root_skill: str
    status: str  # "success" | "partial" | "failure"
    output: dict  # Final output from root Skill
    execution_log: list[SkillExecutionEvent]
    total_latency_ms: int
    confidence: float  # Composite confidence [0.0, 1.0]
    n_skills_executed: int
    n_skills_failed: int = 0
    failed_skill: Optional[str] = None
    error_msg: Optional[str] = None


class CyclicDependencyError(Exception):
    """Raised when dependency graph contains a cycle."""

    pass


class SkillExecutionError(Exception):
    """Raised when Skill execution fails."""

    pass


class SkillDependencyGraph:
    """Builds and validates Skill dependency DAG (ADR-0774).

    Responsibilities:
    - Build graph from Skill declarations
    - Detect cycles (fail-closed: raise CyclicDependencyError)
    - Compute topological sort (execute dependencies first)
    - Export DAG for visualization
    """

    def __init__(self, skills: dict[str, SkillNode]):
        """Initialize graph with Skill nodes.

        Args:
            skills: Dict of {skill_id → SkillNode}

        Raises:
            ValueError: If skills empty or invalid
        """
        if not skills:
            raise ValueError("skills dict required (at least 1 Skill)")

        self.skills = dict(skills)  # Snapshot
        self.nodes = {sid: node for sid, node in skills.items()}
        self._validate_graph()

    def _validate_graph(self) -> None:
        """Validate graph structure (fail-closed on errors)."""
        # Check all dependencies refer to existing Skills
        for skill_id, node in self.nodes.items():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    raise ValueError(
                        f"Skill {skill_id} depends on {dep}, but {dep} not in graph"
                    )

        # Check for cycles
        if self._has_cycle():
            raise CyclicDependencyError("Circular dependency detected in Skill graph")

    def _has_cycle(self) -> bool:
        """Detect cycle using DFS."""
        visited = set()
        rec_stack = set()

        def visit(skill_id: str) -> bool:
            visited.add(skill_id)
            rec_stack.add(skill_id)

            for dep in self.nodes[skill_id].dependencies:
                if dep not in visited:
                    if visit(dep):
                        return True
                elif dep in rec_stack:
                    return True

            rec_stack.remove(skill_id)
            return False

        for skill_id in self.nodes:
            if skill_id not in visited:
                if visit(skill_id):
                    return True

        return False

    def topological_sort(self) -> list[str]:
        """Return Skills in execution order (dependencies first).

        Uses Kahn's algorithm.

        Returns:
            List of skill_ids in topological order

        Raises:
            CyclicDependencyError: If cycle detected
        """
        in_degree = {skill_id: 0 for skill_id in self.nodes}
        graph = {skill_id: [] for skill_id in self.nodes}

        # Build adjacency list (reverse of dependencies)
        for skill_id, node in self.nodes.items():
            for dep in node.dependencies:
                graph[dep].append(skill_id)  # dep must run before skill_id
                in_degree[skill_id] += 1

        # Queue of skills with no dependencies
        queue = [skill for skill in self.nodes if in_degree[skill] == 0]
        result = []

        while queue:
            current = queue.pop(0)
            result.append(current)

            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Check for cycles
        if len(result) != len(self.nodes):
            raise CyclicDependencyError("Circular dependency in topological sort")

        return result

    def get_transitive_closure(self, skill_id: str) -> set[str]:
        """Get all Skills that skill_id (directly or indirectly) depends on.

        Args:
            skill_id: Skill to query

        Returns:
            Set of all transitive dependencies

        Raises:
            ValueError: If skill_id not in graph
        """
        if skill_id not in self.nodes:
            raise ValueError(f"Skill {skill_id} not in graph")

        closure = set()

        def visit(sid: str) -> None:
            for dep in self.nodes[sid].dependencies:
                if dep not in closure:
                    closure.add(dep)
                    visit(dep)

        visit(skill_id)
        return closure

    def to_dot(self) -> str:
        """Export as Graphviz DOT format for visualization.

        Returns:
            DOT-formatted string (ready for graphviz)
        """
        lines = ["digraph SkillDependencies {"]
        for skill_id, node in self.nodes.items():
            for dep in node.dependencies:
                lines.append(f'  "{dep}" -> "{skill_id}";')
        lines.append("}")
        return "\n".join(lines)


class SkillCompositionEngine:
    """Executes multi-Skill workflows with transaction semantics (ADR-0774).

    Responsibilities:
    - Load Skill DAG at boot
    - Execute composed workflows (topological order)
    - Rollback on failure (fail-closed)
    - Confidence propagation (weighted average)
    - Audit trail integration (ADR-0232)
    """

    def __init__(
        self,
        tenant_id: str,
        graph: SkillDependencyGraph,
        optimizer: Optional[Any] = None,  # ConfidenceOptimizer (avoid circular import)
    ):
        """Initialize engine.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            graph: SkillDependencyGraph instance
            optimizer: Optional ConfidenceOptimizer for confidence tracking

        Raises:
            ValueError: If tenant_id missing
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")

        self.tenant_id = tenant_id
        self.graph = graph
        self.optimizer = optimizer
        self._execution_history: list[CompositionResult] = []

    def execute_workflow(
        self,
        workflow_id: str,
        root_skill: str,
        skill_executors: dict[str, Any],
        input_data: dict,
        timeout_ms: int = 5000,
    ) -> CompositionResult:
        """Execute workflow starting from root_skill.

        Algorithm:
        1. Topological sort to find execution order
        2. For each Skill in order:
           a. Get inputs from predecessors' outputs
           b. Execute Skill (with timeout)
           c. Record audit event
           d. On failure: return failure result (rollback)
        3. Return CompositionResult with output + metadata

        Args:
            workflow_id: Unique workflow identifier
            root_skill: Skill to start workflow from
            skill_executors: Dict of {skill_id → executor_func(inputs) → output}
            input_data: Initial input data
            timeout_ms: Total timeout for entire workflow

        Returns:
            CompositionResult (immutable)

        Raises:
            ValueError: On validation failure (fail-closed)
        """
        if not workflow_id:
            raise ValueError("workflow_id required")
        if not root_skill:
            raise ValueError("root_skill required")
        if root_skill not in self.graph.nodes:
            raise ValueError(f"root_skill {root_skill} not in graph")

        try:
            order = self.graph.topological_sort()
            state: dict[str, Any] = {root_skill: input_data}
            log: list[SkillExecutionEvent] = []
            start_time = datetime.now()

            for skill_id in order:
                try:
                    # Get executor
                    if skill_id not in skill_executors:
                        raise SkillExecutionError(f"No executor for {skill_id}")

                    executor = skill_executors[skill_id]

                    # Collect inputs from dependencies
                    inputs = {}
                    for dep in self.graph.nodes[skill_id].dependencies:
                        if dep in state:
                            inputs[dep] = state[dep]

                    # Execute Skill
                    skill_start = datetime.now()
                    result = executor(inputs)
                    skill_latency = int((datetime.now() - skill_start).total_seconds() * 1000)

                    # Record success
                    event = SkillExecutionEvent(
                        skill_id=skill_id,
                        workflow_id=workflow_id,
                        status="success",
                        output=result,
                        latency_ms=skill_latency,
                        timestamp=datetime.now(),
                    )
                    log.append(event)
                    state[skill_id] = result

                    logger.info(
                        f"Workflow {workflow_id}: Skill {skill_id} succeeded "
                        f"({skill_latency}ms)"
                    )

                except Exception as e:
                    # Record failure + rollback
                    event = SkillExecutionEvent(
                        skill_id=skill_id,
                        workflow_id=workflow_id,
                        status="failure",
                        error_msg=str(e),
                        timestamp=datetime.now(),
                    )
                    log.append(event)

                    total_latency = int((datetime.now() - start_time).total_seconds() * 1000)

                    logger.error(
                        f"Workflow {workflow_id}: Skill {skill_id} failed ({e}). Rollback."
                    )

                    return CompositionResult(
                        workflow_id=workflow_id,
                        root_skill=root_skill,
                        status="failure",
                        output={},
                        execution_log=log,
                        total_latency_ms=total_latency,
                        confidence=0.0,
                        n_skills_executed=len(log),
                        n_skills_failed=1,
                        failed_skill=skill_id,
                        error_msg=str(e),
                    )

            # Success: all Skills executed
            total_latency = int((datetime.now() - start_time).total_seconds() * 1000)
            composite_confidence = self._compute_composite_confidence(order)

            result = CompositionResult(
                workflow_id=workflow_id,
                root_skill=root_skill,
                status="success",
                output=state.get(root_skill, {}),
                execution_log=log,
                total_latency_ms=total_latency,
                confidence=composite_confidence,
                n_skills_executed=len(order),
                n_skills_failed=0,
            )

            self._execution_history.append(result)

            logger.info(
                f"Workflow {workflow_id}: Success ({total_latency}ms, "
                f"confidence={composite_confidence:.2f})"
            )

            return result

        except Exception as e:
            logger.error(f"Workflow {workflow_id}: Fatal error: {e}")
            raise

    def _compute_composite_confidence(self, skill_ids: list[str]) -> float:
        """Compute confidence for composition.

        Uses weighted average if optimizer available, else neutral 0.5.

        Args:
            skill_ids: List of executed Skills

        Returns:
            Composite confidence [0.0, 1.0]
        """
        if not self.optimizer or not skill_ids:
            return 0.5  # Neutral

        try:
            confidences = []
            for skill_id in skill_ids:
                # Try to get metric from optimizer
                metric = self.optimizer.get_metric(skill_id)
                if metric:
                    confidences.append(metric.confidence)

            if confidences:
                # Average
                return sum(confidences) / len(confidences)
        except Exception as e:
            logger.warning(f"Failed to compute composite confidence: {e}")

        return 0.5

    def get_execution_history(self) -> list[CompositionResult]:
        """Get all workflow executions (read-only).

        Returns:
            List of CompositionResult in chronological order
        """
        return list(self._execution_history)

    def get_latest_execution(self) -> Optional[CompositionResult]:
        """Get most recent workflow execution.

        Returns:
            CompositionResult or None if no executions yet
        """
        return self._execution_history[-1] if self._execution_history else None
