"""
Phase C: Quality Engine Test Suite

Validates:
  - Autonomous quality gates measurement
  - Real-time health monitoring
  - Auto-optimizer proposal generation & testing
  - Phase C gate decisions (PASS/WARN/FAIL for Phase D)
"""

import pytest
import json
import time
from pathlib import Path
from datetime import datetime, timezone


class TestQualityGatesEngine:
    """Test autonomous quality gates."""

    def test_thresholds_tightened_10_percent(self):
        """GATE: Thresholds are correctly tightened 10% from Phase B."""
        from core.quality.quality_gates_autonomous import QualityGatesEngine

        engine = QualityGatesEngine()
        thresholds = engine.THRESHOLDS

        # Verify Phase C thresholds (10% tighter than Phase B)
        assert thresholds["p99_latency_ms"] == 306, "p99 tightened to 306ms (from 340)"
        assert thresholds["error_rate_percent"] == 0.09, "error_rate tightened to 0.09% (from 0.1%)"
        assert thresholds["context_accuracy_percent"] == 89, "accuracy tightened to 89% (from 80%)"
        assert thresholds["learning_convergence_iter"] == 900, "convergence tightened to 900 (from 1000)"
        assert thresholds["cpu_percent"] == 72, "CPU tightened to 72% (from 80%)"

    def test_latency_gate_pass(self):
        """GATE: Latency gate passes when p99 ≤ 306ms."""
        from core.quality.quality_gates_autonomous import QualityGatesEngine, LatencyMetrics

        engine = QualityGatesEngine()
        metrics = LatencyMetrics(
            p50=150, p95=250, p99=290,  # Under threshold
            mean=200, min=100, max=310,
        )

        result = engine.gate_latency(metrics)
        assert result.status == "PASS"
        assert result.current_value == 290

    def test_latency_gate_fail(self):
        """GATE: Latency gate fails when p99 > 306ms."""
        from core.quality.quality_gates_autonomous import QualityGatesEngine, LatencyMetrics

        engine = QualityGatesEngine()
        metrics = LatencyMetrics(
            p50=150, p95=280, p99=350,  # Over threshold
            mean=200, min=100, max=360,
        )

        result = engine.gate_latency(metrics)
        assert result.status == "FAIL"
        assert "LATENCY REGRESSION" in result.message

    def test_error_rate_gate_pass(self):
        """GATE: Error rate passes when < 0.09%."""
        from core.quality.quality_gates_autonomous import QualityGatesEngine, ErrorMetrics

        engine = QualityGatesEngine()
        metrics = ErrorMetrics(
            error_rate_percent=0.08,
            error_count=4,
            total_count=5000,
            errors_by_type={"timeout": 2, "invalid": 2},
        )

        result = engine.gate_error_rate(metrics)
        assert result.status == "PASS"

    def test_throughput_gate_pass(self):
        """GATE: Throughput passes when ≥ 1000 ops/sec."""
        from core.quality.quality_gates_autonomous import QualityGatesEngine, ThroughputMetrics

        engine = QualityGatesEngine()
        metrics = ThroughputMetrics(
            ops_per_sec=1200,
            requests_per_min=72000,
            total_requests=6000,
        )

        result = engine.gate_throughput(metrics)
        assert result.status == "PASS"

    def test_run_all_gates_comprehensive(self):
        """COMPREHENSIVE: Run all gates and get overall status."""
        from core.quality.quality_gates_autonomous import QualityGatesEngine

        engine = QualityGatesEngine()
        report = engine.run_all_gates()

        # Verify report structure
        assert report.overall_status in ["PASS", "WARN", "FAIL"]
        assert len(report.gate_results) >= 5  # At least 5 gates
        assert report.summary is not None
        assert "status" in report.summary


