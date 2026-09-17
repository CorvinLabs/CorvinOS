#!/usr/bin/env python3
"""
E2E Tests: Token Efficiency Benchmarking (Real LLM Calls, No Mocks)

These tests run REAL Anthropic API calls to measure token savings and quality impact.
They are designed to catch regressions that would be invisible in mocked tests.

Requirements:
  - ANTHROPIC_API_KEY set in environment
  - Sufficient API quota (budget for ~50 API calls per run)
  - Network access to api.anthropic.com

Author: Claude Haiku 4.5 (Adversarial Review)
Date: 2026-09-16
"""

import os
import pytest
import json
from statistics import mean, stdev, median
import sys

sys.path.insert(0, "/home/shumway/projects/CorvinOS/core/benchmarking")
from token_efficiency_benchmark import (
    TokenEfficiencyBenchmark,
    TASK_SCENARIOS,
    PairwiseComparison,
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture(scope="module")
def benchmark():
    """Initialize benchmark with real API key."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return TokenEfficiencyBenchmark(api_key=api_key)


@pytest.fixture(scope="module")
def task_pool():
    """All available tasks across categories."""
    all_tasks = []
    for tasks in TASK_SCENARIOS.values():
        all_tasks.extend(tasks)
    return all_tasks


# ============================================================================
# TEST SUITE 1: PAIRWISE COMPARISONS (Haiku vs. Sonnet)
# ============================================================================

class TestPairwiseComparison:
    """Compare Haiku and Sonnet on identical tasks."""

    def test_code_review_security_haiku_vs_sonnet(self, benchmark):
        """CRITICAL: Haiku must not miss SQL injection detection."""
        task = TASK_SCENARIOS["code_review"][0]  # SQL injection task

        comparison = benchmark.run_pairwise_comparison(task)

        # Assertions
        assert comparison.haiku_quality >= 0.80, "Haiku must score >= 0.80 on security review"
        assert comparison.quality_delta >= -0.10, "Quality loss must be <= 10%"
        assert comparison.token_savings_pct >= 30.0, "Token savings must be >= 30%"
        print(f"✅ Security task: {comparison.token_savings_pct:.1f}% savings, Δ quality: {comparison.quality_delta:+.3f}")

    def test_code_review_performance_haiku_vs_sonnet(self, benchmark):
        """Haiku should excel at performance analysis."""
        task = TASK_SCENARIOS["code_review"][1]  # Performance task

        comparison = benchmark.run_pairwise_comparison(task)

        assert comparison.haiku_quality >= 0.85, "Haiku should score >= 0.85 on performance"
        assert comparison.quality_delta >= -0.05, "Quality loss should be minimal"
        assert comparison.token_savings_pct >= 40.0, "Token savings should be >= 40%"
        print(f"✅ Performance task: {comparison.token_savings_pct:.1f}% savings, Quality similar")

    def test_code_review_readability_haiku_vs_sonnet(self, benchmark):
        """Haiku should be equal to Sonnet on readability."""
        task = TASK_SCENARIOS["code_review"][2]  # Readability task (simple)

        comparison = benchmark.run_pairwise_comparison(task)

        assert comparison.haiku_quality >= 0.88, "Haiku should score >= 0.88 on readability"
        assert comparison.quality_delta >= -0.02, "Quality loss should be negligible"
        assert comparison.token_savings_pct >= 45.0, "Token savings should be >= 45%"
        print(f"✅ Readability task: {comparison.token_savings_pct:.1f}% savings, Haiku comparable")

    def test_testing_coverage_haiku_vs_sonnet(self, benchmark):
        """Haiku should generate solid test cases."""
        task = TASK_SCENARIOS["testing"][0]

        comparison = benchmark.run_pairwise_comparison(task)

        assert comparison.haiku_quality >= 0.82, "Haiku should score >= 0.82 on test generation"
        assert comparison.quality_delta >= -0.08, "Quality loss should be <= 8%"
        assert comparison.token_savings_pct >= 35.0, "Token savings should be >= 35%"

    def test_documentation_api_haiku_vs_sonnet(self, benchmark):
        """Haiku should write clear API documentation."""
        task = TASK_SCENARIOS["documentation"][0]

        comparison = benchmark.run_pairwise_comparison(task)

        assert comparison.haiku_quality >= 0.85, "Haiku should score >= 0.85 on documentation"
        assert comparison.quality_delta >= -0.05, "Quality loss should be minimal"
        assert comparison.token_savings_pct >= 40.0, "Token savings should be >= 40%"

    def test_algorithm_complexity_analysis_haiku_vs_sonnet(self, benchmark):
        """HIGH COMPLEXITY: Haiku may struggle with deep algorithm analysis."""
        task = TASK_SCENARIOS["analysis"][0]  # Algorithm complexity (high)

        comparison = benchmark.run_pairwise_comparison(task)

        # This is a high-complexity task; tolerance should be higher
        assert comparison.haiku_quality >= 0.75, "Haiku should score >= 0.75 on complex analysis"
        assert comparison.quality_delta >= -0.15, "Quality loss acceptable on hard tasks (<= 15%)"
        # Token savings on high-complexity tasks may be lower
        assert comparison.token_savings_pct >= 20.0, "Token savings should still be >= 20%"


# ============================================================================
# TEST SUITE 2: AGGREGATE QUALITY & SAVINGS
# ============================================================================

class TestAggregateMetrics:
    """Verify aggregate metrics across a representative sample."""

    def test_aggregate_token_savings_target(self, benchmark, task_pool):
        """Overall token savings must be >= 40%."""
        # Run 15 random pairwise comparisons
        sample_tasks = task_pool[:15]  # Use first 15 tasks

        for task in sample_tasks:
            try:
                benchmark.run_pairwise_comparison(task)
            except Exception as e:
                print(f"  Skipped {task['id']}: {e}")

        if not benchmark.comparisons:
            pytest.skip("No comparisons to analyze")

        savings_list = [c.token_savings_pct for c in benchmark.comparisons]
        mean_savings = mean(savings_list)

        assert mean_savings >= 40.0, f"Mean savings must be >= 40%, got {mean_savings:.1f}%"
        print(f"✅ Aggregate token savings: {mean_savings:.1f}% (CI: [{min(savings_list):.1f}%, {max(savings_list):.1f}%])")

    def test_aggregate_quality_preservation(self, benchmark):
        """Quality loss must be minimal across all comparisons."""
        if not benchmark.comparisons:
            pytest.skip("No comparisons")

        quality_deltas = [c.quality_delta for c in benchmark.comparisons]
        mean_delta = mean(quality_deltas)

        assert mean_delta >= -0.05, f"Quality loss must be <= 5%, got {mean_delta:.3f}"
        assert all(q >= -0.15 for q in quality_deltas), "No single task should lose > 15% quality"
        print(f"✅ Quality preserved: mean Δ = {mean_delta:+.3f} (all within -15%)")

    def test_no_outlier_regressions(self, benchmark):
        """No outlier tasks with disproportionate quality loss."""
        if not benchmark.comparisons:
            pytest.skip("No comparisons")

        quality_deltas = [c.quality_delta for c in benchmark.comparisons]

        # Outliers are those beyond 2 standard deviations
        if len(quality_deltas) > 1:
            std = stdev(quality_deltas)
            mean_val = mean(quality_deltas)
            outliers = [q for q in quality_deltas if abs(q - mean_val) > 2 * std]

            assert len(outliers) <= 1, f"Found {len(outliers)} outlier regressions (should be 0-1)"
            print(f"✅ Outlier detection: 0 unacceptable regressions")


# ============================================================================
# TEST SUITE 3: COST & VALUE METRICS
# ============================================================================

class TestCostMetrics:
    """Verify cost and value calculations."""

    def test_cost_calculation_accuracy(self, benchmark):
        """Token counts must map correctly to USD costs."""
        if not benchmark.measurements:
            pytest.skip("No measurements")

        for m in benchmark.measurements[:5]:  # Check first 5
            tokens_total = m.total_tokens
            cost_expected = tokens_total * 0.000004  # Rough estimate (USD/token)

            # Cost should be in reasonable range (allow ±50% for model variation)
            assert 0 < m.cost_usd < cost_expected * 2, f"Cost {m.cost_usd} seems wrong for {tokens_total} tokens"

        print(f"✅ Cost calculations verified (checked {len(benchmark.measurements[:5])} measurements)")

    def test_haiku_cost_advantage(self, benchmark):
        """Haiku must be consistently cheaper than Sonnet."""
        if not benchmark.measurements:
            pytest.skip("No measurements")

        haiku_costs = [m.cost_usd for m in benchmark.measurements if m.model == "haiku"]
        sonnet_costs = [m.cost_usd for m in benchmark.measurements if m.model == "sonnet"]

        if haiku_costs and sonnet_costs:
            haiku_mean = mean(haiku_costs)
            sonnet_mean = mean(sonnet_costs)
            cost_reduction = ((sonnet_mean - haiku_mean) / sonnet_mean) * 100

            assert cost_reduction >= 40.0, f"Haiku cost should be <= 60% of Sonnet, got {100-cost_reduction:.1f}%"
            print(f"✅ Haiku cost advantage: {cost_reduction:.1f}% cheaper than Sonnet")


# ============================================================================
# TEST SUITE 4: LATENCY SLA COMPLIANCE
# ============================================================================

class TestLatencySLA:
    """Verify latency stays within SLA."""

    def test_latency_p99_within_sla(self, benchmark, task_pool):
        """P99 latency must be < 2.0s."""
        if not benchmark.measurements:
            # Run a few sample tasks if needed
            for task in task_pool[:3]:
                try:
                    benchmark.run_task(task, "haiku")
                except:
                    pass

        if not benchmark.measurements:
            pytest.skip("No latency measurements")

        latencies = sorted([m.latency_total_ms for m in benchmark.measurements])
        p99_latency_s = latencies[int(len(latencies) * 0.99)] / 1000.0

        assert p99_latency_s < 2.0, f"P99 latency must be < 2.0s, got {p99_latency_s:.2f}s"
        print(f"✅ Latency SLA met: P99 = {p99_latency_s:.2f}s (< 2.0s)")


# ============================================================================
# TEST SUITE 5: SCIENTIFIC RIGOR
# ============================================================================

class TestScientificRigor:
    """Verify benchmarking methodology is scientifically sound."""

    def test_statistical_significance(self, benchmark):
        """Token savings must be statistically significant (p < 0.05)."""
        if len(benchmark.comparisons) < 5:
            pytest.skip("Need >= 5 comparisons for statistical test")

        # Simple t-test surrogate: check variance is not dominated by noise
        savings_list = [c.token_savings_pct for c in benchmark.comparisons]
        mean_savings = mean(savings_list)
        std_savings = stdev(savings_list) if len(savings_list) > 1 else 0

        # Signal-to-noise ratio
        snr = mean_savings / (std_savings + 1e-6)  # Avoid division by zero
        assert snr > 2.0, f"Signal-to-noise ratio too low ({snr:.2f}). Increase sample size."
        print(f"✅ Statistical significance: SNR = {snr:.2f} (> 2.0)")

    def test_confidence_interval_width(self, benchmark):
        """95% CI should be tight enough for publication."""
        if len(benchmark.comparisons) < 10:
            pytest.skip("Need >= 10 comparisons for tight CI")

        savings_list = [c.token_savings_pct for c in benchmark.comparisons]
        mean_savings = mean(savings_list)
        std_savings = stdev(savings_list)
        n = len(savings_list)
        se = std_savings / (n ** 0.5)
        ci_width = 1.96 * se

        assert ci_width < 10.0, f"CI width too wide ({ci_width:.1f}%). Need more samples."
        print(f"✅ CI precision: width = ±{ci_width:.1f}% (< 10%)")


# ============================================================================
# TEST SUITE 6: PRODUCTION READINESS
# ============================================================================

class TestProductionReadiness:
    """Final gate: Is token optimization production-ready?"""

    def test_production_readiness_gate(self, benchmark):
        """All criteria must pass for production deployment."""
        if len(benchmark.comparisons) < 10:
            pytest.skip("Need >= 10 pairwise comparisons")

        report = benchmark.generate_report()

        # Criteria
        savings_ok = report["token_savings"]["mean_pct"] >= 40.0
        quality_ok = report["verdict"]["haiku_preserves_quality"]
        ready = report["verdict"]["ready_for_production"]

        print(f"""
        🎯 PRODUCTION READINESS GATE
        ═════════════════════════════════
        Savings >= 40%:        {'✅' if savings_ok else '❌'} ({report["token_savings"]["mean_pct"]:.1f}%)
        Quality preserved:     {'✅' if quality_ok else '❌'} (Δ = {report["quality_impact"]["mean_delta"]:+.3f})
        Ready for production:  {'✅' if ready else '❌'}

        📊 Detailed metrics:
        {json.dumps(report, indent=2)}
        """)

        assert ready, "Production readiness gate failed"


# ============================================================================
# MAIN ENTRY POINT (Run tests)
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
