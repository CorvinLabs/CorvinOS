"""A/B-Testing Framework for OS-Skills (ADR-0511 Phase 2).

Implements:
1. Experiment runner with variant assignment
2. Tenant cohort assignment (stable hash)
3. Metrics collection (latency, cost, quality)
4. Statistical significance testing (chi-square, p<0.05)
5. Auto-rollout when variant wins

ADR-0511: Marketplace Plugin-First Architecture
ADR-0533: OS-Skill Manifest Schema & Versioning (canary deployment)
ADR-0314: Learning Infrastructure (event emission & metrics collection)
ADR-0722: DoD Loss Signal Learning Integration

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Callable
from uuid import uuid4

# numpy and scipy are optional for advanced statistical tests
try:
    import numpy as np
    from scipy.stats import chi2_contingency, ttest_ind
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    np = None

logger = logging.getLogger(__name__)

#: Recommendation / winner reason when a group has fewer than two samples.
INSUFFICIENT_STATS = "insufficient_stats"

#: Positive field allowlists for this module's chain events (the core writer
#: is default-deny on keys).
_AB_AUDIT_FIELDS: Dict[str, frozenset] = {
    "ab_experiment_created": frozenset({"experiment_id", "skill_id", "baseline_version",
                                        "variant_version", "sample_size", "significance_threshold"}),
    "ab_analysis_started": frozenset({"experiment_id", "skill_id", "sample_size_control",
                                      "sample_size_variant"}),
    "ab_analysis_completed": frozenset({"experiment_id", "skill_id", "winner", "confidence",
                                        "pvalue", "effect_size"}),
    "ab_auto_rollout_promoted": frozenset({"experiment_id", "skill_id", "variant_version",
                                           "confidence"}),
    "ab_auto_rollout_reverted": frozenset({"experiment_id", "skill_id", "variant_version",
                                           "regression", "threshold"}),
}


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _betainc_reg(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b), stdlib only."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def welch_t_test(control: List[float], variant: List[float]) -> Tuple[Optional[float], float]:
    """Two-sided Welch t-test with a real t-distribution p-value (stdlib math).

    Returns ``(pvalue, cohens_d)``. ``pvalue`` is ``None`` when either group
    has fewer than two samples — "not enough data" must never read as
    "no difference" (p = 1.0), which is what the scipy-less fallback used
    to report. Zero variance in both groups is exact: p = 0.0 when the means
    differ, 1.0 when they are equal.
    """
    n1, n2 = len(control), len(variant)
    if n1 < 2 or n2 < 2:
        return (None, 0.0)
    m1, m2 = statistics.fmean(control), statistics.fmean(variant)
    v1, v2 = statistics.variance(control), statistics.variance(variant)
    pooled = math.sqrt((v1 + v2) / 2.0)
    d = (m2 - m1) / pooled if pooled > 0 else 0.0
    se2 = v1 / n1 + v2 / n2
    if se2 == 0.0:
        return (0.0 if m1 != m2 else 1.0, d)
    t = (m2 - m1) / math.sqrt(se2)
    denom = 0.0
    if v1 > 0:
        denom += (v1 / n1) ** 2 / (n1 - 1)
    if v2 > 0:
        denom += (v2 / n2) ** 2 / (n2 - 1)
    df = se2 ** 2 / denom
    p = _betainc_reg(df / 2.0, 0.5, df / (df + t * t))
    return (min(max(p, 0.0), 1.0), d)


class ExperimentStatus(str, Enum):
    """Experiment lifecycle states."""
    PLANNING = "planning"       # Variant not yet running
    RUNNING = "running"         # Collecting metrics
    ANALYZING = "analyzing"     # Computing statistical significance
    COMPLETED = "completed"     # Variant selected (winner or loser)
    ROLLED_BACK = "rolled_back" # Variant failed, reverted


class CohortAssignment(str, Enum):
    """Cohort assignment result."""
    CONTROL = "control"         # Receives baseline skill version
    VARIANT = "variant"         # Receives new skill variant


@dataclass(frozen=True)
class ExperimentConfig:
    """Immutable A/B test configuration."""
    experiment_id: str
    skill_id: str
    baseline_version: str
    variant_version: str
    variant_name: str           # e.g., "Claude Opus Routing"

    # Experiment parameters
    sample_size_per_variant: int = 1000
    min_runtime_days: int = 3
    significance_threshold: float = 0.05  # p < 0.05
    success_criteria: Dict[str, float] = field(default_factory=dict)  # e.g., {"latency": -0.1, "quality": 0.05}

    # Rollout strategy
    rollout_percentage: int = 10  # Start with 10% traffic
    rollback_on_regression: bool = True
    max_regression_pct: float = 0.15  # Auto-revert if regression > 15%

    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ExperimentMetrics:
    """Mutable experiment metrics (updated during collection)."""
    experiment_id: str
    variant_id: str

    # Observations
    observations_control: List[float] = field(default_factory=list)
    observations_variant: List[float] = field(default_factory=list)

    # Aggregates
    latency_ms_control: float = 0.0
    latency_ms_variant: float = 0.0
    cost_per_token_control: float = 0.0
    cost_per_token_variant: float = 0.0
    quality_score_control: float = 0.0
    quality_score_variant: float = 0.0

    # Statistical
    pvalue: Optional[float] = None
    effect_size: Optional[float] = None
    is_significant: bool = False

    # Metadata
    sample_size_control: int = 0
    sample_size_variant: int = 0
    collected_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ExperimentResult:
    """Immutable experiment result."""
    experiment_id: str
    skill_id: str
    winner: str  # "control", "variant", or "inconclusive"
    confidence: float  # 0-1, confidence in decision
    metrics: ExperimentMetrics
    recommendation: str
    completed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class CohortAssigner:
    """Assign tenants to control/variant cohorts using stable hash."""

    def __init__(self, experiment_id: str):
        self.experiment_id = experiment_id

    def assign(self, tenant_id: str, variant_percentage: int = 10) -> CohortAssignment:
        """
        Assign tenant to control or variant cohort using stable hash.

        Args:
            tenant_id: Tenant identifier
            variant_percentage: % of traffic to route to variant (default: 10%)

        Returns:
            CohortAssignment.CONTROL or CohortAssignment.VARIANT
        """
        # Stable hash: same input always produces same output
        hash_input = f"{self.experiment_id}:{tenant_id}"
        hash_obj = hashlib.sha256(hash_input.encode())
        hash_value = int(hash_obj.hexdigest(), 16)

        # Map hash to 0-99 range
        cohort_value = hash_value % 100

        if cohort_value < variant_percentage:
            return CohortAssignment.VARIANT
        else:
            return CohortAssignment.CONTROL


class MetricsCollector:
    """Collect metrics from skill executions."""

    def __init__(self, experiment_id: str):
        self.experiment_id = experiment_id
        self.metrics = ExperimentMetrics(
            experiment_id=experiment_id,
            variant_id=str(uuid4()),
        )

    def record_execution(
        self,
        cohort: CohortAssignment,
        latency_ms: float,
        cost_per_token: float,
        quality_score: float,
    ) -> None:
        """Record a single skill execution metric."""
        if cohort == CohortAssignment.CONTROL:
            self.metrics.observations_control.append(latency_ms)
            self.metrics.sample_size_control += 1
            self.metrics.latency_ms_control = statistics.mean(self.metrics.observations_control)
            n = self.metrics.sample_size_control
            self.metrics.cost_per_token_control += (cost_per_token - self.metrics.cost_per_token_control) / n
            self.metrics.quality_score_control += (quality_score - self.metrics.quality_score_control) / n
        else:
            self.metrics.observations_variant.append(latency_ms)
            self.metrics.sample_size_variant += 1
            self.metrics.latency_ms_variant = statistics.mean(self.metrics.observations_variant)
            n = self.metrics.sample_size_variant
            self.metrics.cost_per_token_variant += (cost_per_token - self.metrics.cost_per_token_variant) / n
            self.metrics.quality_score_variant += (quality_score - self.metrics.quality_score_variant) / n

    def get_metrics(self) -> ExperimentMetrics:
        """Get current aggregated metrics.

        cost/quality are kept as running means by ``record_execution``. They
        used to be accumulated as sums and divided here — but the framework
        feeds the already-divided value back in on the next record, so each
        call computed (mean + x) / n and the mean decayed toward 0 with sample
        size. The 90% control cohort therefore always read as far lower
        quality than the 10% variant, whatever the real scores were.
        """
        return self.metrics


class StatisticalTest:
    """Statistical significance testing (chi-square, p<0.05)."""

    @staticmethod
    def chi_square_test(
        observations_control: List[float],
        observations_variant: List[float],
    ) -> Tuple[Optional[float], float]:
        """
        Perform chi-square test for statistical significance.

        Args:
            observations_control: Control group observations
            observations_variant: Variant group observations

        Returns:
            (pvalue, effect_size)
        """
        if len(observations_control) < 2 or len(observations_variant) < 2:
            return (None, 0.0)  # insufficient_stats — never "p = 1.0"
        if not HAS_SCIPY:
            return (None, 0.0)  # not measurable without scipy — never "p = 1.0"

        # Bin observations into categories (e.g., latency ranges)
        all_obs = observations_control + observations_variant
        bins = np.histogram_bin_edges(all_obs, bins=5)

        # Create contingency table
        control_hist, _ = np.histogram(observations_control, bins=bins)
        variant_hist, _ = np.histogram(observations_variant, bins=bins)

        contingency_table = np.array([control_hist, variant_hist])

        try:
            chi2, pvalue, dof, expected = chi2_contingency(contingency_table)

            # Effect size (Cramér's V)
            n = contingency_table.sum()
            min_dim = min(contingency_table.shape) - 1
            cramers_v = np.sqrt(chi2 / (n * min_dim)) if min_dim > 0 else 0.0

            return (pvalue, cramers_v)
        except Exception as e:
            logger.error(f"Chi-square test failed: {e}")
            return (None, 0.0)  # failed ≠ "no difference"

    @staticmethod
    def t_test(
        observations_control: List[float],
        observations_variant: List[float],
    ) -> Tuple[Optional[float], float]:
        """Welch t-test (see :func:`welch_t_test`); ``pvalue`` None = insufficient_stats.

        This used to call scipy's (Student) ``ttest_ind`` and, when scipy was
        absent or anything raised, return ``(1.0, 0.0)`` — an unmeasured test
        reported as a measured "no difference".
        """
        return welch_t_test(list(observations_control), list(observations_variant))


class ABTestingFramework:
    """End-to-end A/B testing framework for OS-Skills."""

    def __init__(
        self,
        test_dir: Optional[Path] = None,
        audit_emit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        """
        Initialize A/B testing framework.

        Args:
            test_dir: Directory for test data (default: ~/.corvin/ab_tests/)
            audit_emit: Audit event emitter (reaches compliance chain, GDPR Art. 30/32)
        """
        if test_dir is None:
            # Honour CORVIN_HOME (CLAUDE.md: never hard-wire ~/.corvin in skills).
            from core.paths.tenant import corvin_home  # noqa: PLC0415

            test_dir = corvin_home() / "ab_tests"
        self.test_dir = Path(test_dir)
        self.audit_emit = audit_emit or self._default_audit_emit
        self.test_dir.mkdir(parents=True, exist_ok=True)

        # Load existing experiments
        self.experiments: Dict[str, ExperimentConfig] = self._load_experiments()
        self.metrics: Dict[str, ExperimentMetrics] = self._load_metrics()

    def _default_audit_emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Append to the tenant's hash-chained audit log (audit-FIRST).

        This used to only ``logger.info`` the event while the docstring claimed
        a compliance chain. A failed chain write now raises, so the audited
        operation (experiment creation, analysis, rollout) does not proceed.
        """
        from ._chain_audit import chain_write  # noqa: PLC0415

        chain_write(event_type, payload, fields=_AB_AUDIT_FIELDS.get(event_type, frozenset()))

    def _load_experiments(self) -> Dict[str, ExperimentConfig]:
        """Load existing experiment configurations."""
        experiments_path = self.test_dir / "experiments.json"
        if not experiments_path.exists():
            return {}

        try:
            with open(experiments_path, "r") as f:
                data = json.load(f)

            experiments = {}
            for exp_id, exp_data in data.get("experiments", {}).items():
                experiments[exp_id] = ExperimentConfig(
                    experiment_id=exp_id,
                    skill_id=exp_data["skill_id"],
                    baseline_version=exp_data["baseline_version"],
                    variant_version=exp_data["variant_version"],
                    variant_name=exp_data["variant_name"],
                    sample_size_per_variant=exp_data.get("sample_size_per_variant", 1000),
                    min_runtime_days=exp_data.get("min_runtime_days", 3),
                    significance_threshold=exp_data.get("significance_threshold", 0.05),
                    success_criteria=exp_data.get("success_criteria", {}),
                    rollout_percentage=exp_data.get("rollout_percentage", 10),
                    rollback_on_regression=exp_data.get("rollback_on_regression", True),
                    max_regression_pct=exp_data.get("max_regression_pct", 0.15),
                    created_at=exp_data.get("created_at", ""),
                )
            return experiments
        except Exception as e:
            logger.warning(f"Failed to load experiments: {e}")
            return {}

    def _load_metrics(self) -> Dict[str, ExperimentMetrics]:
        """Load existing experiment metrics."""
        metrics_path = self.test_dir / "metrics.json"
        if not metrics_path.exists():
            return {}

        try:
            with open(metrics_path, "r") as f:
                data = json.load(f)

            metrics = {}
            for exp_id, metric_data in data.get("metrics", {}).items():
                m = ExperimentMetrics(
                    experiment_id=metric_data["experiment_id"],
                    variant_id=metric_data["variant_id"],
                    observations_control=metric_data.get("observations_control", []),
                    observations_variant=metric_data.get("observations_variant", []),
                    latency_ms_control=metric_data.get("latency_ms_control", 0.0),
                    latency_ms_variant=metric_data.get("latency_ms_variant", 0.0),
                    cost_per_token_control=metric_data.get("cost_per_token_control", 0.0),
                    cost_per_token_variant=metric_data.get("cost_per_token_variant", 0.0),
                    quality_score_control=metric_data.get("quality_score_control", 0.0),
                    quality_score_variant=metric_data.get("quality_score_variant", 0.0),
                    pvalue=metric_data.get("pvalue"),
                    effect_size=metric_data.get("effect_size"),
                    is_significant=metric_data.get("is_significant", False),
                    sample_size_control=metric_data.get("sample_size_control", 0),
                    sample_size_variant=metric_data.get("sample_size_variant", 0),
                )
                metrics[exp_id] = m
            return metrics
        except Exception as e:
            logger.warning(f"Failed to load metrics: {e}")
            return {}

    def create_experiment(self, config: ExperimentConfig) -> ExperimentConfig:
        """Create a new A/B test experiment."""
        # Audit-FIRST: log experiment creation
        self.audit_emit("ab_experiment_created", {
            "experiment_id": config.experiment_id,
            "skill_id": config.skill_id,
            "baseline_version": config.baseline_version,
            "variant_version": config.variant_version,
            "sample_size": config.sample_size_per_variant,
            "significance_threshold": config.significance_threshold,
        })

        self.experiments[config.experiment_id] = config
        self._save_experiments()

        logger.info(f"✅ Experiment created: {config.experiment_id}")
        return config

    def assign_cohort(self, experiment_id: str, tenant_id: str) -> CohortAssignment:
        """
        Assign tenant to control or variant cohort for an experiment.

        Args:
            experiment_id: Experiment ID
            tenant_id: Tenant identifier

        Returns:
            CohortAssignment.CONTROL or CohortAssignment.VARIANT
        """
        if experiment_id not in self.experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        config = self.experiments[experiment_id]
        assigner = CohortAssigner(experiment_id)
        cohort = assigner.assign(tenant_id, config.rollout_percentage)

        # Log cohort assignment (non-compliance event, for metrics only)
        logger.debug(f"Tenant {tenant_id} assigned to {cohort.value} for {experiment_id}")

        return cohort

    def record_metric(
        self,
        experiment_id: str,
        tenant_id: str,
        latency_ms: float,
        cost_per_token: float,
        quality_score: float,
    ) -> None:
        """
        Record a skill execution metric.

        Args:
            experiment_id: Experiment ID
            tenant_id: Tenant ID (used for cohort assignment)
            latency_ms: Execution latency in milliseconds
            cost_per_token: Cost per token
            quality_score: Quality score (0-1)
        """
        if experiment_id not in self.experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        # Get or create metrics collection
        if experiment_id not in self.metrics:
            self.metrics[experiment_id] = ExperimentMetrics(
                experiment_id=experiment_id,
                variant_id=str(uuid4()),
            )

        # Assign cohort and record
        cohort = self.assign_cohort(experiment_id, tenant_id)
        collector = MetricsCollector(experiment_id)
        collector.metrics = self.metrics[experiment_id]
        collector.record_execution(cohort, latency_ms, cost_per_token, quality_score)
        self.metrics[experiment_id] = collector.get_metrics()

        self._save_metrics()

    def analyze_experiment(self, experiment_id: str) -> ExperimentResult:
        """
        Analyze experiment results and determine winner.

        Returns:
            ExperimentResult with recommendation
        """
        if experiment_id not in self.experiments:
            raise ValueError(f"Experiment not found: {experiment_id}")

        if experiment_id not in self.metrics:
            raise ValueError(f"No metrics collected for experiment: {experiment_id}")

        config = self.experiments[experiment_id]
        metrics = self.metrics[experiment_id]

        # Audit-FIRST: log analysis
        self.audit_emit("ab_analysis_started", {
            "experiment_id": experiment_id,
            "skill_id": config.skill_id,
            "sample_size_control": metrics.sample_size_control,
            "sample_size_variant": metrics.sample_size_variant,
        })

        # Perform statistical test
        pvalue, effect_size = StatisticalTest.t_test(
            metrics.observations_control,
            metrics.observations_variant,
        )

        metrics.pvalue = pvalue
        metrics.effect_size = effect_size
        metrics.is_significant = pvalue is not None and pvalue < config.significance_threshold

        # Determine winner based on success criteria
        winner = "inconclusive"
        confidence = 0.0
        recommendation = ""

        if metrics.is_significant:
            # Check if variant meets success criteria
            latency_improvement = (
                (metrics.latency_ms_control - metrics.latency_ms_variant)
                / metrics.latency_ms_control
                if metrics.latency_ms_control > 0
                else 0.0
            )

            quality_improvement = (
                (metrics.quality_score_variant - metrics.quality_score_control)
                / max(metrics.quality_score_control, 0.001)
            )

            latency_criteria = config.success_criteria.get("latency", 0.0)
            quality_criteria = config.success_criteria.get("quality", 0.0)

            if (
                latency_improvement >= latency_criteria
                and quality_improvement >= quality_criteria
            ):
                winner = "variant"
                confidence = 1.0 - pvalue
                recommendation = f"Promote variant (p={pvalue:.4f}, effect_size={effect_size:.2f})"
            else:
                winner = "control"
                confidence = 1.0 - pvalue
                recommendation = "Variant did not meet success criteria"
        elif pvalue is None:
            winner = "inconclusive"
            confidence = 0.0
            recommendation = f"{INSUFFICIENT_STATS}: each cohort needs at least 2 samples"
        else:
            winner = "inconclusive"
            confidence = 0.0
            recommendation = f"Insufficient statistical significance (p={pvalue:.4f} >= {config.significance_threshold})"

        result = ExperimentResult(
            experiment_id=experiment_id,
            skill_id=config.skill_id,
            winner=winner,
            confidence=confidence,
            metrics=metrics,
            recommendation=recommendation,
        )

        # Emit audit event
        self.audit_emit("ab_analysis_completed", {
            "experiment_id": experiment_id,
            "skill_id": config.skill_id,
            "winner": winner,
            "confidence": confidence,
            "pvalue": pvalue,
            "effect_size": effect_size,
        })

        logger.info(f"✅ Analysis complete: {experiment_id} → {winner}")
        return result

    def auto_rollout(self, experiment_id: str) -> Optional[str]:
        """
        Auto-rollout variant to 100% if it wins.

        Returns:
            "promoted" if variant won, "rolled_back" if control won, None if inconclusive
        """
        result = self.analyze_experiment(experiment_id)
        config = self.experiments[experiment_id]

        if result.winner == "variant":
            # Promote variant to 100%
            self.audit_emit("ab_auto_rollout_promoted", {
                "experiment_id": experiment_id,
                "skill_id": config.skill_id,
                "variant_version": config.variant_version,
                "confidence": result.confidence,
            })
            return "promoted"

        elif result.winner == "control" and config.rollback_on_regression:
            # Rollback variant
            regression = (
                (result.metrics.latency_ms_variant - result.metrics.latency_ms_control)
                / result.metrics.latency_ms_control
                if result.metrics.latency_ms_control > 0
                else 0.0
            )

            if regression > config.max_regression_pct:
                self.audit_emit("ab_auto_rollout_reverted", {
                    "experiment_id": experiment_id,
                    "skill_id": config.skill_id,
                    "variant_version": config.variant_version,
                    "regression": regression,
                    "threshold": config.max_regression_pct,
                })
                return "rolled_back"

        return None

    def _save_experiments(self) -> None:
        """Persist experiments to disk."""
        experiments_path = self.test_dir / "experiments.json"
        data = {
            "version": "1.0",
            "last_updated": datetime.utcnow().isoformat(),
            "experiments": {
                exp_id: asdict(config)
                for exp_id, config in self.experiments.items()
            },
        }

        with open(experiments_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _save_metrics(self) -> None:
        """Persist metrics to disk."""
        metrics_path = self.test_dir / "metrics.json"
        data = {
            "version": "1.0",
            "last_updated": datetime.utcnow().isoformat(),
            "metrics": {
                exp_id: {
                    "experiment_id": m.experiment_id,
                    "variant_id": m.variant_id,
                    "observations_control": m.observations_control,
                    "observations_variant": m.observations_variant,
                    "latency_ms_control": m.latency_ms_control,
                    "latency_ms_variant": m.latency_ms_variant,
                    "cost_per_token_control": m.cost_per_token_control,
                    "cost_per_token_variant": m.cost_per_token_variant,
                    "quality_score_control": m.quality_score_control,
                    "quality_score_variant": m.quality_score_variant,
                    "pvalue": m.pvalue,
                    "effect_size": m.effect_size,
                    "is_significant": m.is_significant,
                    "sample_size_control": m.sample_size_control,
                    "sample_size_variant": m.sample_size_variant,
                }
                for exp_id, m in self.metrics.items()
            },
        }

        with open(metrics_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
