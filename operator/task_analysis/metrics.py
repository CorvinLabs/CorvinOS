"""Prometheus metrics exporter for Task Engine monitoring (ADR-0267).

Exports 7 core metrics across all 6 phases:
1. phase_duration_seconds — timing + outcome per phase
2. confidence_score — histogram of routing confidence
3. routing_decision — delegation target distribution
4. model_selection — haiku vs opus distribution
5. graph_redundancy_ratio — graph deduplication efficiency
6. estimated_cost_usd — cost estimation histogram
7. contract_violations — phase contract breach counter

Context manager integration:
    with TaskMetrics() as metrics:
        result = engine.route_task(raw_task)
        metrics.record_phase("enrichment", duration=0.123, outcome="success")
        metrics.record_decision(result.decision_target, result.carve_out_reason)
"""

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from time import time
from typing import Optional, Generator
import logging

try:
    from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry
except ImportError:
    # Graceful no-op if prometheus_client not installed
    Counter = Histogram = Gauge = CollectorRegistry = None


logger = logging.getLogger(__name__)


class MetricsPhase(Enum):
    """Pipeline phases for metric tracking."""

    NORMALIZATION = "normalization"
    CLASSIFICATION = "classification"
    FILTERING = "filtering"
    VALIDATION = "validation"
    ENRICHMENT = "enrichment"
    CEL = "context_engineering"  # Phase 5.5
    DELEGATION = "delegation"


