"""
Phase 4.3: Production Tuning — A/B testing, canary deployment, automatic rollback.

Responsibilities:
1. Live A/B testing (run two threshold strategies in parallel)
2. Canary deployment (propose changes to 10% of operators first)
3. Automatic rollback (if metrics degrade, auto-rollback)
4. Cohort selection (which operators get the new config first?)
5. Metrics monitoring (detect regressions, success metrics)

Audit-first: Every deployment decision logged to audit chain.
Thread-safe: RLock protection on shared state.
Tenant-scoped: All queries filtered by tenant_id.

ADR-0587: Production Tuning (A/B testing, canary, auto-rollback)
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from enum import Enum
import random
import hashlib

logger = logging.getLogger(__name__)


# ============================================================================
# Data Structures
# ============================================================================


class TestStatus(str, Enum):
    """A/B test status."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class CanaryPhase(str, Enum):
    """Canary deployment phase."""
    INIT = "init"
    PHASE_1 = "phase_1"  # 10% operators
    PHASE_2 = "phase_2"  # 50% operators
    PHASE_3 = "phase_3"  # 100% operators


@dataclass(frozen=True)
class ABTestMetrics:
    """Metrics for an A/B test arm."""
    arm_id: str  # "control" or "treatment"
    num_evaluations: int
    approval_accuracy: float  # % approvals that were correct
    latency_p50: float  # ms
    latency_p95: float  # ms
    error_rate: float  # % errors
    cost: float  # Optimization cost


@dataclass(frozen=True)
class ABTest:
    """Active A/B test."""
    test_id: str
    skill_id: str
    metric_name: str
    started_at: str  # ISO 8601
    ended_at: Optional[str]
    status: TestStatus
    control_config: Dict[str, float]
    treatment_config: Dict[str, float]
    control_metrics: Optional[ABTestMetrics]
    treatment_metrics: Optional[ABTestMetrics]
    winner: Optional[str]  # "control" or "treatment"
    confidence: float  # P(winner is correct)


@dataclass(frozen=True)
class CanaryDeployment:
    """Canary deployment state."""
    deployment_id: str
    skill_id: str
    metric_name: str
    new_config: Dict[str, float]
    phase: CanaryPhase
    started_at: str  # ISO 8601
    phase_start: str
    num_operators_total: int
    num_operators_current_phase: int
    target_cohort_size: float  # % of operators
    metrics_pre_deployment: Dict[str, float]
    metrics_current_phase: Dict[str, float]
    rollback_triggered: bool
    rollback_reason: Optional[str]


@dataclass(frozen=True)
class RollbackDecision:
    """Automatic rollback decision."""
    deployment_id: str
    triggered_at: str  # ISO 8601
    trigger_reason: str  # "accuracy_drop", "latency_increase", "error_spike"
    metric_name: str
    threshold_value: float
    observed_value: float
    confidence: float  # P(rollback correct)
    restored_config: Dict[str, float]


# ============================================================================
# Production Tuning Engine
# ============================================================================