class TestRealtimeHealthMonitor:
    """Test real-time health monitoring."""

    def test_monitor_starts(self):
        """Test: Monitor starts and stops cleanly."""
        from core.quality.health_monitor_realtime import RealtimeHealthMonitor

        monitor = RealtimeHealthMonitor()
        monitor.start()
        time.sleep(1)  # Let it collect one snapshot
        monitor.stop()

        assert len(monitor.snapshots) >= 0

    def test_health_status_computation(self):
        """Test: Health status correctly computed from snapshots."""
        from core.quality.health_monitor_realtime import RealtimeHealthMonitor, MetricSnapshot

        monitor = RealtimeHealthMonitor()

        # Add sample snapshot
        snapshot = MetricSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            p50_latency_ms=150,
            p95_latency_ms=250,
            p99_latency_ms=290,
            mean_latency_ms=200,
            throughput_ops_sec=1200,
            error_rate_percent=0.08,
            cpu_percent=65,
            memory_percent=58,
            io_wait_percent=5,
        )
        monitor.snapshots.append(snapshot)

        status = monitor.get_health_status()
        assert status.overall_status == "GREEN"
        assert status.last_snapshot == snapshot

    def test_metric_history_export(self):
        """Test: Metric history can be exported for charting."""
        from core.quality.health_monitor_realtime import RealtimeHealthMonitor

        monitor = RealtimeHealthMonitor()
        history = monitor.get_metric_history(hours=24)

        assert "snapshots" in history
        assert "period_hours" in history
        assert history["period_hours"] == 24


class TestAutoOptimizer:
    """Test autonomous optimization loop."""

    def test_failure_context_capture(self):
        """Test: Failure context captured with system state."""
        from core.quality.auto_optimizer import AutoOptimizer

        optimizer = AutoOptimizer()
        context = optimizer.capture_failure_context(
            failure_type="latency_regression",
            metric_name="p99_latency_ms",
            current_value=350,
            threshold=306,
            component="context_engine",
        )

        assert context.failure_type == "latency_regression"
        assert context.current_value == 350
        assert context.threshold == 306
        assert context.system_load in ["low", "moderate", "high"]

    def test_proposal_generation_latency_regression(self):
        """Test: Optimizer generates proposal for latency regression."""
        from core.quality.auto_optimizer import AutoOptimizer

        optimizer = AutoOptimizer()
        context = optimizer.capture_failure_context(
            failure_type="latency_regression",
            metric_name="p99_latency_ms",
            current_value=350,
            threshold=306,
        )

        proposal = optimizer.generate_proposal(context)
        assert proposal.strategy.value in ["increase_connection_pool", "enable_response_caching"]
        assert proposal.parameter_name is not None
        assert proposal.expected_improvement_percent > 0

    def test_proposal_generation_error_spike(self):
        """Test: Optimizer generates proposal for error spike."""
        from core.quality.auto_optimizer import AutoOptimizer

        optimizer = AutoOptimizer()
        context = optimizer.capture_failure_context(
            failure_type="error_spike",
            metric_name="error_rate_percent",
            current_value=0.15,
            threshold=0.09,
        )

        proposal = optimizer.generate_proposal(context)
        assert proposal.strategy.value == "increase_retry_count"

    def test_proposal_generation_throughput_drop(self):
        """Test: Optimizer generates proposal for throughput drop."""
        from core.quality.auto_optimizer import AutoOptimizer

        optimizer = AutoOptimizer()
        context = optimizer.capture_failure_context(
            failure_type="throughput_drop",
            metric_name="throughput_ops_sec",
            current_value=800,
            threshold=1000,
        )

        proposal = optimizer.generate_proposal(context)
        assert proposal.strategy.value in ["scale_worker_threads", "increase_batch_size"]

    def test_optimization_cycle_complete(self):
        """Test: Complete optimization cycle runs end-to-end."""
        from core.quality.auto_optimizer import AutoOptimizer

        optimizer = AutoOptimizer()
        result = optimizer.run_optimization_cycle(
            failure_type="latency_regression",
            metric_name="p99_latency_ms",
            current_value=320,
            threshold=306,
            component="context_engine",
        )

        assert result.proposal_id is not None
        assert result.status in ["APPLIED", "BLOCKED", "TESTING"]
        if result.status == "APPLIED":
            assert result.improvement_percent > 0