class MetricsOutcome(Enum):
    """Success/failure outcome for phase tracking."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


@dataclass
class PhaseMetrics:
    """Accumulated metrics for a single phase run."""

    phase: MetricsPhase
    duration_seconds: float
    outcome: MetricsOutcome
    contract_violation: bool = False
    violation_details: Optional[str] = None


class TaskMetrics:
    """Prometheus metrics collector for Task Engine.

    Thread-safe registry for all 7 metrics. Handles graceful fallback if
    prometheus_client is not available (no-op mode).
    """

    def __init__(self, registry: Optional[CollectorRegistry] = None):
        """Initialize metrics exporter.

        Args:
            registry: Custom Prometheus registry (uses default if None).
        """
        self._enabled = Counter is not None
        self._registry = registry if self._enabled else None
        self._phases_this_run: dict[str, PhaseMetrics] = {}

        if not self._enabled:
            logger.debug("prometheus_client not installed; metrics disabled (no-op mode)")
            return

        # 1. Phase duration + outcome (Counter for timing, labeled by phase + outcome)
        self.phase_duration_seconds = Histogram(
            name="task_analysis_phase_duration_seconds",
            documentation="Duration of each task analysis phase (seconds)",
            labelnames=["phase", "outcome"],
            buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0),
            registry=self._registry,
        )

        # 2. Confidence score distribution (histogram)
        self.confidence_score = Histogram(
            name="task_analysis_confidence_score",
            documentation="Confidence score of routing decision (0.0–1.0)",
            labelnames=[],
            buckets=(0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0),
            registry=self._registry,
        )

        # 3. Routing decision (Counter by target + carve_out_reason)
        self.routing_decision = Counter(
            name="task_analysis_routing_decision_total",
            documentation="Task routing decisions (native | acs | tde)",
            labelnames=["target", "carve_out_reason"],
            registry=self._registry,
        )

        # 4. Model selection distribution (Counter: haiku vs opus)
        self.model_selection = Counter(
            name="task_analysis_model_selection_total",
            documentation="Model selection distribution (haiku | opus)",
            labelnames=["model"],
            registry=self._registry,
        )

        # 5. Graph redundancy ratio (Gauge, 0.0–1.0)
        self.graph_redundancy_ratio = Gauge(
            name="task_analysis_graph_redundancy_ratio",
            documentation="Deduplication efficiency: (original_graphs - filtered_graphs) / original_graphs",
            labelnames=[],
            registry=self._registry,
        )

        # 6. Estimated cost (Histogram in USD)
        self.estimated_cost_usd = Histogram(
            name="task_analysis_estimated_cost_usd",
            documentation="Estimated USD cost for task completion",
            labelnames=[],
            buckets=(0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0),
            registry=self._registry,
        )

        # 7. Contract violations (Counter by phase)
        self.contract_violations = Counter(
            name="task_analysis_contract_violations_total",
            documentation="Phase data contract violations (per phase)",
            labelnames=["phase"],
            registry=self._registry,
        )

    def record_phase(
        self,
        phase: MetricsPhase,
        duration_seconds: float,
        outcome: MetricsOutcome = MetricsOutcome.SUCCESS,
        contract_violation: bool = False,
        violation_details: Optional[str] = None,
    ) -> None:
        """Record metrics for a completed phase.

        Args:
            phase: Which phase completed.
            duration_seconds: Elapsed time.
            outcome: SUCCESS | FAILURE | PARTIAL.
            contract_violation: Whether a data contract was violated.
            violation_details: Human-readable violation description.
        """
        if not self._enabled:
            return

        # Record phase timing
        self.phase_duration_seconds.labels(
            phase=phase.value, outcome=outcome.value
        ).observe(duration_seconds)

        # Record contract violation if present
        if contract_violation:
            self.contract_violations.labels(phase=phase.value).inc()
            if violation_details:
                logger.warning(
                    f"Contract violation in {phase.value}: {violation_details}"
                )

        # Track for end-of-run aggregation
        self._phases_this_run[phase.value] = PhaseMetrics(
            phase=phase,
            duration_seconds=duration_seconds,
            outcome=outcome,
            contract_violation=contract_violation,
            violation_details=violation_details,
        )

    def record_confidence(self, score: float) -> None:
        """Record a confidence score (0.0–1.0).

        Args:
            score: Confidence value.
        """
        if not self._enabled:
            return

        score = max(0.0, min(1.0, score))  # Clamp to [0.0, 1.0]
        self.confidence_score.observe(score)

    def record_decision(
        self, target: str, carve_out_reason: str = "none"
    ) -> None:
        """Record a routing decision.

        Args:
            target: Delegation target (native | acs | tde).
            carve_out_reason: Why delegated (big_data_vocabulary, high_complexity_opus, none).
        """
        if not self._enabled:
            return

        self.routing_decision.labels(
            target=target.lower(), carve_out_reason=carve_out_reason.lower()
        ).inc()

    def record_model_selection(self, model: str) -> None:
        """Record a model selection decision.

        Args:
            model: Selected model (haiku | opus).
        """
        if not self._enabled:
            return

        self.model_selection.labels(model=model.lower()).inc()

    def record_redundancy(self, original_count: int, filtered_count: int) -> None:
        """Record graph deduplication efficiency.

        Args:
            original_count: Number of graphs before filtering.
            filtered_count: Number of graphs after filtering/deduplication.
        """
        if not self._enabled or original_count == 0:
            return

        ratio = max(0.0, (original_count - filtered_count) / original_count)
        self.graph_redundancy_ratio.set(ratio)

    def record_cost(self, estimated_usd: float) -> None:
        """Record estimated cost in USD.

        Args:
            estimated_usd: Estimated cost.
        """
        if not self._enabled:
            return

        estimated_usd = max(0.0, estimated_usd)  # Cost cannot be negative
        self.estimated_cost_usd.observe(estimated_usd)

    @contextmanager
    def phase_timer(self, phase: MetricsPhase) -> Generator[dict, None, None]:
        """Context manager for automatic phase timing.

        Usage:
            with metrics.phase_timer(MetricsPhase.ENRICHMENT) as ctx:
                # do work
                result = enricher.enrich(task)
                ctx['outcome'] = MetricsOutcome.SUCCESS

        Yields:
            Dict to record outcome and optional metadata.
        """
        ctx = {"outcome": MetricsOutcome.SUCCESS, "contract_violation": False}
        start = time()

        try:
            yield ctx
        except Exception as e:
            ctx["outcome"] = MetricsOutcome.FAILURE
            ctx["violation_details"] = str(e)
            raise
        finally:
            duration = time() - start
            self.record_phase(
                phase=phase,
                duration_seconds=duration,
                outcome=ctx.get("outcome", MetricsOutcome.SUCCESS),
                contract_violation=ctx.get("contract_violation", False),
                violation_details=ctx.get("violation_details"),
            )

    def summary(self) -> dict:
        """Return summary of metrics collected this run.

        Returns:
            Dict with phase timings, total duration, contract violations.
        """
        if not self._enabled:
            return {}

        total_duration = sum(p.duration_seconds for p in self._phases_this_run.values())
        violations = sum(
            1 for p in self._phases_this_run.values() if p.contract_violation
        )

        return {
            "total_duration_seconds": total_duration,
            "phases": {
                name: {
                    "duration_seconds": p.duration_seconds,
                    "outcome": p.outcome.value,
                    "contract_violation": p.contract_violation,
                }
                for name, p in self._phases_this_run.items()
            },
            "total_contract_violations": violations,
        }

    def reset(self) -> None:
        """Clear in-memory phase tracking for next run."""
        self._phases_this_run.clear()
