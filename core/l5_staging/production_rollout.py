"""L5 Week 3: Production Rollout.

Gradual canary rollout (10% → 50% → 100% of operators).
SLA monitoring and auto-rollback if violations.
Post-rollout verification and success criteria.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum
from datetime import datetime
import random
import json
import logging

logger = logging.getLogger(__name__)


class CanaryPhase(str, Enum):
    """Canary rollout phases."""
    PHASE_1_10_PERCENT = "phase_1_10_percent"
    PHASE_2_50_PERCENT = "phase_2_50_percent"
    PHASE_3_100_PERCENT = "phase_3_100_percent"
    COMPLETE = "complete"


class SLAViolationType(str, Enum):
    """Types of SLA violations."""
    OPERATOR_LATENCY = "operator_latency"
    APPROVAL_ACCURACY = "approval_accuracy"
    AUTO_APPROVAL_RATE = "auto_approval_rate"
    NONE = "none"


@dataclass
class SLAMetrics:
    """SLA metrics during production rollout."""
    operator_latency_p95: float
    operator_latency_p99: float
    approval_accuracy: float
    auto_approval_rate: float
    incident_count: int = 0
    violations: List[SLAViolationType] = field(default_factory=list)


@dataclass
class CanaryPhaseResult:
    """Results from a canary phase."""
    phase: CanaryPhase
    duration_hours: int
    operators_deployed: int
    sla_metrics: SLAMetrics
    incidents: List[str]
    ready_for_next_phase: bool
    auto_rollback_triggered: bool


@dataclass
class ProductionRolloutMetrics:
    """Metrics from complete Week 3 rollout."""
    total_duration_days: int
    total_operators: int
    all_phases_complete: bool
    total_incidents: int
    sla_violations: Dict[str, int]
    auto_rollbacks: int
    final_sla_metrics: SLAMetrics
    operator_satisfaction: float
    ready_for_long_term_ops: bool


class ProductionRolloutManager:
    """L5 Week 3: Production Rollout Management."""

    def __init__(self, total_operators: int = 1000):
        """Initialize rollout manager.

        Args:
            total_operators: Total number of operators in production
        """
        self.total_operators = total_operators
        self.current_phase = CanaryPhase.PHASE_1_10_PERCENT
        self.phase_results: List[CanaryPhaseResult] = []
        self.all_incidents: List[str] = []
        self.auto_rollback_count = 0

        logger.info(f"[Production Rollout] Initialized for {total_operators} operators")

    def _simulate_phase(
        self,
        phase: CanaryPhase,
        num_operators: int,
        duration_hours: int,
    ) -> CanaryPhaseResult:
        """Simulate a canary phase."""
        logger.info(f"[Production Rollout] {phase.value}: {num_operators} operators, {duration_hours}h")

        # Simulate SLA metrics
        # Baseline: good but with some variation
        baseline_p95 = 2.8 + random.uniform(-0.3, 0.5)
        baseline_p99 = 3.8 + random.uniform(-0.3, 0.8)
        baseline_accuracy = 0.985 + random.uniform(-0.01, 0.005)
        baseline_auto_approval = 0.55 + random.uniform(-0.05, 0.08)

        sla_metrics = SLAMetrics(
            operator_latency_p95=baseline_p95,
            operator_latency_p99=baseline_p99,
            approval_accuracy=baseline_accuracy,
            auto_approval_rate=baseline_auto_approval,
            incident_count=random.randint(0, 2),
        )

        # Check for SLA violations
        violations = []
        if sla_metrics.operator_latency_p95 > 5.0:
            violations.append(SLAViolationType.OPERATOR_LATENCY)
        if sla_metrics.approval_accuracy < 0.99:
            violations.append(SLAViolationType.APPROVAL_ACCURACY)
        if sla_metrics.auto_approval_rate < 0.30:
            violations.append(SLAViolationType.AUTO_APPROVAL_RATE)

        sla_metrics.violations = violations

        # Generate incidents if violations
        incidents = []
        if violations:
            incidents.append(f"SLA violations detected: {[v.value for v in violations]}")

        # Auto-rollback if approval accuracy drops
        auto_rollback = False
        if SLAViolationType.APPROVAL_ACCURACY in violations:
            auto_rollback = True
            self.auto_rollback_count += 1
            incidents.append("Auto-rollback triggered: approval accuracy dropped")

        # Phase progression
        ready_for_next = (
            sla_metrics.operator_latency_p95 < 5.0
            and sla_metrics.approval_accuracy > 0.985
            and len(violations) == 0
        )

        result = CanaryPhaseResult(
            phase=phase,
            duration_hours=duration_hours,
            operators_deployed=num_operators,
            sla_metrics=sla_metrics,
            incidents=incidents,
            ready_for_next_phase=ready_for_next,
            auto_rollback_triggered=auto_rollback,
        )

        self.phase_results.append(result)
        self.all_incidents.extend(incidents)

        return result

    def phase_1_10_percent(self) -> CanaryPhaseResult:
        """Phase 1: Deploy to 10% of operators (24h)."""
        num_operators = int(self.total_operators * 0.10)
        return self._simulate_phase(
            phase=CanaryPhase.PHASE_1_10_PERCENT,
            num_operators=num_operators,
            duration_hours=24,
        )

    def phase_2_50_percent(self) -> CanaryPhaseResult:
        """Phase 2: Deploy to 50% of operators (48h)."""
        num_operators = int(self.total_operators * 0.50)
        return self._simulate_phase(
            phase=CanaryPhase.PHASE_2_50_PERCENT,
            num_operators=num_operators,
            duration_hours=48,
        )

    def phase_3_100_percent(self) -> CanaryPhaseResult:
        """Phase 3: Deploy to 100% of operators (ongoing monitoring)."""
        num_operators = self.total_operators
        return self._simulate_phase(
            phase=CanaryPhase.PHASE_3_100_PERCENT,
            num_operators=num_operators,
            duration_hours=72,  # 3 days of sustained monitoring
        )

    def run_full_rollout(self) -> ProductionRolloutMetrics:
        """Run complete production rollout (all 3 phases)."""
        logger.info("[Production Rollout] Starting full production rollout")

        # Phase 1
        result_1 = self.phase_1_10_percent()
        if not result_1.ready_for_next_phase:
            logger.warning("[Production Rollout] Phase 1 failed; would retry in production")

        # Phase 2
        result_2 = self.phase_2_50_percent()
        if not result_2.ready_for_next_phase:
            logger.warning("[Production Rollout] Phase 2 failed; would retry in production")

        # Phase 3
        result_3 = self.phase_3_100_percent()

        # Compute overall metrics
        total_duration = result_1.duration_hours + result_2.duration_hours + result_3.duration_hours
        total_days = total_duration / 24

        # SLA violations
        sla_violation_counts = {}
        for result in self.phase_results:
            for violation in result.sla_metrics.violations:
                sla_violation_counts[violation.value] = (
                    sla_violation_counts.get(violation.value, 0) + 1
                )

        # Success if Phase 3 complete and SLAs met
        all_phases_complete = (
            result_1.ready_for_next_phase
            and result_2.ready_for_next_phase
            and result_3.ready_for_next_phase
        )

        # Operator satisfaction (simulated)
        operator_satisfaction = 0.82 + random.uniform(-0.05, 0.08)

        # Ready for long-term ops if no critical violations and Phase 3 passed
        ready_for_longterm = (
            all_phases_complete
            and self.auto_rollback_count == 0
            and operator_satisfaction > 0.75
        )

        return ProductionRolloutMetrics(
            total_duration_days=int(total_days),
            total_operators=self.total_operators,
            all_phases_complete=all_phases_complete,
            total_incidents=len(self.all_incidents),
            sla_violations=sla_violation_counts,
            auto_rollbacks=self.auto_rollback_count,
            final_sla_metrics=result_3.sla_metrics,
            operator_satisfaction=operator_satisfaction,
            ready_for_long_term_ops=ready_for_longterm,
        )

    def to_json_report(self) -> str:
        """Export metrics as JSON report."""
        # Run rollout if not already done
        if not self.phase_results:
            metrics = self.run_full_rollout()
        else:
            metrics = self._compute_metrics()

        return json.dumps(
            {
                "total_duration_days": metrics.total_duration_days,
                "total_operators": metrics.total_operators,
                "all_phases_complete": metrics.all_phases_complete,
                "total_incidents": metrics.total_incidents,
                "sla_violations": metrics.sla_violations,
                "auto_rollbacks": metrics.auto_rollbacks,
                "final_sla_metrics": {
                    "operator_latency_p95": metrics.final_sla_metrics.operator_latency_p95,
                    "operator_latency_p99": metrics.final_sla_metrics.operator_latency_p99,
                    "approval_accuracy": metrics.final_sla_metrics.approval_accuracy,
                    "auto_approval_rate": metrics.final_sla_metrics.auto_approval_rate,
                },
                "operator_satisfaction": metrics.operator_satisfaction,
                "ready_for_long_term_ops": metrics.ready_for_long_term_ops,
            },
            indent=2,
        )

    def _compute_metrics(self) -> ProductionRolloutMetrics:
        """Compute metrics from results."""
        if not self.phase_results:
            return ProductionRolloutMetrics(
                total_duration_days=0,
                total_operators=self.total_operators,
                all_phases_complete=False,
                total_incidents=0,
                sla_violations={},
                auto_rollbacks=0,
                final_sla_metrics=SLAMetrics(0, 0, 0, 0),
                operator_satisfaction=0.0,
                ready_for_long_term_ops=False,
            )

        total_duration = sum(r.duration_hours for r in self.phase_results)
        all_phases_complete = all(r.ready_for_next_phase for r in self.phase_results)

        sla_violation_counts = {}
        for result in self.phase_results:
            for violation in result.sla_metrics.violations:
                sla_violation_counts[violation.value] = (
                    sla_violation_counts.get(violation.value, 0) + 1
                )

        return ProductionRolloutMetrics(
            total_duration_days=int(total_duration / 24),
            total_operators=self.total_operators,
            all_phases_complete=all_phases_complete,
            total_incidents=len(self.all_incidents),
            sla_violations=sla_violation_counts,
            auto_rollbacks=self.auto_rollback_count,
            final_sla_metrics=self.phase_results[-1].sla_metrics if self.phase_results else SLAMetrics(0, 0, 0, 0),
            operator_satisfaction=0.82,
            ready_for_long_term_ops=all_phases_complete and self.auto_rollback_count == 0,
        )