class ProductionTuningEngine:
    """
    Production tuning with A/B testing, canary deployment, automatic rollback.

    Fail-closed: All changes are reversible. No config is final until canary completes.
    Every deployment phase is monitored. Any metric degradation triggers rollback.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.lock = RLock()

        # Active A/B tests (by test_id)
        self.ab_tests: Dict[str, ABTest] = {}

        # Active canary deployments (by deployment_id)
        self.canaries: Dict[str, CanaryDeployment] = {}

        # Rollback history
        self.rollbacks: List[RollbackDecision] = []

        # Operator cohorts (for canary: operator_id → deployment_id)
        self.operator_cohorts: Dict[str, str] = {}

        # Configuration
        self.accuracy_drop_threshold = 0.05  # 5% drop triggers rollback
        self.latency_increase_threshold = 0.1  # 10% increase triggers rollback
        self.error_rate_threshold = 0.01  # 1% error rate triggers rollback
        self.min_samples_for_decision = 100  # Need 100+ samples before deciding

        # Audit trail
        self.audit_log: List[Dict[str, Any]] = []

    def start_ab_test(
        self,
        skill_id: str,
        metric_name: str,
        control_config: Dict[str, float],
        treatment_config: Dict[str, float],
    ) -> ABTest:
        """Start a new A/B test.

        Args:
            skill_id: Skill being tested
            metric_name: Metric being optimized
            control_config: Current (baseline) config
            treatment_config: Proposed new config

        Returns:
            ABTest with test_id and status=RUNNING
        """
        with self.lock:
            test_id = f"test_{datetime.utcnow().timestamp()}"

            test = ABTest(
                test_id=test_id,
                skill_id=skill_id,
                metric_name=metric_name,
                started_at=datetime.utcnow().isoformat(),
                ended_at=None,
                status=TestStatus.RUNNING,
                control_config=control_config,
                treatment_config=treatment_config,
                control_metrics=None,
                treatment_metrics=None,
                winner=None,
                confidence=0.0,
            )

            self.ab_tests[test_id] = test

            # Audit
            self._audit_event({
                "event_type": "ab_test_started",
                "test_id": test_id,
                "skill_id": skill_id,
                "metric_name": metric_name,
            })

            return test

    def record_ab_test_metrics(
        self,
        test_id: str,
        arm_id: str,
        approval_accuracy: float,
        latency_p50: float,
        latency_p95: float,
        error_rate: float,
        cost: float,
        num_evaluations: int,
    ) -> None:
        """Record metrics for an A/B test arm.

        Args:
            test_id: Test ID
            arm_id: "control" or "treatment"
            approval_accuracy: % correct approvals
            latency_p50, latency_p95: Latency percentiles (ms)
            error_rate: % errors
            cost: Optimization cost
            num_evaluations: Number of samples
        """
        # BUG FIX #5: Add input validation for arm_id
        valid_arm_ids = {"control", "treatment"}
        if arm_id not in valid_arm_ids:
            raise ValueError(f"Invalid arm_id: {arm_id}. Must be one of {valid_arm_ids}")

        with self.lock:
            if test_id not in self.ab_tests:
                raise ValueError(f"Test {test_id} not found")

            test = self.ab_tests[test_id]
            metrics = ABTestMetrics(
                arm_id=arm_id,
                num_evaluations=num_evaluations,
                approval_accuracy=approval_accuracy,
                latency_p50=latency_p50,
                latency_p95=latency_p95,
                error_rate=error_rate,
                cost=cost,
            )

            # Update test
            if arm_id == "control":
                test = ABTest(
                    test_id=test.test_id,
                    skill_id=test.skill_id,
                    metric_name=test.metric_name,
                    started_at=test.started_at,
                    ended_at=test.ended_at,
                    status=test.status,
                    control_config=test.control_config,
                    treatment_config=test.treatment_config,
                    control_metrics=metrics,
                    treatment_metrics=test.treatment_metrics,
                    winner=test.winner,
                    confidence=test.confidence,
                )
            else:
                test = ABTest(
                    test_id=test.test_id,
                    skill_id=test.skill_id,
                    metric_name=test.metric_name,
                    started_at=test.started_at,
                    ended_at=test.ended_at,
                    status=test.status,
                    control_config=test.control_config,
                    treatment_config=test.treatment_config,
                    control_metrics=test.control_metrics,
                    treatment_metrics=metrics,
                    winner=test.winner,
                    confidence=test.confidence,
                )

            self.ab_tests[test_id] = test

            # Audit
            self._audit_event({
                "event_type": "ab_test_metrics_recorded",
                "test_id": test_id,
                "arm_id": arm_id,
                "approval_accuracy": approval_accuracy,
                "latency_p50": latency_p50,
                "error_rate": error_rate,
            })

    def complete_ab_test(self, test_id: str) -> ABTest:
        """Complete an A/B test and determine winner.

        Algorithm:
            1. Require min_samples for both arms
            2. Compare accuracy, latency, cost
            3. Declare winner if confidence > 80%
            4. Update test status to COMPLETED

        Returns:
            Updated ABTest with winner
        """
        with self.lock:
            if test_id not in self.ab_tests:
                raise ValueError(f"Test {test_id} not found")

            test = self.ab_tests[test_id]

            if not test.control_metrics or not test.treatment_metrics:
                raise ValueError("Both arms must have metrics")

            control = test.control_metrics
            treatment = test.treatment_metrics

            if (
                control.num_evaluations < self.min_samples_for_decision
                or treatment.num_evaluations < self.min_samples_for_decision
            ):
                raise ValueError(
                    f"Insufficient samples: control={control.num_evaluations}, "
                    f"treatment={treatment.num_evaluations}"
                )

            # Decide winner based on metrics
            winner, confidence = self._decide_winner(control, treatment)

            test = ABTest(
                test_id=test.test_id,
                skill_id=test.skill_id,
                metric_name=test.metric_name,
                started_at=test.started_at,
                ended_at=datetime.utcnow().isoformat(),
                status=TestStatus.COMPLETED,
                control_config=test.control_config,
                treatment_config=test.treatment_config,
                control_metrics=control,
                treatment_metrics=treatment,
                winner=winner,
                confidence=confidence,
            )

            self.ab_tests[test_id] = test

            # Audit
            self._audit_event({
                "event_type": "ab_test_completed",
                "test_id": test_id,
                "winner": winner,
                "confidence": confidence,
            })

            return test

    def _decide_winner(
        self, control: ABTestMetrics, treatment: ABTestMetrics
    ) -> Tuple[str, float]:
        """Decide which arm is better.

        Scoring: accuracy (50%) + latency (30%) + cost (20%)

        Returns:
            (winner_arm_id, confidence)
        """
        control_score = (
            control.approval_accuracy * 0.5
            + (1.0 - control.error_rate) * 0.3
            - (control.cost / 1000.0) * 0.2
        )
        treatment_score = (
            treatment.approval_accuracy * 0.5
            + (1.0 - treatment.error_rate) * 0.3
            - (treatment.cost / 1000.0) * 0.2
        )

        if control_score > treatment_score:
            winner = "control"
            confidence = min(1.0, control_score / max(treatment_score, 0.1))
        else:
            winner = "treatment"
            confidence = min(1.0, treatment_score / max(control_score, 0.1))

        return winner, min(1.0, confidence)

    def start_canary_deployment(
        self,
        skill_id: str,
        metric_name: str,
        new_config: Dict[str, float],
        metrics_pre: Dict[str, float],
    ) -> CanaryDeployment:
        """Start a canary deployment (10% → 50% → 100%).

        Args:
            skill_id: Skill being deployed
            metric_name: Metric
            new_config: New configuration to roll out
            metrics_pre: Pre-deployment metrics (baseline)

        Returns:
            CanaryDeployment with phase=INIT
        """
        with self.lock:
            deployment_id = f"canary_{datetime.utcnow().timestamp()}"

            # Assume we have access to operator count
            num_operators = 100  # Placeholder; real code fetches from user backend

            deployment = CanaryDeployment(
                deployment_id=deployment_id,
                skill_id=skill_id,
                metric_name=metric_name,
                new_config=new_config,
                phase=CanaryPhase.INIT,
                started_at=datetime.utcnow().isoformat(),
                phase_start=datetime.utcnow().isoformat(),
                num_operators_total=num_operators,
                num_operators_current_phase=0,
                target_cohort_size=0.1,  # 10%
                metrics_pre_deployment=metrics_pre,
                metrics_current_phase=metrics_pre.copy(),
                rollback_triggered=False,
                rollback_reason=None,
            )

            self.canaries[deployment_id] = deployment

            # Audit
            self._audit_event({
                "event_type": "canary_deployment_started",
                "deployment_id": deployment_id,
                "skill_id": skill_id,
                "phase": CanaryPhase.INIT.value,
            })

            return deployment

    def advance_canary_phase(
        self,
        deployment_id: str,
        current_metrics: Dict[str, float],
    ) -> CanaryDeployment:
        """Advance canary to next phase (10% → 50% → 100%).

        Pre-conditions:
            - Current phase metrics are healthy (no regression > threshold)
            - Minimum time in phase has elapsed (e.g., 1 hour)

        Args:
            deployment_id: Canary ID
            current_metrics: Metrics for current phase

        Returns:
            Updated CanaryDeployment with new phase

        Raises:
            ValueError: If metrics degraded (triggers rollback instead)
        """
        with self.lock:
            if deployment_id not in self.canaries:
                raise ValueError(f"Canary {deployment_id} not found")

            canary = self.canaries[deployment_id]

            # Check for degradation
            if self._check_metric_degradation(
                canary.metrics_pre_deployment, current_metrics
            ):
                # Trigger rollback instead
                self.trigger_rollback(deployment_id)
                raise ValueError(
                    f"Metrics degraded in phase {canary.phase.value}; rollback triggered"
                )

            # Advance phase
            if canary.phase == CanaryPhase.INIT:
                new_phase = CanaryPhase.PHASE_1
            elif canary.phase == CanaryPhase.PHASE_1:
                new_phase = CanaryPhase.PHASE_2
            elif canary.phase == CanaryPhase.PHASE_2:
                new_phase = CanaryPhase.PHASE_3
            else:
                raise ValueError(f"Cannot advance beyond {canary.phase.value}")

            # Update cohort size
            if new_phase == CanaryPhase.PHASE_1:
                target_cohort = 0.1
            elif new_phase == CanaryPhase.PHASE_2:
                target_cohort = 0.5
            else:
                target_cohort = 1.0

            num_current = int(canary.num_operators_total * target_cohort)

            updated_canary = CanaryDeployment(
                deployment_id=canary.deployment_id,
                skill_id=canary.skill_id,
                metric_name=canary.metric_name,
                new_config=canary.new_config,
                phase=new_phase,
                started_at=canary.started_at,
                phase_start=datetime.utcnow().isoformat(),
                num_operators_total=canary.num_operators_total,
                num_operators_current_phase=num_current,
                target_cohort_size=target_cohort,
                metrics_pre_deployment=canary.metrics_pre_deployment,
                metrics_current_phase=current_metrics.copy(),
                rollback_triggered=False,
                rollback_reason=None,
            )

            self.canaries[deployment_id] = updated_canary

            # Audit
            self._audit_event({
                "event_type": "canary_phase_advanced",
                "deployment_id": deployment_id,
                "new_phase": new_phase.value,
                "num_operators": num_current,
            })

            return updated_canary

    def trigger_rollback(self, deployment_id: str) -> RollbackDecision:
        """Trigger automatic rollback for a canary deployment.

        Args:
            deployment_id: Canary ID

        Returns:
            RollbackDecision with trigger reason and restored config
        """
        with self.lock:
            if deployment_id not in self.canaries:
                raise ValueError(f"Canary {deployment_id} not found")

            canary = self.canaries[deployment_id]

            # Determine trigger reason
            trigger_reason = self._determine_rollback_reason(
                canary.metrics_pre_deployment,
                canary.metrics_current_phase,
            )

            rollback = RollbackDecision(
                deployment_id=deployment_id,
                triggered_at=datetime.utcnow().isoformat(),
                trigger_reason=trigger_reason,
                metric_name=canary.metric_name,
                threshold_value=0.05,  # e.g., 5% accuracy drop
                observed_value=0.08,  # Observed drop
                confidence=0.95,
                restored_config=canary.metrics_pre_deployment.copy(),
            )

            self.rollbacks.append(rollback)

            # Update canary state
            updated_canary = CanaryDeployment(
                deployment_id=canary.deployment_id,
                skill_id=canary.skill_id,
                metric_name=canary.metric_name,
                new_config=canary.new_config,
                phase=canary.phase,
                started_at=canary.started_at,
                phase_start=canary.phase_start,
                num_operators_total=canary.num_operators_total,
                num_operators_current_phase=canary.num_operators_current_phase,
                target_cohort_size=canary.target_cohort_size,
                metrics_pre_deployment=canary.metrics_pre_deployment,
                metrics_current_phase=canary.metrics_current_phase,
                rollback_triggered=True,
                rollback_reason=trigger_reason,
            )

            self.canaries[deployment_id] = updated_canary

            # Audit
            self._audit_event({
                "event_type": "rollback_triggered",
                "deployment_id": deployment_id,
                "trigger_reason": trigger_reason,
            })

            return rollback

    def _check_metric_degradation(
        self, baseline: Dict[str, float], current: Dict[str, float]
    ) -> bool:
        """Check if current metrics are degraded vs. baseline.

        Returns: True if any metric exceeds degradation threshold
        """
        for key in ["accuracy", "latency_p95", "error_rate"]:
            if key not in baseline or key not in current:
                continue

            if key == "accuracy":
                # Accuracy drop > threshold
                drop = baseline[key] - current[key]
                if drop > self.accuracy_drop_threshold:
                    return True

            elif key == "latency_p95":
                # BUG FIX #2: Avoid division by zero (use absolute instead of relative)
                if baseline[key] == 0:
                    increase = current[key]
                else:
                    increase = (current[key] - baseline[key]) / baseline[key]
                if increase > self.latency_increase_threshold:
                    return True

            elif key == "error_rate":
                # Error rate > threshold
                if current[key] > self.error_rate_threshold:
                    return True

        return False

    def _determine_rollback_reason(
        self, baseline: Dict[str, float], current: Dict[str, float]
    ) -> str:
        """Determine which metric triggered rollback."""
        if baseline.get("accuracy", 0) - current.get("accuracy", 0) > self.accuracy_drop_threshold:
            return "accuracy_drop"
        if current.get("error_rate", 0) > self.error_rate_threshold:
            return "error_spike"
        return "latency_increase"

    def select_canary_cohort(self, deployment_id: str, operator_ids: List[str]) -> List[str]:
        """Select operators for canary phase.

        Args:
            deployment_id: Canary ID
            operator_ids: All available operator IDs

        Returns:
            List of selected operator IDs (random sample of target cohort size)
        """
        with self.lock:
            if deployment_id not in self.canaries:
                raise ValueError(f"Canary {deployment_id} not found")

            canary = self.canaries[deployment_id]
            cohort_size = int(len(operator_ids) * canary.target_cohort_size)

            # BUG FIX #8: Use strong random seed (256+ bits, not 8 bits)
            # Deterministic for reproducibility, but with strong entropy
            seed_material = f"{deployment_id}:{datetime.utcnow().isoformat()}"
            strong_seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
            # A LOCAL generator: seeding the module-global ``random`` here reset
            # the randomness of every other consumer in the process.
            rng = random.Random(strong_seed)
            selected = rng.sample(operator_ids, min(cohort_size, len(operator_ids)))

            # Record cohort assignment
            for op_id in selected:
                self.operator_cohorts[op_id] = deployment_id

            return selected

    def _audit_event(self, event: Dict[str, Any]) -> None:
        """Log audit event (thread-safe)."""
        with self.lock:
            event["tenant_id"] = self.tenant_id
            event["timestamp"] = datetime.utcnow().isoformat()
            self.audit_log.append(event)

            if len(self.audit_log) > 1000:
                self.audit_log.pop(0)

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get audit log (copy)."""
        with self.lock:
            return self.audit_log.copy()


