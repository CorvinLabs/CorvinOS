"""🚨 DEPRECATED: Phase 5.2 Workflow Optimizer Skill (ADR-0532 Phase 2) — Dead Code

⚠️  IMPORTANT: This module is NOT wired into production. It is dead code.
- Zero call sites outside tests
- Manifest exists but is never loaded
- E2E tests are unit tests (direct Python imports, no API transport)
- Learning loop disconnected (_emit_learning_event never executes)

Status: [BLOCKED] by E2E Wiring Proof — Phase 2 fix or remove entirely.

DECISION PENDING: Either (a) implement full API wiring + SkillManager registration, or (b) delete.
Timeline: Coordinate with ADR-0532 Phase roadmap (target: 2026-10-15).

---

ORIGINAL DESIGN:
Analyzes multi-task execution patterns to optimize orchestration decisions:
- Identifies parallelizable tasks from execution history
- Detects critical path and bottleneck stages
- Generates DAG reordering suggestions
- Learns confidence scores from feedback loop (ADR-0314)

This Skill replaces static workflow orchestration logic with data-driven optimization.

Compliance:
- GDPR Art. 30: All decisions logged to audit trail
- GDPR Art. 32: Execution results immutable, PII-scrubbed (no prompts/content)
- EU AI Act Art. 50: LoM binding in every execution
- ADR-0532 Phase 2: OS-Skills learning loop (feedback → optimization)
- ADR-0314: Event emission + feedback integration
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from .skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionTrace:
    """Immutable execution trace from a past task.

    Represents one completed task execution with:
    - task_id: Unique identifier
    - task_type: "code", "analysis", "chat", etc.
    - subtasks: List of subtask names executed
    - total_latency_ms: Total wall-clock time
    - per_task_latency_ms: {subtask_name: latency}
    - parallelizable_groups: Detected groups that can run in parallel
    - confidence_score: 0.0-1.0 (from learning loop)
    """
    task_id: str
    task_type: str
    subtasks: List[str]
    total_latency_ms: float
    per_task_latency_ms: Dict[str, float]
    parallelizable_groups: Optional[List[List[str]]] = None
    confidence_score: float = 0.85  # Default: high confidence in past decisions


@dataclass(frozen=True)
class OptimizationSuggestion:
    """Immutable optimization suggestion for a workflow."""
    suggestion_id: str  # UUID for tracking
    task_type: str  # "code", "analysis", etc.
    current_shape: str  # "serial" or "parallel_group_N"
    suggested_shape: str  # New orchestration shape
    estimated_speedup: float  # e.g., 1.5 = 50% faster
    confidence: float  # 0.0-1.0, how confident in this suggestion
    reasoning: str  # Human-readable explanation
    parallelizable_stages: List[Tuple[str, List[str]]]  # (stage_name, [tasks...])
    critical_path: List[str]  # Longest dependency chain


class WorkflowOptimizerSkill(Skill):
    """Learn optimal task orchestration from execution history.

    Input:
        task_type: str (e.g., "code_review", "analysis", "documentation")
        execution_history: List[dict] (past task traces)
        current_shape: str (current orchestration: "serial", "parallel")
        tenant_id: str (optional, for tenant-scoped learning)

    Output:
        suggestion: OptimizationSuggestion
        accepted: bool (whether to apply suggestion)
        confidence: float (0.0-1.0)
        learning_event_id: str (for feedback tracking)
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.workflow_optimizer",
            name="Workflow Optimizer",
            description="Optimize task orchestration by learning from execution patterns",
            version="0.1.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["orchestration", "workflow", "learning", "os-core"],
            learn=True,  # This skill learns from feedback (ADR-0314)
        )
        super().__init__(metadata)
        self._trace_cache: Dict[str, ExecutionTrace] = {}
        self._learned_patterns: Dict[str, Dict[str, float]] = {}  # task_type -> {param: value}

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze execution history and suggest optimizations.

        Args:
            input: Dictionary with:
                - task_type: str
                - execution_history: List[{task_id, subtasks, latencies}]
                - current_shape: str ("serial" | "parallel")
                - tenant_id: str (optional)

        Returns:
            Dictionary with:
                - suggestion: str (suggested DAG shape)
                - estimated_speedup: float
                - confidence: float
                - reasoning: str
                - learning_event_id: str (for feedback loop)
        """
        task_type = input.get("task_type", "general")
        execution_history = input.get("execution_history", [])
        current_shape = input.get("current_shape", "serial")
        tenant_id = input.get("tenant_id", "_default")

        # Phase 1: Validate inputs
        if not execution_history or len(execution_history) < 2:
            return {
                "suggestion": "insufficient_data",
                "estimated_speedup": 1.0,
                "confidence": 0.3,
                "reasoning": "Need at least 2 past executions to learn (received {})".format(
                    len(execution_history)
                ),
                "learning_event_id": str(uuid4()),
            }

        # Phase 2: Parse execution traces
        try:
            traces = self._parse_traces(execution_history)
        except Exception as e:
            logger.error(f"WorkflowOptimizer: Failed to parse traces: {e}")
            return {
                "suggestion": "parse_error",
                "estimated_speedup": 1.0,
                "confidence": 0.0,
                "reasoning": f"Parse error: {type(e).__name__}",
                "learning_event_id": str(uuid4()),
            }

        # Phase 3: Analyze parallelization potential
        parallelizable_groups, critical_path = self._detect_parallelizable_stages(traces)

        # Phase 4: Compute statistics
        latencies = [t.total_latency_ms for t in traces]
        mean_latency = statistics.mean(latencies)
        stdev_latency = statistics.stdev(latencies) if len(latencies) > 1 else 0.0

        # Phase 5: Generate suggestion
        suggested_shape, estimated_speedup, confidence = self._generate_suggestion(
            task_type=task_type,
            current_shape=current_shape,
            parallelizable_groups=parallelizable_groups,
            critical_path=critical_path,
            traces=traces,
            tenant_id=tenant_id,
        )

        # Phase 6: Emit learning event
        event_id = str(uuid4())
        self._emit_learning_event(
            event_id=event_id,
            task_type=task_type,
            suggestion=suggested_shape,
            confidence=confidence,
            parallelizable_groups=parallelizable_groups,
            tenant_id=tenant_id,
        )

        # Phase 7: Build result
        reasoning = self._build_reasoning(
            current_shape=current_shape,
            suggested_shape=suggested_shape,
            estimated_speedup=estimated_speedup,
            parallelizable_count=len(parallelizable_groups),
            critical_path_length=len(critical_path),
            mean_latency=mean_latency,
            stdev=stdev_latency,
        )

        logger.info(
            f"WorkflowOptimizer: task_type={task_type}, "
            f"current={current_shape} → {suggested_shape}, "
            f"speedup={estimated_speedup:.2f}x, confidence={confidence:.2f}"
        )

        return {
            "suggestion": suggested_shape,
            "estimated_speedup": estimated_speedup,
            "confidence": confidence,
            "reasoning": reasoning,
            "learning_event_id": event_id,
            "parallelizable_groups": [list(g) for g in parallelizable_groups],
            "critical_path": critical_path,
            "current_shape": current_shape,
        }

    def _parse_traces(self, history: List[dict]) -> List[ExecutionTrace]:
        """Parse raw execution history into ExecutionTrace objects."""
        traces = []
        for record in history:
            trace = ExecutionTrace(
                task_id=record.get("task_id", f"task_{len(traces)}"),
                task_type=record.get("task_type", "general"),
                subtasks=record.get("subtasks", []),
                total_latency_ms=float(record.get("total_latency_ms", 0)),
                per_task_latency_ms=record.get("per_task_latency_ms", {}),
                parallelizable_groups=record.get("parallelizable_groups"),
                confidence_score=float(record.get("confidence_score", 0.85)),
            )
            traces.append(trace)
        return traces

    def _detect_parallelizable_stages(
        self, traces: List[ExecutionTrace]
    ) -> Tuple[List[Tuple[str, ...]], List[str]]:
        """Detect which task stages can run in parallel.

        Returns:
            (parallelizable_groups, critical_path)
            where parallelizable_groups are tuples of (task_a, task_b, ...)
        """
        if not traces:
            return [], []

        # Analyze all traces to find consistent parallelization patterns
        parallelizable_groups = []
        all_subtasks = set()

        for trace in traces:
            all_subtasks.update(trace.subtasks)

        # Simple heuristic: tasks with similar latencies can likely run in parallel
        # (advanced version would use DAG analysis)
        subtask_list = sorted(all_subtasks)
        if len(subtask_list) >= 2:
            # Group subtasks by latency similarity
            groups = self._group_by_latency_similarity(traces, subtask_list)
            parallelizable_groups = groups

        # Critical path: longest sequence of dependent tasks
        # (simplified: assume sequential order in subtask list)
        critical_path = subtask_list[:min(3, len(subtask_list))]

        return parallelizable_groups, critical_path

    def _group_by_latency_similarity(
        self, traces: List[ExecutionTrace], subtasks: List[str]
    ) -> List[Tuple[str, ...]]:
        """Group subtasks that have similar latency profiles.

        Tasks with similar latencies are good parallelization candidates.
        """
        if len(subtasks) < 2:
            return []

        groups = []
        remaining = set(subtasks)

        while len(remaining) >= 2:
            # Pick first remaining task as group anchor
            anchor = next(iter(remaining))
            anchor_latencies = [
                trace.per_task_latency_ms.get(anchor, 100.0)
                for trace in traces
            ]
            anchor_mean = statistics.mean(anchor_latencies) if anchor_latencies else 0.0

            # Find tasks with similar latency
            group = {anchor}
            for task in list(remaining):
                if task == anchor:
                    continue
                task_latencies = [
                    trace.per_task_latency_ms.get(task, 100.0)
                    for trace in traces
                ]
                task_mean = statistics.mean(task_latencies) if task_latencies else 0.0
                # If within 20% of anchor latency, consider parallelizable
                if abs(task_mean - anchor_mean) / max(anchor_mean, 1.0) < 0.2:
                    group.add(task)

            groups.append(tuple(sorted(group)))
            remaining -= group

        return groups

    def _generate_suggestion(
        self,
        task_type: str,
        current_shape: str,
        parallelizable_groups: List[Tuple[str, ...]],
        critical_path: List[str],
        traces: List[ExecutionTrace],
        tenant_id: str,
    ) -> Tuple[str, float, float]:
        """Generate optimization suggestion based on analysis.

        Returns:
            (suggested_shape, estimated_speedup, confidence)
        """
        # Load learned config if available
        confidence_threshold = 0.7
        try:
            from .os_skills.skill_adapter import load_skill_config
            cfg, _ = load_skill_config("os.workflow_optimizer", tenant_id)
            confidence_threshold = getattr(cfg, "confidence_threshold", 0.7)
        except Exception:
            pass  # Use default if learning config unavailable

        # Determine suggestion
        if not parallelizable_groups or len(parallelizable_groups[0]) < 2:
            # No parallelization opportunity
            suggestion = "serial"
            speedup = 1.0
            confidence = 0.9
        else:
            # Opportunity to parallelize
            suggestion = "parallel_grouped"

            # Estimate speedup: based on how much work can be parallelized
            # Simplified: if we can parallelize N groups, speedup ≈ N / max_group_size
            max_group_size = max(len(g) for g in parallelizable_groups)
            speedup = min(2.0, max_group_size * 1.2)  # Cap at 2x

            # Confidence based on variance in traces
            latencies = [t.total_latency_ms for t in traces]
            if len(latencies) > 1:
                variance = statistics.variance(latencies)
                mean_latency = statistics.mean(latencies)
                coeff_variation = (variance ** 0.5) / mean_latency if mean_latency > 0 else 1.0
                # Lower variance = higher confidence
                confidence = max(0.5, 1.0 - (coeff_variation / 2.0))
            else:
                confidence = 0.6

        # If current shape matches suggestion, reduce confidence slightly
        if current_shape == suggestion:
            confidence *= 0.8  # Penalize no-change

        return suggestion, speedup, confidence

    def _emit_learning_event(
        self,
        event_id: str,
        task_type: str,
        suggestion: str,
        confidence: float,
        parallelizable_groups: List[Tuple[str, ...]],
        tenant_id: str,
    ) -> None:
        """Emit learning event for the feedback loop (ADR-0314)."""
        try:
            from core.learning.learning_events import LearningEvent, EventType
            from core.learning.event_emitter import EventEmitter
            from core.learning.event_store import EventStore
            from core.paths.tenant import tenant_home

            # Resolve event store
            home = tenant_home(tenant_id)
            store = EventStore(home / "global" / "learning")
            emitter = EventEmitter(store)

            # Create and emit event
            event = LearningEvent(
                event_id=event_id,
                event_type=EventType.SKILL_EXECUTED,
                skill_id="os.workflow_optimizer",
                tenant_id=tenant_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                signal={
                    "task_type": task_type,
                    "suggestion": suggestion,
                    "confidence": confidence,
                    "parallelizable_groups_count": len(parallelizable_groups),
                },
                skill_version="0.1.0",
                lom="core/skills/workflow_optimizer.py:WorkflowOptimizerSkill.execute",
            )
            emitter.emit(event)
            logger.debug(f"WorkflowOptimizer: emitted learning event {event_id}")
        except Exception as e:
            # Non-fatal: learning failure doesn't break execution
            logger.warning(f"WorkflowOptimizer: failed to emit learning event: {e}")

    def _build_reasoning(
        self,
        current_shape: str,
        suggested_shape: str,
        estimated_speedup: float,
        parallelizable_count: int,
        critical_path_length: int,
        mean_latency: float,
        stdev: float,
    ) -> str:
        """Build human-readable reasoning for the suggestion."""
        parts = []

        # Base reasoning
        if current_shape == suggested_shape:
            parts.append(f"Current shape ({current_shape}) is already optimal")
        else:
            parts.append(
                f"Recommend {suggested_shape} (vs. current {current_shape}) "
                f"for {estimated_speedup:.1f}x speedup"
            )

        # Add details
        if parallelizable_count > 0:
            parts.append(f"Found {parallelizable_count} parallelizable stage(s)")

        if critical_path_length > 0:
            parts.append(f"Critical path has {critical_path_length} stage(s)")

        if mean_latency > 0:
            parts.append(f"Mean latency: {mean_latency:.0f}ms (σ={stdev:.0f}ms)")

        return "; ".join(parts)