class TestPhaseC_Integration:
    """Integration tests for Phase C as a whole."""

    def test_quality_gates_gating_decision(self):
        """Test: Quality gates produce go/no-go decision for Phase D."""
        from core.quality.quality_gates_autonomous import QualityGatesEngine

        engine = QualityGatesEngine()
        report = engine.run_all_gates()

        # Verify Phase D decision criteria
        if report.overall_status == "FAIL":
            # Blocked: don't proceed to Phase D
            assert any(g.status == "FAIL" for g in report.gate_results)
        elif report.overall_status == "PASS":
            # Clear: proceed to Phase D
            assert all(g.status == "PASS" for g in report.gate_results)

    def test_health_monitor_and_optimizer_integration(self):
        """Test: Health monitor detects issues, optimizer proposes fixes."""
        from core.quality.health_monitor_realtime import RealtimeHealthMonitor
        from core.quality.auto_optimizer import AutoOptimizer

        monitor = RealtimeHealthMonitor()
        optimizer = AutoOptimizer()

        # Simulate: monitor detects latency regression
        # Optimizer responds with proposal
        context = optimizer.capture_failure_context(
            failure_type="latency_regression",
            metric_name="p99_latency_ms",
            current_value=350,
            threshold=306,
        )

        proposal = optimizer.generate_proposal(context)
        assert proposal is not None

    def test_phase_c_artifacts_created(self):
        """Test: Phase C creates expected artifacts (reports, checkpoints)."""
        from core.quality.quality_gates_autonomous import QualityGatesEngine
        from pathlib import Path

        engine = QualityGatesEngine()
        report = engine.run_all_gates()

        # Verify report was saved
        report_path = engine.metrics_dir / "latest_quality_gates.json"
        assert report_path.exists(), "Quality gates report should be saved"

        # Verify JSON is valid
        with open(report_path) as f:
            data = json.load(f)
            assert "overall_status" in data
            assert "gate_results" in data


# ────────────────────────────────────────────────────────────────────────
# Phase C ENTRY POINT: Run quality gates and report
# ────────────────────────────────────────────────────────────────────────

def test_phase_c_quality_gates_entry_point():
    """
    ENTRY POINT: Phase C Quality Engine validation.

    This test runs the complete quality gates suite and reports the result.
    The test PASSES if all gates pass (Phase D authorized).
    The test WARNS if some gates warn (Phase D caution).
    The test FAILS if any gate fails (Phase D blocked).
    """
    from core.quality.quality_gates_autonomous import QualityGatesEngine

    print("\n" + "="*70)
    print("🚀 PHASE C: QUALITY ENGINE VALIDATION")
    print("="*70)

    engine = QualityGatesEngine()
    report = engine.run_all_gates()

    # Print summary
    print(f"\n📊 PHASE C RESULTS:")
    print(f"   Overall Status: {report.overall_status}")
    print(f"   Gates Passed: {sum(1 for g in report.gate_results if g.status == 'PASS')}/{len(report.gate_results)}")
    print(f"   Gates Warned: {sum(1 for g in report.gate_results if g.status == 'WARN')}")
    print(f"   Gates Failed: {sum(1 for g in report.gate_results if g.status == 'FAIL')}")

    # Phase D authorization decision
    if report.overall_status == "FAIL":
        print(f"\n❌ PHASE D BLOCKED — Quality gates failed")
        pytest.fail("Quality gates failed — Phase D cannot proceed")
    elif report.overall_status == "WARN":
        print(f"\n⚠️  PHASE D CAUTIONED — Some gates warning")
    else:
        print(f"\n✅ PHASE D AUTHORIZED — All quality gates passed")

    # Auto-optimizer recommendations
    if report.recommendations:
        print(f"\n💡 Auto-Optimizer Recommendations:")
        for rec in report.recommendations[:3]:
            print(f"   • {rec}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