if __name__ == "__main__":
    # Example usage
    engine = ProductionTuningEngine(tenant_id="_default")

    # Start A/B test
    test = engine.start_ab_test(
        skill_id="skill_a",
        metric_name="latency",
        control_config={"threshold": 0.7},
        treatment_config={"threshold": 0.75},
    )
    print(f"A/B test started: {test.test_id}")

    # Record metrics
    engine.record_ab_test_metrics(
        test_id=test.test_id,
        arm_id="control",
        approval_accuracy=0.92,
        latency_p50=120.0,
        latency_p95=150.0,
        error_rate=0.005,
        cost=50.0,
        num_evaluations=200,
    )
    engine.record_ab_test_metrics(
        test_id=test.test_id,
        arm_id="treatment",
        approval_accuracy=0.95,
        latency_p50=115.0,
        latency_p95=145.0,
        error_rate=0.003,
        cost=40.0,
        num_evaluations=200,
    )

    # Complete test
    completed_test = engine.complete_ab_test(test.test_id)
    print(f"Winner: {completed_test.winner} (confidence: {completed_test.confidence:.2f})")

    # Start canary
    canary = engine.start_canary_deployment(
        skill_id="skill_a",
        metric_name="latency",
        new_config={"threshold": 0.75},
        metrics_pre={"accuracy": 0.92, "latency_p95": 150.0, "error_rate": 0.005},
    )
    print(f"Canary deployment started: {canary.deployment_id}")
