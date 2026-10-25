"""Phase 1 Complete Tests — K=3-5 validation.

Tests baseline, comparison, and aggregation pipeline.
"""

import pytest
from core.learning.token_instrumentation import TokenCounter
from core.learning.token_baseline import BaselineMetrics, ComparisonEngine, ComparisonResult
from core.learning.token_metrics_store import TokenMetricsStore
# token_metrics_aggregator was retired by ADR-2282 (it fabricated a timestamp and a
# confidence); its aggregation tests left with it.


class MockEventEmitter:
    """Recording stand-in for ``EventEmitter`` (the k2 module it was imported from no longer exists)."""

    def __init__(self):
        self.emitted = []

    def emit(self, event):
        self.emitted.append(event)
        return True


class TestBaselineMetrics:
    """Test baseline estimation."""

    def test_baseline_by_complexity(self):
        """Test baseline varies by task complexity."""
        trivial = BaselineMetrics("t1", "trivial").baseline_tokens
        complex = BaselineMetrics("t2", "complex").baseline_tokens

        assert complex > trivial
        assert trivial == 1800  # Base
        assert complex == int(1800 * 2.5)  # 4500

    def test_comparison_result_savings(self):
        """Test savings calculation."""
        counter = TokenCounter(turn_id="t1", engine="claude")
        counter.record_llm_call(1000, 500)  # 1500 total
        counter.finalize()

        comp = ComparisonResult.from_counter(counter, baseline_tokens=2000)

        assert comp.savings_tokens == 500
        assert comp.savings_percent == 25.0
        assert comp.is_significant  # 25% > 15%


class TestComparisonEngine:
    """Test Vibe vs Native comparison."""

    def test_get_baseline(self):
        """Test baseline retrieval."""
        engine = ComparisonEngine()

        baseline1 = engine.get_baseline("t1", "simple")
        baseline2 = engine.get_baseline("t1", "simple")  # Cached

        assert baseline1 == baseline2
        assert baseline1 == int(1800 * 1.3)

    def test_compare_turn(self):
        """Test comparing one turn."""
        engine = ComparisonEngine()
        counter = TokenCounter(turn_id="t1", engine="claude")
        counter.record_llm_call(1000, 500)
        counter.finalize()

        comp = engine.compare(counter, "moderate")

        assert comp.is_significant
        assert comp.savings_percent > 0

    def test_aggregate_multiple_comparisons(self):
        """Test aggregating multiple comparisons."""
        engine = ComparisonEngine()

        for i in range(5):
            counter = TokenCounter(turn_id=f"t{i}", engine="claude")
            counter.record_llm_call(1000 + i * 100, 500 + i * 50)
            counter.finalize()
            engine.compare(counter, "moderate")

        agg = engine.aggregate_comparisons()

        assert agg["comparison_count"] == 5
        assert agg["avg_savings_percent"] > 0
        assert agg["high_confidence_count"] > 0


class TestPhase1Integration:
    """Integration test: full Phase 1 flow."""

    def test_full_measurement_flow(self):
        """Test: Instrumentation → Store → Comparison."""
        # K=1: Instrumentation
        counter = TokenCounter(turn_id="turn_final", engine="claude-opus-5")
        counter.record_llm_call(input_tokens=1400, output_tokens=850)
        counter.record_subsystem_usage("confidence", 200)
        counter.record_subsystem_usage("cache", 150)
        counter.baseline_tokens = 2800
        counter.task_type = "code"
        counter.task_domain = "backend"
        counter.outcome_quality = "excellent"
        counter.finalize()

        # K=2: Store
        emitter = MockEventEmitter()
        store = TokenMetricsStore(emitter)
        event_id = store.write_token_metrics(
            counter,
            tenant_id="prod-tenant",
            instance_id="instance-001",
            session_id="session-abc123",
            user_id="user-xyz",
        )

        assert event_id is not None

        # K=3: Baseline + Comparison
        engine = ComparisonEngine()
        comparison = engine.compare(counter, "moderate")

        assert comparison.is_significant
        assert comparison.savings_percent > 30  # (2800-2250)/2800 = 19.6%
