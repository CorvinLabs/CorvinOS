"""
Metrics calculation for scientific LLM benchmarking.

Implements CONCEPT-0047 metrics:
- Token Savings: (tokens_opus - tokens_routed) / tokens_opus * 100
- Latency Improvement: (latency_opus - latency_routed) / latency_opus * 100
- Quality Accuracy: #correct_outputs / N * 100
- Statistical validation: p-values, confidence intervals
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from scipy import stats
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TokenMetrics:
    """Token savings metrics."""
    total_tokens_opus: int  # Total tokens used by Opus baseline
    total_tokens_routed: int  # Total tokens used by routing

    mean_savings_pct: float  # Mean savings across all tasks
    median_savings_pct: float
    std_dev_savings: float
    p25_savings: float
    p50_savings: float
    p75_savings: float
    p95_savings: float

    ci_lower_95: float  # 95% confidence interval (lower bound)
    ci_upper_95: float  # 95% confidence interval (upper bound)

    pct_tasks_with_savings: float  # % of tasks where routed < Opus
    p_value: float  # Paired t-test p-value
    accepts_h1: bool  # True if p<0.05 AND mean_savings >= 20%

    savings_per_task: List[float]  # Individual task savings (for plotting)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class LatencyMetrics:
    """Latency improvement metrics."""
    total_latency_opus_ms: int
    total_latency_routed_ms: int

    mean_improvement_pct: float
    median_improvement_pct: float
    std_dev_improvement: float

    p50_latency_opus_ms: float
    p50_latency_routed_ms: float

    p90_latency_opus_ms: float
    p90_latency_routed_ms: float

    p99_latency_opus_ms: float
    p99_latency_routed_ms: float

    ci_lower_95: float
    ci_upper_95: float

    p_value: float  # Mann-Whitney U test p-value
    accepts_h1: bool  # True if p<0.05 AND mean >= 10% AND p99 improves

    latency_improvements_per_task: List[float]  # For plotting
    latencies_opus: List[float]
    latencies_routed: List[float]

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class QualityMetrics:
    """Quality/accuracy metrics."""
    accuracy_opus_pct: float  # Overall accuracy of Opus baseline
    accuracy_routed_pct: float  # Overall accuracy of routed system

    regression_pct: float  # accuracy_routed - accuracy_opus

    # By tier (SIMPLE/MEDIUM/COMPLEX)
    accuracy_by_tier: Dict[str, float]  # {"simple": 98.5, "medium": 97.2, "complex": 96.8}
    regression_by_tier: Dict[str, float]

    # By category (code/data/writing/etc.)
    accuracy_by_category: Dict[str, float]
    regression_by_category: Dict[str, float]

    ci_lower_95: float
    ci_upper_95: float

    p_value: float  # Chi-square test p-value
    accepts_h1: bool  # True if p<0.05 AND accuracy >= 98% AND regression <= 2%

    tier_regression_violations: List[str]  # Tiers where regression > 0 (quality dropped)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ConfoundMetrics:
    """Confounding factors (observed but not optimized for)."""
    routing_accuracy_pct: float  # % of tasks routed to expected tier

    model_distribution: Dict[str, float]  # {"haiku": 22%, "sonnet": 35%, "opus": 43%}

    cost_estimation_error_pct: float  # (actual - estimated) / actual

    error_rate_pct: float  # % of tasks that failed to complete

    mean_confidence: float  # Average routing confidence

    routing_signals: Dict[str, int]  # {"strong": 85, "medium": 60, "weak": 35}

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class AllMetrics:
    """Complete metrics bundle."""
    token_metrics: TokenMetrics
    latency_metrics: LatencyMetrics
    quality_metrics: QualityMetrics
    confound_metrics: ConfoundMetrics

    # Test timestamps
    baseline_run_timestamp: str
    routing_run_timestamp: str
    analysis_timestamp: str

    # Summary verdict
    accepts_h1_all_three: bool  # True if all three primary metrics accept H1
    summary: str  # Human-readable summary

    def to_dict(self) -> Dict:
        return {
            "token_metrics": self.token_metrics.to_dict(),
            "latency_metrics": self.latency_metrics.to_dict(),
            "quality_metrics": self.quality_metrics.to_dict(),
            "confound_metrics": self.confound_metrics.to_dict(),
            "baseline_run_timestamp": self.baseline_run_timestamp,
            "routing_run_timestamp": self.routing_run_timestamp,
            "analysis_timestamp": self.analysis_timestamp,
            "accepts_h1_all_three": self.accepts_h1_all_three,
            "summary": self.summary,
        }


def calculate_metrics(
    baseline_runs: List[Dict[str, Any]],
    routing_runs: List[Dict[str, Any]],
    quality_scores: List[Dict[str, Any]],
    baseline_timestamp: str,
    routing_timestamp: str,
) -> AllMetrics:
    """
    Calculate all metrics from baseline + routing runs.

    Args:
        baseline_runs: List of baseline (Opus) run results
        routing_runs: List of routing run results
        quality_scores: List of quality judge scores
        baseline_timestamp: When baseline was run
        routing_timestamp: When routing was run

    Returns:
        AllMetrics with all three metric groups + verdicts
    """
    # Merge runs by task_id
    runs_by_task = {}
    for run in baseline_runs:
        task_id = run["task_id"]
        if task_id not in runs_by_task:
            runs_by_task[task_id] = {}
        runs_by_task[task_id]["baseline"] = run

    for run in routing_runs:
        task_id = run["task_id"]
        if task_id not in runs_by_task:
            runs_by_task[task_id] = {}
        runs_by_task[task_id]["routed"] = run

    # Extract metrics
    token_metrics = _calculate_token_metrics(runs_by_task)
    latency_metrics = _calculate_latency_metrics(runs_by_task)
    quality_metrics = _calculate_quality_metrics(runs_by_task, quality_scores)
    confound_metrics = _calculate_confound_metrics(runs_by_task)

    # Determine verdict
    accepts_all_three = (
        token_metrics.accepts_h1 and
        latency_metrics.accepts_h1 and
        quality_metrics.accepts_h1
    )

    summary = _generate_summary(token_metrics, latency_metrics, quality_metrics)

    return AllMetrics(
        token_metrics=token_metrics,
        latency_metrics=latency_metrics,
        quality_metrics=quality_metrics,
        confound_metrics=confound_metrics,
        baseline_run_timestamp=baseline_timestamp,
        routing_run_timestamp=routing_timestamp,
        analysis_timestamp=_now_iso8601(),
        accepts_h1_all_three=accepts_all_three,
        summary=summary,
    )


def _calculate_token_metrics(runs_by_task: Dict[str, Dict]) -> TokenMetrics:
    """Calculate token savings metrics."""
    savings_list = []

    for task_id, runs in runs_by_task.items():
        if "baseline" not in runs or "routed" not in runs:
            continue

        baseline = runs["baseline"]
        routed = runs["routed"]

        tokens_opus = baseline.get("total_tokens", 0)
        tokens_routed = routed.get("total_tokens", 0)

        if tokens_opus > 0:
            savings_pct = ((tokens_opus - tokens_routed) / tokens_opus) * 100
            savings_list.append(savings_pct)

    if not savings_list:
        raise ValueError("No matching baseline/routed runs for token calculation")

    # Calculate statistics
    mean_savings = statistics.mean(savings_list)
    median_savings = statistics.median(savings_list)
    stdev = statistics.stdev(savings_list) if len(savings_list) > 1 else 0.0

    # Percentiles
    sorted_savings = sorted(savings_list)
    p25 = np.percentile(sorted_savings, 25)
    p50 = np.percentile(sorted_savings, 50)
    p75 = np.percentile(sorted_savings, 75)
    p95 = np.percentile(sorted_savings, 95)

    # 95% CI (bootstrapped)
    ci_lower, ci_upper = _bootstrap_ci(savings_list, alpha=0.05)

    # % of tasks with savings
    pct_with_savings = (sum(1 for s in savings_list if s > 0) / len(savings_list)) * 100

    # Paired t-test: H0: mean = 0, H1: mean >= 20%
    # One-tailed test: t > 0 and p < 0.025 (one-tailed p = two-tailed p / 2)
    t_stat = (mean_savings - 0) / (stdev / (len(savings_list) ** 0.5)) if stdev > 0 else 0
    p_value = stats.t.sf(t_stat, df=len(savings_list) - 1)  # Right-tailed p-value

    # H1 acceptance: p < 0.05 AND mean >= 20%
    accepts_h1 = (p_value < 0.05) and (mean_savings >= 20.0)

    # Total tokens
    total_opus = sum(runs["baseline"].get("total_tokens", 0) for runs in runs_by_task.values())
    total_routed = sum(runs["routed"].get("total_tokens", 0) for runs in runs_by_task.values())

    return TokenMetrics(
        total_tokens_opus=total_opus,
        total_tokens_routed=total_routed,
        mean_savings_pct=mean_savings,
        median_savings_pct=median_savings,
        std_dev_savings=stdev,
        p25_savings=p25,
        p50_savings=p50,
        p75_savings=p75,
        p95_savings=p95,
        ci_lower_95=ci_lower,
        ci_upper_95=ci_upper,
        pct_tasks_with_savings=pct_with_savings,
        p_value=p_value,
        accepts_h1=accepts_h1,
        savings_per_task=savings_list,
    )


def _calculate_latency_metrics(runs_by_task: Dict[str, Dict]) -> LatencyMetrics:
    """Calculate latency improvement metrics."""
    improvement_list = []
    latencies_opus = []
    latencies_routed = []

    for task_id, runs in runs_by_task.items():
        if "baseline" not in runs or "routed" not in runs:
            continue

        baseline = runs["baseline"]
        routed = runs["routed"]

        latency_opus = baseline.get("latency_ms", 0)
        latency_routed = routed.get("latency_ms", 0)

        latencies_opus.append(latency_opus)
        latencies_routed.append(latency_routed)

        if latency_opus > 0:
            improvement_pct = ((latency_opus - latency_routed) / latency_opus) * 100
            improvement_list.append(improvement_pct)

    if not improvement_list:
        raise ValueError("No matching baseline/routed runs for latency calculation")

    # Calculate statistics
    mean_improvement = statistics.mean(improvement_list)
    median_improvement = statistics.median(improvement_list)
    stdev = statistics.stdev(improvement_list) if len(improvement_list) > 1 else 0.0

    # 95% CI
    ci_lower, ci_upper = _bootstrap_ci(improvement_list, alpha=0.05)

    # Percentile latencies
    p50_opus = np.percentile(latencies_opus, 50)
    p50_routed = np.percentile(latencies_routed, 50)
    p90_opus = np.percentile(latencies_opus, 90)
    p90_routed = np.percentile(latencies_routed, 90)
    p99_opus = np.percentile(latencies_opus, 99)
    p99_routed = np.percentile(latencies_routed, 99)

    # Mann-Whitney U test: distribution-free comparison
    u_stat, p_value = stats.mannwhitneyu(latencies_opus, latencies_routed, alternative='greater')

    # H1 acceptance: p < 0.05 AND mean >= 10% AND p99 improves
    p99_improves = p99_routed < p99_opus
    accepts_h1 = (p_value < 0.05) and (mean_improvement >= 10.0) and p99_improves

    # Total latencies
    total_latency_opus = sum(latencies_opus)
    total_latency_routed = sum(latencies_routed)

    return LatencyMetrics(
        total_latency_opus_ms=total_latency_opus,
        total_latency_routed_ms=total_latency_routed,
        mean_improvement_pct=mean_improvement,
        median_improvement_pct=median_improvement,
        std_dev_improvement=stdev,
        p50_latency_opus_ms=p50_opus,
        p50_latency_routed_ms=p50_routed,
        p90_latency_opus_ms=p90_opus,
        p90_latency_routed_ms=p90_routed,
        p99_latency_opus_ms=p99_opus,
        p99_latency_routed_ms=p99_routed,
        ci_lower_95=ci_lower,
        ci_upper_95=ci_upper,
        p_value=p_value,
        accepts_h1=accepts_h1,
        latency_improvements_per_task=improvement_list,
        latencies_opus=latencies_opus,
        latencies_routed=latencies_routed,
    )


def _calculate_quality_metrics(
    runs_by_task: Dict[str, Dict],
    quality_scores: List[Dict[str, Any]],
) -> QualityMetrics:
    """Calculate quality/accuracy metrics."""
    # Build score lookup
    scores_by_task = {score["task_id"]: score for score in quality_scores}

    baseline_correct = 0
    routed_correct = 0
    total_tasks = 0

    accuracy_by_tier = {"simple": 0, "medium": 0, "complex": 0}
    accuracy_by_category = {}
    count_by_tier = {"simple": 0, "medium": 0, "complex": 0}
    count_by_category = {}

    for task_id, runs in runs_by_task.items():
        if "baseline" not in runs or "routed" not in runs:
            continue
        if task_id not in scores_by_task:
            continue

        score = scores_by_task[task_id]
        tier = runs["routed"].get("tier", "unknown")
        category = runs["routed"].get("category", "unknown")

        baseline_score = score.get("baseline_score", 0)  # 0=INCORRECT, 1=PARTIAL, 2=CORRECT
        routed_score = score.get("routed_score", 0)

        if baseline_score >= 2:  # CORRECT
            baseline_correct += 1
        if routed_score >= 2:  # CORRECT
            routed_correct += 1

        total_tasks += 1

        # By tier
        if tier in accuracy_by_tier:
            count_by_tier[tier] += 1
            accuracy_by_tier[tier] += (1 if routed_score >= 2 else 0)

        # By category
        if category not in count_by_category:
            count_by_category[category] = 0
            accuracy_by_category[category] = 0
        count_by_category[category] += 1
        accuracy_by_category[category] += (1 if routed_score >= 2 else 0)

    if total_tasks == 0:
        raise ValueError("No quality scores found")

    # Convert to percentages
    accuracy_opus_pct = (baseline_correct / total_tasks) * 100
    accuracy_routed_pct = (routed_correct / total_tasks) * 100
    regression_pct = accuracy_routed_pct - accuracy_opus_pct

    # By tier (convert to percentages)
    accuracy_by_tier_pct = {
        tier: (accuracy_by_tier[tier] / count_by_tier[tier] * 100)
        if count_by_tier[tier] > 0 else 0
        for tier in accuracy_by_tier
    }

    # By category (convert to percentages)
    accuracy_by_category_pct = {
        cat: (accuracy_by_category[cat] / count_by_category[cat] * 100)
        if count_by_category[cat] > 0 else 0
        for cat in accuracy_by_category
    }

    # Regression per tier (routed - baseline for that tier)
    regression_by_tier = {tier: 0 for tier in accuracy_by_tier}  # TODO: calculate properly
    regression_by_category = {cat: 0 for cat in accuracy_by_category}  # TODO: calculate properly

    # Check for tier regression violations
    tier_regression_violations = [
        tier for tier in regression_by_tier
        if regression_by_tier[tier] > 0
    ]

    # 95% CI (binomial proportion)
    ci_lower, ci_upper = _binomial_ci(routed_correct, total_tasks, alpha=0.05)

    # Chi-square test: H0: accuracy_routed = accuracy_opus
    # Contingency table: correct/incorrect vs. baseline/routed
    chi2, p_value, _, _ = stats.chi2_contingency([
        [baseline_correct, total_tasks - baseline_correct],
        [routed_correct, total_tasks - routed_correct],
    ])

    # H1 acceptance: p < 0.05 AND accuracy >= 98% AND regression <= 2%
    accepts_h1 = (p_value < 0.05) and (accuracy_routed_pct >= 98.0) and (regression_pct >= -2.0)

    return QualityMetrics(
        accuracy_opus_pct=accuracy_opus_pct,
        accuracy_routed_pct=accuracy_routed_pct,
        regression_pct=regression_pct,
        accuracy_by_tier=accuracy_by_tier_pct,
        regression_by_tier=regression_by_tier,
        accuracy_by_category=accuracy_by_category_pct,
        regression_by_category=regression_by_category,
        ci_lower_95=ci_lower,
        ci_upper_95=ci_upper,
        p_value=p_value,
        accepts_h1=accepts_h1,
        tier_regression_violations=tier_regression_violations,
    )


def _calculate_confound_metrics(runs_by_task: Dict[str, Dict]) -> ConfoundMetrics:
    """Calculate confounding factor metrics."""
    routing_correct = 0  # Tasks routed to expected tier
    total_tasks = 0

    model_dist = {"haiku": 0, "sonnet": 0, "opus": 0}
    cost_errors = []
    error_count = 0
    confidences = []
    signals = {"strong": 0, "medium": 0, "weak": 0}

    for task_id, runs in runs_by_task.items():
        if "routed" not in runs:
            continue

        routed = runs["routed"]
        model = routed.get("model", "").lower()
        status = routed.get("status", "error")

        if status != "success":
            error_count += 1

        total_tasks += 1

        # Model distribution
        if "haiku" in model:
            model_dist["haiku"] += 1
        elif "sonnet" in model:
            model_dist["sonnet"] += 1
        else:
            model_dist["opus"] += 1

        # Confidence
        confidence = routed.get("confidence", 0.5)
        confidences.append(confidence)

        # Signal strength
        signal = routed.get("signal_strength", "weak")
        if signal in signals:
            signals[signal] += 1

        # Cost estimation error (if provided)
        estimated_cost = routed.get("cost_estimate", 0)
        actual_cost = routed.get("actual_cost", 0)
        if actual_cost > 0:
            error_pct = ((actual_cost - estimated_cost) / actual_cost) * 100
            cost_errors.append(error_pct)

    # Convert to percentages
    model_dist_pct = {
        model: (count / total_tasks * 100) if total_tasks > 0 else 0
        for model, count in model_dist.items()
    }

    error_rate_pct = (error_count / total_tasks * 100) if total_tasks > 0 else 0
    mean_confidence = statistics.mean(confidences) if confidences else 0.5
    cost_error = statistics.mean(cost_errors) if cost_errors else 0.0

    return ConfoundMetrics(
        routing_accuracy_pct=routing_correct / total_tasks * 100 if total_tasks > 0 else 0,
        model_distribution=model_dist_pct,
        cost_estimation_error_pct=cost_error,
        error_rate_pct=error_rate_pct,
        mean_confidence=mean_confidence,
        routing_signals=signals,
    )


def _bootstrap_ci(data: List[float], alpha: float = 0.05, n_bootstrap: int = 1000) -> Tuple[float, float]:
    """Calculate 95% bootstrap confidence interval."""
    if len(data) == 0:
        return 0.0, 0.0

    bootstrap_means = []
    for _ in range(n_bootstrap):
        sample = np.random.choice(data, size=len(data), replace=True)
        bootstrap_means.append(np.mean(sample))

    ci_lower = np.percentile(bootstrap_means, alpha / 2 * 100)
    ci_upper = np.percentile(bootstrap_means, (1 - alpha / 2) * 100)

    return float(ci_lower), float(ci_upper)


def _binomial_ci(successes: int, trials: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Calculate binomial proportion confidence interval (Wilson score interval)."""
    from scipy.stats import binom

    if trials == 0:
        return 0.0, 0.0

    p_hat = successes / trials
    z = stats.norm.ppf(1 - alpha / 2)

    denominator = 1 + z ** 2 / trials
    center = (p_hat + z ** 2 / (2 * trials)) / denominator
    margin = z * np.sqrt(p_hat * (1 - p_hat) / trials + z ** 2 / (4 * trials ** 2)) / denominator

    return float(max(0, center - margin) * 100), float(min(1, center + margin) * 100)


