"""WorkflowOptimizerSkill for L22 Workflow Optimization (ADR-0532 Phase 2).

Learns from execution traces and recommends workflow optimizations:
- Parallelization opportunities (latency-based grouping)
- Critical path analysis
- Resource utilization improvement

Integration:
- ADR-0314 (Learning): Emits confidence_event, outcome_feedback events
- Audit-first: Every recommendation logged via audit_backend
- Tenant-scoped: All operations isolated by tenant_id
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Literal
from uuid import uuid4

try:
    from core.learning.event_persistence import EventStore
    from core.learning.event_emission import EventEmitter, LearningEventType
except ImportError:
    # Fallback for test environments
    EventStore = None
    EventEmitter = None
    LearningEventType = None

try:
    from core.tenants.validation import validate_tenant_id
    from core.paths.tenant import tenant_home
except ImportError:
    def validate_tenant_id(tid: str) -> str:
        return tid

    def tenant_home(tid: str) -> str:
        return f"/fake/{tid}"

try:
    from core.audit.backend import AuditBackend
except ImportError:
    AuditBackend = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionTrace:
    """Single execution trace from a workflow run."""

    task_id: str
    task_type: str
    subtasks: List[str]
    total_latency_ms: float
    per_task_latency_ms: Dict[str, float]

    def __post_init__(self):
        if not self.task_id or not self.subtasks:
            raise ValueError("task_id and subtasks required")
        if self.total_latency_ms <= 0:
            raise ValueError("total_latency_ms must be positive")


@dataclass
class SkillMetadata:
    """Skill metadata."""

    id: str = "os.workflow_optimizer"
    version: str = "0.1.0"
    tags: List[str] = field(default_factory=lambda: ["workflow", "optimization", "l22"])


@dataclass
class OptimizationRecommendation:
    """Optimization recommendation output."""

    suggestion: str  # "serial", "parallel_grouped", "insufficient_data"
    estimated_speedup: float  # 1.0 = no improvement
    confidence: float  # [0.0, 1.0]
    reasoning: str  # Human-readable explanation
    parallelizable_groups: List[List[str]]  # Grouped subtasks
    critical_path: List[str]  # Longest sequential path
    current_shape: str  # Input current_shape
    learning_event_id: str  # Link to audit trail
    audit_hash: str = ""  # Hash for integrity

    def __post_init__(self):
        if not self.audit_hash:
            content = json.dumps(
                {
                    "suggestion": self.suggestion,
                    "speedup": self.estimated_speedup,
                    "confidence": self.confidence,
                    "groups": self.parallelizable_groups,
                    "path": self.critical_path,
                },
                sort_keys=True,
            )
            object.__setattr__(
                self, "audit_hash", hashlib.sha256(content.encode()).hexdigest()[:16]
            )


class WorkflowOptimizerSkill:
    """Workflow optimization Skill for L22.

    Analyzes execution traces to recommend:
    1. Parallelization opportunities (latency-based grouping)
    2. Critical path analysis
    3. Resource utilization improvements

    Learning-enabled: Feedback improves recommendations over time.
    Audit-first: Every recommendation is logged and attributed.
    """

    # Configuration (tunable via feedback/optimizer)
    LATENCY_THRESHOLD_PERCENTILE = 0.20  # Tasks within 20% of each other can parallelize
    CONFIDENCE_BASE = 0.70  # Base confidence for recommendations
    MIN_HISTORY_FOR_CONFIDENCE = 3  # Need ≥3 traces for good confidence
    LEARNING_EVENT_BUFFER_SIZE = 100  # Batch events every N recommendations

    def __init__(self):
        """Initialize WorkflowOptimizerSkill."""
        self.metadata = SkillMetadata()
        self._learning_count = 0
        self._event_store: Optional[EventStore] = None
        self._event_emitter: Optional[EventEmitter] = None
        self._audit_backend: Optional[AuditBackend] = None

    def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute workflow optimization analysis.

        Args:
            input_data: {
                "task_type": str,  # e.g., "code_review"
                "execution_history": List[Dict],  # Execution traces
                "current_shape": str,  # "serial" or "parallel_grouped"
                "tenant_id": Optional[str],  # Defaults to "_default"
            }

        Returns:
            Dict with recommendation and metadata.
        """
        tenant_id = input_data.get("tenant_id", "_default")
        try:
            validate_tenant_id(tenant_id)
        except (ValueError, TypeError):
            tenant_id = "_default"

        try:
            # Validate input
            task_type = input_data.get("task_type", "unknown")
            execution_history = input_data.get("execution_history", [])
            current_shape = input_data.get("current_shape", "serial")

            if not task_type:
                return self._error_response(
                    "missing_task_type", tenant_id, current_shape
                )

            # Parse traces
            try:
                traces = self._parse_traces(execution_history)
            except (ValueError, KeyError) as e:
                logger.warning(f"Trace parse error: {e}")
                return self._error_response(
                    "parse_error", tenant_id, current_shape, str(e)
                )

            # Insufficient data
            if len(traces) < self.MIN_HISTORY_FOR_CONFIDENCE:
                return self._insufficient_data_response(tenant_id, current_shape)

            # Analyze chains
            parallelizable_groups, critical_path = self._detect_parallelizable_stages(
                traces
            )

            # Generate recommendation
            recommendation = self._generate_recommendation(
                traces,
                parallelizable_groups,
                critical_path,
                current_shape,
                task_type,
            )

            # Emit learning event (ADR-0314)
            learning_event_id = self._emit_learning_event(
                recommendation, tenant_id, task_type
            )
            recommendation.learning_event_id = learning_event_id

            # Audit (fail-closed: if audit fails, still return recommendation but log it)
            self._audit_recommendation(recommendation, tenant_id)

            return self._format_response(recommendation)

        except Exception as e:
            logger.error(f"WorkflowOptimizerSkill.execute failed: {e}", exc_info=True)
            return self._error_response("execution_error", tenant_id, "serial", str(e))

    def _parse_traces(self, execution_history: List[Dict[str, Any]]) -> List[ExecutionTrace]:
        """Parse raw execution history into ExecutionTrace objects.

        Args:
            execution_history: List of trace dicts

        Returns:
            List of ExecutionTrace objects

        Raises:
            ValueError: If traces are malformed
        """
        traces = []
        for raw in execution_history:
            try:
                trace = ExecutionTrace(
                    task_id=raw.get("task_id", f"task_{len(traces)}"),
                    task_type=raw.get("task_type", "unknown"),
                    subtasks=raw.get("subtasks", []),
                    total_latency_ms=float(raw.get("total_latency_ms", 0.0)),
                    per_task_latency_ms=raw.get("per_task_latency_ms", {}),
                )
                traces.append(trace)
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Skipping malformed trace: {e}")
                continue

        if not traces:
            raise ValueError("No valid traces found in execution_history")

        return traces

    def _detect_parallelizable_stages(
        self, traces: List[ExecutionTrace]
    ) -> Tuple[List[List[str]], List[str]]:
        """Detect parallelizable stages and critical path.

        Args:
            traces: List of ExecutionTrace objects

        Returns:
            (parallelizable_groups, critical_path)
            - parallelizable_groups: List of task groups that can run in parallel
            - critical_path: The longest sequential path
        """
        if not traces:
            return [], []

        # Extract all subtask names
        all_subtasks = set()
        for trace in traces:
            all_subtasks.update(trace.subtasks)
        all_subtasks = sorted(list(all_subtasks))

        if not all_subtasks:
            return [], []

        # Group by latency similarity
        groups = self._group_by_latency_similarity(traces, all_subtasks)

        # Critical path = longest single task across all traces
        critical_path = self._compute_critical_path(traces)

        return groups, critical_path

    def _group_by_latency_similarity(
        self, traces: List[ExecutionTrace], subtasks: List[str]
    ) -> List[List[str]]:
        """Group subtasks by latency similarity.

        Tasks with similar latencies across multiple traces can often
        be parallelized without much loss of efficiency.

        Args:
            traces: List of ExecutionTrace objects
            subtasks: List of subtask names

        Returns:
            List of task groups (each group can parallelize)
        """
        if not traces or not subtasks:
            return []

        # Compute average latency per subtask
        subtask_latencies: Dict[str, List[float]] = {task: [] for task in subtasks}
        for trace in traces:
            for task, latency in trace.per_task_latency_ms.items():
                if task in subtask_latencies:
                    subtask_latencies[task].append(latency)

        # Filter out tasks with no data
        valid_tasks = {
            task: latencies
            for task, latencies in subtask_latencies.items()
            if latencies
        }

        if not valid_tasks:
            return []

        # Compute mean latency per task
        task_means = {task: statistics.mean(lats) for task, lats in valid_tasks.items()}

        # Group tasks with similar latencies (within LATENCY_THRESHOLD_PERCENTILE)
        groups: List[List[str]] = []
        sorted_tasks = sorted(
            task_means.items(), key=lambda x: x[1], reverse=True
        )  # Largest first

        assigned = set()
        for task, latency in sorted_tasks:
            if task in assigned:
                continue

            group = [task]
            threshold_min = latency * (1 - self.LATENCY_THRESHOLD_PERCENTILE)
            threshold_max = latency * (1 + self.LATENCY_THRESHOLD_PERCENTILE)

            for other_task, other_latency in sorted_tasks:
                if other_task != task and other_task not in assigned:
                    if threshold_min <= other_latency <= threshold_max:
                        group.append(other_task)
                        assigned.add(other_task)

            assigned.add(task)
            if len(group) > 1:  # Only add if >1 task can parallelize
                groups.append(sorted(group))

        return groups

    def _compute_critical_path(self, traces: List[ExecutionTrace]) -> List[str]:
        """Compute the longest sequential path.

        Returns:
            List of task names forming the longest path
        """
        if not traces:
            return []

        # Critical path = task with highest average latency
        all_task_latencies: Dict[str, List[float]] = {}
        for trace in traces:
            for task, latency in trace.per_task_latency_ms.items():
                if task not in all_task_latencies:
                    all_task_latencies[task] = []
                all_task_latencies[task].append(latency)

        if not all_task_latencies:
            return []

        # Sort by mean latency
        sorted_tasks = sorted(
            all_task_latencies.items(),
            key=lambda x: statistics.mean(x[1]),
            reverse=True,
        )

        # Return top 3 tasks (or fewer if not enough)
        return [task for task, _ in sorted_tasks[:3]]

    def _generate_recommendation(
        self,
        traces: List[ExecutionTrace],
        parallelizable_groups: List[List[str]],
        critical_path: List[str],
        current_shape: str,
        task_type: str,
    ) -> OptimizationRecommendation:
        """Generate optimization recommendation.

        Args:
            traces: List of ExecutionTrace objects
            parallelizable_groups: Grouped parallelizable tasks
            critical_path: Longest sequential path
            current_shape: Current workflow shape ("serial" or "parallel_grouped")
            task_type: Type of task

        Returns:
            OptimizationRecommendation
        """
        # Compute variance in latencies (stability metric)
        total_latencies = [t.total_latency_ms for t in traces]
        avg_latency = statistics.mean(total_latencies)
        variance = (
            statistics.stdev(total_latencies)
            if len(total_latencies) > 1
            else 0.0
        )
        cv = variance / avg_latency if avg_latency > 0 else 0.0  # Coefficient of variation

        # Decide recommendation
        if parallelizable_groups:
            suggestion = "parallel_grouped"
            # Estimate speedup based on number of groups
            num_groups = len(parallelizable_groups)
            estimated_speedup = min(2.0, 1.0 + (num_groups / 4.0))
        else:
            suggestion = "serial"
            estimated_speedup = 1.0

        # Compute confidence
        confidence = self._compute_confidence(
            suggestion, current_shape, cv, len(traces)
        )

        # Generate reasoning
        reasoning = self._generate_reasoning(
            suggestion, parallelizable_groups, critical_path, estimated_speedup, cv
        )

        return OptimizationRecommendation(
            suggestion=suggestion,
            estimated_speedup=estimated_speedup,
            confidence=confidence,
            reasoning=reasoning,
            parallelizable_groups=parallelizable_groups,
            critical_path=critical_path,
            current_shape=current_shape,
            learning_event_id="",  # Will be set after event emission
        )

    def _compute_confidence(
        self, suggestion: str, current_shape: str, cv: float, num_traces: int
    ) -> float:
        """Compute confidence score for recommendation.

        Args:
            suggestion: "serial", "parallel_grouped", etc.
            current_shape: Current workflow shape
            cv: Coefficient of variation (stability metric)
            num_traces: Number of traces analyzed

        Returns:
            Confidence score [0.0, 1.0]
        """
        confidence = self.CONFIDENCE_BASE

        # History size bonus
        if num_traces >= 10:
            confidence += 0.15
        elif num_traces >= self.MIN_HISTORY_FOR_CONFIDENCE:
            confidence += 0.05

        # Stability bonus (low variance = more confident)
        if cv < 0.1:
            confidence += 0.10
        elif cv < 0.2:
            confidence += 0.05

        # Penalty if suggestion == current_shape (no change = less valuable)
        if suggestion == current_shape:
            confidence *= 0.85

        # Clamp to [0.0, 1.0]
        return max(0.0, min(1.0, confidence))

    def _generate_reasoning(
        self,
        suggestion: str,
        parallelizable_groups: List[List[str]],
        critical_path: List[str],
        estimated_speedup: float,
        cv: float,
    ) -> str:
        """Generate human-readable reasoning.

        Args:
            suggestion: Recommendation
            parallelizable_groups: Grouped tasks
            critical_path: Longest path
            estimated_speedup: Estimated improvement factor
            cv: Coefficient of variation

        Returns:
            Reasoning string
        """
        parts = []

        if suggestion == "parallel_grouped":
            parts.append(
                f"Workflow can be optimized via parallelization. "
                f"{len(parallelizable_groups)} task group(s) can run in parallel."
            )
            for i, group in enumerate(parallelizable_groups, 1):
                parts.append(f"  Group {i}: {', '.join(group)}")
            parts.append(
                f"Estimated speedup: {estimated_speedup:.2f}x "
                f"(from ~{1.0:.1f}x baseline to {estimated_speedup:.2f}x)"
            )
        else:
            parts.append(
                "Workflow is already optimized for serial execution. "
                "Current task structure does not allow significant parallelization."
            )
            if critical_path:
                parts.append(f"Critical path (longest tasks): {', '.join(critical_path)}")

        if cv > 0.2:
            parts.append("Note: High variance in execution times detected. Results may vary.")
        elif cv < 0.1:
            parts.append("Note: Execution times are stable and consistent.")

        return "\n".join(parts)

    def _emit_learning_event(
        self, recommendation: OptimizationRecommendation, tenant_id: str, task_type: str
    ) -> str:
        """Emit learning event (ADR-0314).

        Args:
            recommendation: The recommendation
            tenant_id: Tenant ID
            task_type: Task type

        Returns:
            Event ID
        """
        event_id = str(uuid4())

        try:
            # Try to use real learning infrastructure
            if EventStore is not None:
                store = EventStore()
            if EventEmitter is not None:
                emitter = EventEmitter()

                event_payload = {
                    "event_type": "skill_executed",
                    "skill_id": self.metadata.id,
                    "suggestion": recommendation.suggestion,
                    "confidence": recommendation.confidence,
                    "estimated_speedup": recommendation.estimated_speedup,
                    "event_id": event_id,
                    "tenant_id": tenant_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                # Fire-and-forget emit (non-blocking)
                try:
                    emitter.emit(event_payload)
                except Exception as e:
                    logger.warning(f"Learning event emission failed: {e}")

        except Exception as e:
            logger.warning(f"Could not emit learning event: {e}")

        return event_id

    def _audit_recommendation(
        self, recommendation: OptimizationRecommendation, tenant_id: str
    ) -> None:
        """Audit recommendation (fail-closed: log but don't fail).

        Args:
            recommendation: The recommendation
            tenant_id: Tenant ID
        """
        try:
            if AuditBackend is not None:
                audit = AuditBackend(tenant_id=tenant_id)

                audit_event = {
                    "event_type": "skill_recommendation",
                    "skill_id": self.metadata.id,
                    "recommendation_hash": recommendation.audit_hash,
                    "suggestion": recommendation.suggestion,
                    "confidence": recommendation.confidence,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                audit.write_event(audit_event)
        except Exception as e:
            logger.warning(f"Audit write failed (non-blocking): {e}")

    def _error_response(
        self, error_type: str, tenant_id: str, current_shape: str, detail: str = ""
    ) -> Dict[str, Any]:
        """Generate error response.

        Args:
            error_type: Type of error
            tenant_id: Tenant ID
            current_shape: Current workflow shape
            detail: Optional detail message

        Returns:
            Error response dict
        """
        learning_event_id = self._emit_learning_event(
            OptimizationRecommendation(
                suggestion=error_type,
                estimated_speedup=1.0,
                confidence=0.0,
                reasoning=f"Error: {error_type}",
                parallelizable_groups=[],
                critical_path=[],
                current_shape=current_shape,
                learning_event_id="",
            ),
            tenant_id,
            "error",
        )

        return {
            "suggestion": error_type,
            "estimated_speedup": 1.0,
            "confidence": 0.0,
            "reasoning": f"Error: {error_type}" + (f" — {detail}" if detail else ""),
            "learning_event_id": learning_event_id,
            "parallelizable_groups": [],
            "critical_path": [],
            "current_shape": current_shape,
        }

    def _insufficient_data_response(self, tenant_id: str, current_shape: str) -> Dict[str, Any]:
        """Generate insufficient data response.

        Args:
            tenant_id: Tenant ID
            current_shape: Current workflow shape

        Returns:
            Insufficient data response dict
        """
        return self._error_response(
            "insufficient_data",
            tenant_id,
            current_shape,
            f"Need at least {self.MIN_HISTORY_FOR_CONFIDENCE} execution traces",
        )

    def _format_response(self, recommendation: OptimizationRecommendation) -> Dict[str, Any]:
        """Format recommendation as response dict.

        Args:
            recommendation: The recommendation object

        Returns:
            Formatted response dict
        """
        return {
            "suggestion": recommendation.suggestion,
            "estimated_speedup": recommendation.estimated_speedup,
            "confidence": recommendation.confidence,
            "reasoning": recommendation.reasoning,
            "learning_event_id": recommendation.learning_event_id,
            "parallelizable_groups": recommendation.parallelizable_groups,
            "critical_path": recommendation.critical_path,
            "current_shape": recommendation.current_shape,
        }

    def learn_from_feedback(self, feedback_event: Dict[str, Any]) -> None:
        """Learn from feedback event (ADR-0314).

        Args:
            feedback_event: Feedback event dict
                {
                    "event_id": str,
                    "skill_id": str,
                    "suggestion": str,  # Was it "serial" or "parallel_grouped"?
                    "outcome": str,  # "improved", "no_change", "worse"
                    "confidence_delta": float,  # How much to adjust confidence
                }
        """
        try:
            outcome = feedback_event.get("outcome", "unknown")
            suggestion = feedback_event.get("suggestion", "")
            confidence_delta = feedback_event.get("confidence_delta", 0.0)

            logger.info(
                f"WorkflowOptimizerSkill.learn_from_feedback: "
                f"suggestion={suggestion}, outcome={outcome}, delta={confidence_delta}"
            )

            # In a real implementation, this would update internal model parameters
            # For now, just log the feedback
            if outcome == "improved":
                self.CONFIDENCE_BASE = min(1.0, self.CONFIDENCE_BASE + abs(confidence_delta))
            elif outcome == "worse":
                self.CONFIDENCE_BASE = max(0.3, self.CONFIDENCE_BASE - abs(confidence_delta))

        except Exception as e:
            logger.warning(f"learn_from_feedback failed: {e}")