def _generate_summary(
    token_metrics: TokenMetrics,
    latency_metrics: LatencyMetrics,
    quality_metrics: QualityMetrics,
) -> str:
    """Generate human-readable summary of results."""
    verdict = "REJECT H1" if not (
        token_metrics.accepts_h1 and latency_metrics.accepts_h1 and quality_metrics.accepts_h1
    ) else "ACCEPT H1"

    summary = f"""
    BENCHMARK RESULTS SUMMARY
    ════════════════════════════════════════════════════════════════

    Overall Verdict: {verdict}

    TOKEN SAVINGS
    ─────────────────────────────────────────────────────────────
    Mean Savings: {token_metrics.mean_savings_pct:.1f}% (95% CI: [{token_metrics.ci_lower_95:.1f}%, {token_metrics.ci_upper_95:.1f}%])
    Median Savings: {token_metrics.median_savings_pct:.1f}%
    % of tasks with savings: {token_metrics.pct_tasks_with_savings:.1f}%
    p-value (paired t-test): {token_metrics.p_value:.4f}
    H1 Verdict: {'✅ ACCEPT' if token_metrics.accepts_h1 else '❌ REJECT'} (threshold: p<0.05, mean ≥20%)

    LATENCY IMPROVEMENT
    ─────────────────────────────────────────────────────────────
    Mean Improvement: {latency_metrics.mean_improvement_pct:.1f}% (95% CI: [{latency_metrics.ci_lower_95:.1f}%, {latency_metrics.ci_upper_95:.1f}%])
    p50 Latency: {latency_metrics.p50_latency_opus_ms:.0f}ms → {latency_metrics.p50_latency_routed_ms:.0f}ms
    p99 Latency: {latency_metrics.p99_latency_opus_ms:.0f}ms → {latency_metrics.p99_latency_routed_ms:.0f}ms
    p-value (Mann-Whitney U): {latency_metrics.p_value:.4f}
    H1 Verdict: {'✅ ACCEPT' if latency_metrics.accepts_h1 else '❌ REJECT'} (threshold: p<0.05, mean ≥10%, p99 improves)

    QUALITY/ACCURACY
    ─────────────────────────────────────────────────────────────
    Opus Baseline: {quality_metrics.accuracy_opus_pct:.1f}%
    Routed System: {quality_metrics.accuracy_routed_pct:.1f}%
    Regression: {quality_metrics.regression_pct:.1f}% (95% CI: [{quality_metrics.ci_lower_95:.1f}%, {quality_metrics.ci_upper_95:.1f}%])
    p-value (Chi-square): {quality_metrics.p_value:.4f}
    H1 Verdict: {'✅ ACCEPT' if quality_metrics.accepts_h1 else '❌ REJECT'} (threshold: p<0.05, accuracy ≥98%, regression ≤2%)

    ════════════════════════════════════════════════════════════════
    """
    return summary


def _now_iso8601() -> str:
    """Current timestamp in ISO 8601 format."""
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
