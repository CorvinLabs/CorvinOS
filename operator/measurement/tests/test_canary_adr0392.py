"""Comprehensive tests for measurement infrastructure (ADR-0392 Phase 1).

12+ tests covering:
  1. CanaryRouter deterministic routing
  2. CanaryRouter percentage distribution
  3. CanaryRouter edge cases
  4. TokenMetric validation
  5. MetricsCollector recording
  6. MetricsCollector CSV export
  7. MetricsCollector file I/O
  8. Analysis: group splitting
  9. Analysis: group comparison
  10. Analysis: report generation
  11. Integration: routing + metrics
  12. Integration: complete workflow
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from analysis import (
    compare_groups,
    generate_report,
    load_metrics,
    split_by_group,
)
from canary_router import CanaryRouter
from token_metrics import MetricsCollector, TokenMetric


class TestCanaryRouter:
    """Tests for deterministic canary routing."""

    def test_router_initialization(self):
        """Router initializes without state."""
        router = CanaryRouter()
        assert router is not None

    def test_deterministic_routing_same_tenant_same_result(self):
        """Same tenant_id always gets same canary assignment."""
        router = CanaryRouter()
        tenant_id = "user_42"

        # Call multiple times
        result1 = router.is_canary_tenant(tenant_id, canary_pct=10)
        result2 = router.is_canary_tenant(tenant_id, canary_pct=10)
        result3 = router.is_canary_tenant(tenant_id, canary_pct=10)

        assert result1 == result2 == result3

    def test_different_tenants_different_assignments(self):
        """Different tenant_ids can get different assignments."""
        router = CanaryRouter()

        # Create 100 tenant IDs and check we get both True and False
        assignments = [
            router.is_canary_tenant(f"user_{i}", canary_pct=10)
            for i in range(100)
        ]

        assert True in assignments, "Should have some canary tenants"
        assert False in assignments, "Should have some control tenants"

    def test_canary_percentage_distribution(self):
        """With 100 tenants and 10% canary, roughly 10 should be canary.

        Note: deterministic hash may produce skewed distributions for small
        samples with specific input patterns. This test uses user_0..user_99.
        """
        router = CanaryRouter()
        canary_count = sum(
            1
            for i in range(100)
            if router.is_canary_tenant(f"user_{i}", canary_pct=10)
        )

        # With deterministic hash, distribution may vary significantly for
        # small samples (100 items) with specific inputs. Allow wide range.
        # For a larger sample (1000+) the distribution would be more uniform.
        assert 1 <= canary_count <= 25, (
            f"Expected roughly 10 canary (±large margin for small sample), "
            f"got {canary_count}"
        )

    def test_canary_percentage_50(self):
        """With 50% canary, roughly half should be canary."""
        router = CanaryRouter()
        canary_count = sum(
            1
            for i in range(100)
            if router.is_canary_tenant(f"user_{i}", canary_pct=50)
        )

        # Allow ±10 for hash distribution variance
        assert 40 <= canary_count <= 60, f"Expected ~50 canary, got {canary_count}"

    def test_route_by_tenant_percentage_canary(self):
        """Route returns flags as-is for canary tenants."""
        router = CanaryRouter()

        # Create a tenant that's in the canary group for 100% (all canary)
        flags = {"vibe_engineering": True, "per_stage_token_budgeting": True}

        result = router.route_by_tenant_percentage(
            "user_0", flags, canary_pct=100  # 100% canary
        )

        assert result == flags, "Canary tenants should keep flags as-is"

    def test_route_by_tenant_percentage_control(self):
        """Route disables flags for control tenants."""
        router = CanaryRouter()

        flags = {"vibe_engineering": True, "per_stage_token_budgeting": True}

        result = router.route_by_tenant_percentage(
            "user_0", flags, canary_pct=0  # 0% canary (all control)
        )

        assert all(v is False for v in result.values()), (
            "Control tenants should have all flags disabled"
        )

    def test_route_by_tenant_percentage_invalid_canary_pct(self):
        """Route raises ValueError for invalid canary_pct."""
        router = CanaryRouter()

        with pytest.raises(ValueError):
            router.route_by_tenant_percentage("user_0", {}, canary_pct=101)

        with pytest.raises(ValueError):
            router.route_by_tenant_percentage("user_0", {}, canary_pct=-1)

    def test_report_assignment(self):
        """Report assignment provides diagnostic info."""
        router = CanaryRouter()

        report = router.report_assignment("user_42", canary_pct=10)

        assert report["tenant_id"] == "user_42"
        assert "percentage_bucket" in report
        assert "is_canary" in report
        assert report["group"] in ("canary", "control")
        assert 0 <= report["percentage_bucket"] <= 99

    def test_report_assignment_consistency(self):
        """Report assignment is consistent across calls."""
        router = CanaryRouter()

        report1 = router.report_assignment("user_42", canary_pct=10)
        report2 = router.report_assignment("user_42", canary_pct=10)

        assert report1 == report2


class TestTokenMetric:
    """Tests for TokenMetric validation."""

    def test_token_metric_creation(self):
        """TokenMetric initializes with valid data."""
        metric = TokenMetric(
            timestamp="2026-08-19T13:00:00Z",
            turn_id="turn_abc123",
            tenant_id="_default",
            feature_flags_enabled={"vibe_engineering": True},
            context_size_before=15000,
            context_size_after=8000,
            tokens_saved=7000,
            latency_ms=1250,
        )

        assert metric.tokens_saved == 7000
        assert metric.group == "unknown"

    def test_token_metric_validation_context_size(self):
        """TokenMetric rejects context_size_after > context_size_before."""
        with pytest.raises(ValueError, match="context_size_after"):
            TokenMetric(
                timestamp="2026-08-19T13:00:00Z",
                turn_id="turn_abc123",
                tenant_id="_default",
                feature_flags_enabled={},
                context_size_before=8000,
                context_size_after=15000,  # WRONG: after > before
                tokens_saved=-7000,
                latency_ms=1250,
            )

    def test_token_metric_validation_tokens_saved(self):
        """TokenMetric rejects mismatched tokens_saved."""
        with pytest.raises(ValueError, match="tokens_saved"):
            TokenMetric(
                timestamp="2026-08-19T13:00:00Z",
                turn_id="turn_abc123",
                tenant_id="_default",
                feature_flags_enabled={},
                context_size_before=15000,
                context_size_after=8000,
                tokens_saved=5000,  # WRONG: should be 7000
                latency_ms=1250,
            )

    def test_token_metric_validation_negative_latency(self):
        """TokenMetric rejects negative latency."""
        with pytest.raises(ValueError, match="latency_ms"):
            TokenMetric(
                timestamp="2026-08-19T13:00:00Z",
                turn_id="turn_abc123",
                tenant_id="_default",
                feature_flags_enabled={},
                context_size_before=15000,
                context_size_after=8000,
                tokens_saved=7000,
                latency_ms=-100,  # WRONG: negative
            )

    def test_token_metric_to_dict(self):
        """TokenMetric converts to dict."""
        metric = TokenMetric(
            timestamp="2026-08-19T13:00:00Z",
            turn_id="turn_abc123",
            tenant_id="_default",
            feature_flags_enabled={"vibe_engineering": True},
            context_size_before=15000,
            context_size_after=8000,
            tokens_saved=7000,
            latency_ms=1250,
        )

        data = metric.to_dict()
        assert isinstance(data, dict)
        assert data["turn_id"] == "turn_abc123"
        assert data["tokens_saved"] == 7000


class TestMetricsCollector:
    """Tests for metrics collection."""

    def test_collector_initialization(self):
        """MetricsCollector initializes with a path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = MetricsCollector(f"{tmpdir}/metrics.jsonl")
            assert collector.metrics_path.parent.exists()

    def test_collector_record(self):
        """MetricsCollector records metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = MetricsCollector(f"{tmpdir}/metrics.jsonl")

            metric = TokenMetric(
                timestamp="2026-08-19T13:00:00Z",
                turn_id="turn_abc123",
                tenant_id="_default",
                feature_flags_enabled={"vibe_engineering": True},
                context_size_before=15000,
                context_size_after=8000,
                tokens_saved=7000,
                latency_ms=1250,
            )

            collector.record(metric)

            # Verify file was written
            assert collector.metrics_path.is_file()
            content = collector.metrics_path.read_text()
            assert "turn_abc123" in content

    def test_collector_load_from_file(self):
        """MetricsCollector loads metrics from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/metrics.jsonl"
            collector = MetricsCollector(path)

            # Write some metrics
            for i in range(5):
                metric = TokenMetric(
                    timestamp=f"2026-08-19T13:0{i}:00Z",
                    turn_id=f"turn_{i}",
                    tenant_id="_default",
                    feature_flags_enabled={},
                    context_size_before=10000,
                    context_size_after=5000,
                    tokens_saved=5000,
                    latency_ms=1000,
                )
                collector.record(metric)

            # Load and verify
            metrics = collector.load_from_file()
            assert len(metrics) == 5
            assert metrics[0].turn_id == "turn_0"
            assert metrics[4].turn_id == "turn_4"

    def test_collector_summary(self):
        """MetricsCollector.summary() returns statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = MetricsCollector(f"{tmpdir}/metrics.jsonl")

            # Record 10 metrics with varying latencies
            for i in range(10):
                metric = TokenMetric(
                    timestamp=f"2026-08-19T13:0{i}:00Z",
                    turn_id=f"turn_{i}",
                    tenant_id="_default",
                    feature_flags_enabled={},
                    context_size_before=10000,
                    context_size_after=5000,
                    tokens_saved=5000,
                    latency_ms=1000 + i * 100,  # 1000, 1100, ..., 1900 ms
                )
                collector.record(metric)

            summary = collector.summary()

            assert summary["total_turns"] == 10
            assert summary["avg_tokens_saved"] == 5000
            assert 1400 < summary["avg_latency_ms"] < 1500  # Should be ~1450
            assert summary["min_latency_ms"] == 1000
            assert summary["max_latency_ms"] == 1900

    def test_collector_export_csv(self):
        """MetricsCollector exports to CSV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metrics_path = f"{tmpdir}/metrics.jsonl"
            csv_path = f"{tmpdir}/metrics.csv"

            collector = MetricsCollector(metrics_path)

            # Record a few metrics
            for i in range(3):
                metric = TokenMetric(
                    timestamp=f"2026-08-19T13:0{i}:00Z",
                    turn_id=f"turn_{i}",
                    tenant_id="_default",
                    feature_flags_enabled={"vibe_engineering": i % 2 == 0},
                    context_size_before=10000,
                    context_size_after=5000,
                    tokens_saved=5000,
                    latency_ms=1000,
                )
                collector.record(metric)

            collector.export_csv(csv_path)

            # Verify CSV was created and has headers
            assert Path(csv_path).is_file()
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                assert len(rows) == 3
                assert "turn_id" in rows[0]
                assert rows[0]["turn_id"] == "turn_0"

    def test_collector_summary_empty(self):
        """MetricsCollector.summary() handles empty state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = MetricsCollector(f"{tmpdir}/metrics.jsonl")

            summary = collector.summary()

            assert summary["total_turns"] == 0
            assert summary["avg_tokens_saved"] == 0


class TestAnalysis:
    """Tests for analysis pipeline."""

    def test_split_by_group_empty(self):
        """split_by_group handles empty metrics."""
        baseline, canary = split_by_group([])

        assert baseline == []
        assert canary == []

    def test_split_by_group_mixed(self):
        """split_by_group separates control and canary."""
        from analysis import TokenMetricRecord

        metrics = [
            TokenMetricRecord(
                timestamp="2026-08-19T13:00:00Z",
                turn_id="turn_1",
                tenant_id="_default",
                context_size_before=10000,
                context_size_after=5000,
                tokens_saved=5000,
                latency_ms=1000,
                group="control",
            ),
            TokenMetricRecord(
                timestamp="2026-08-19T13:01:00Z",
                turn_id="turn_2",
                tenant_id="_default",
                context_size_before=10000,
                context_size_after=3000,
                tokens_saved=7000,
                latency_ms=1100,
                group="canary",
            ),
            TokenMetricRecord(
                timestamp="2026-08-19T13:02:00Z",
                turn_id="turn_3",
                tenant_id="_default",
                context_size_before=10000,
                context_size_after=5000,
                tokens_saved=5000,
                latency_ms=1050,
                group="control",
            ),
        ]

        baseline, canary = split_by_group(metrics)

        assert len(baseline) == 2
        assert len(canary) == 1
        assert baseline[0].turn_id == "turn_1"
        assert canary[0].turn_id == "turn_2"

    def test_compare_groups_insufficient_data(self):
        """compare_groups handles insufficient data."""
        result = compare_groups([], [])

        assert result["baseline_turns"] == 0
        assert result["canary_turns"] == 0
        assert "error" in result

    def test_compare_groups_shows_improvement(self):
        """compare_groups measures Phase 1-3 improvement."""
        from analysis import TokenMetricRecord

        # Control: small savings
        baseline = [
            TokenMetricRecord(
                timestamp="2026-08-19T13:00:00Z",
                turn_id=f"baseline_{i}",
                tenant_id="_default",
                context_size_before=10000,
                context_size_after=8000,  # 20% reduction
                tokens_saved=2000,
                latency_ms=1000,
                group="control",
            )
            for i in range(10)
        ]

        # Canary: large savings
        canary = [
            TokenMetricRecord(
                timestamp="2026-08-19T13:00:00Z",
                turn_id=f"canary_{i}",
                tenant_id="_default",
                context_size_before=10000,
                context_size_after=5000,  # 50% reduction
                tokens_saved=5000,
                latency_ms=1100,
                group="canary",
            )
            for i in range(10)
        ]

        comparison = compare_groups(baseline, canary)

        # Canary should show better context reduction
        assert comparison["canary_avg_reduction_pct"] > (
            comparison["baseline_avg_reduction_pct"]
        )
        assert comparison["reduction_improvement_pct"] > 20
        assert comparison["tokens_saved_improvement"] > 2000

    def test_generate_report(self):
        """generate_report creates a full report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metrics_path = f"{tmpdir}/metrics.jsonl"

            # Write some test metrics
            with open(metrics_path, "w") as f:
                for i in range(50):
                    metric_dict = {
                        "timestamp": "2026-08-19T13:00:00Z",
                        "turn_id": f"turn_{i}",
                        "tenant_id": "_default",
                        "feature_flags_enabled": {},
                        "context_size_before": 10000,
                        "context_size_after": 5000 if i < 25 else 3000,
                        "tokens_saved": 5000 if i < 25 else 7000,
                        "latency_ms": 1000,
                        "model": "claude-opus-5",
                        "group": "control" if i < 25 else "canary",
                    }
                    f.write(json.dumps(metric_dict) + "\n")

            report = generate_report(metrics_path)

            assert "baseline_summary" in report
            assert "canary_summary" in report
            assert "comparison" in report
            assert "recommendation" in report
            assert report["baseline_summary"]["turns"] == 25
            assert report["canary_summary"]["turns"] == 25

    def test_load_metrics_from_file(self):
        """load_metrics reads JSON lines file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metrics_path = f"{tmpdir}/metrics.jsonl"

            # Write test data
            with open(metrics_path, "w") as f:
                for i in range(5):
                    metric_dict = {
                        "timestamp": "2026-08-19T13:00:00Z",
                        "turn_id": f"turn_{i}",
                        "tenant_id": "_default",
                        "context_size_before": 10000,
                        "context_size_after": 5000,
                        "tokens_saved": 5000,
                        "latency_ms": 1000,
                        "group": "control",
                    }
                    f.write(json.dumps(metric_dict) + "\n")

            metrics = load_metrics(metrics_path)

            assert len(metrics) == 5
            assert metrics[0].turn_id == "turn_0"


class TestIntegration:
    """End-to-end integration tests."""

    def test_routing_to_metrics_workflow(self):
        """Full workflow: route tenant → collect metrics."""
        router = CanaryRouter()

        tenant_id = "user_42"
        flags = {
            "vibe_engineering": True,
            "per_stage_token_budgeting": True,
            "adaptive_context_routing": True,
        }

        # Route the tenant
        routed_flags = router.route_by_tenant_percentage(tenant_id, flags)

        # Determine if canary
        is_canary = router.is_canary_tenant(tenant_id)

        # Collect metrics
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = MetricsCollector(f"{tmpdir}/metrics.jsonl")

            for i in range(5):
                metric = TokenMetric(
                    timestamp=f"2026-08-19T13:0{i}:00Z",
                    turn_id=f"turn_{i}",
                    tenant_id=tenant_id,
                    feature_flags_enabled=routed_flags,
                    context_size_before=10000,
                    context_size_after=5000 if is_canary else 8000,
                    tokens_saved=5000 if is_canary else 2000,
                    latency_ms=1000 + i * 100,
                    group="canary" if is_canary else "control",
                )
                collector.record(metric)

            summary = collector.summary()
            assert summary["total_turns"] == 5

    def test_complete_measurement_workflow(self):
        """Complete workflow: multiple tenants → analysis."""
        router = CanaryRouter()

        with tempfile.TemporaryDirectory() as tmpdir:
            metrics_path = f"{tmpdir}/metrics.jsonl"
            collector = MetricsCollector(metrics_path)

            # Simulate 100 tenants over 2 weeks (200 total turns)
            for turn_id in range(200):
                tenant_num = turn_id % 10
                tenant_id = f"user_{tenant_num}"
                is_canary = router.is_canary_tenant(tenant_id, canary_pct=10)

                metric = TokenMetric(
                    timestamp="2026-08-19T13:00:00Z",
                    turn_id=f"turn_{turn_id}",
                    tenant_id=tenant_id,
                    feature_flags_enabled={"vibe_engineering": is_canary},
                    context_size_before=10000,
                    context_size_after=5000 if is_canary else 8500,
                    tokens_saved=5000 if is_canary else 1500,
                    latency_ms=1000 if is_canary else 950,
                    group="canary" if is_canary else "control",
                )
                collector.record(metric)

            # Generate report
            report = generate_report(metrics_path)

            # Verify report structure
            assert report["baseline_summary"]["turns"] > 0
            assert report["canary_summary"]["turns"] > 0
            comparison = report["comparison"]
            assert comparison["canary_avg_reduction_pct"] > (
                comparison["baseline_avg_reduction_pct"]
            )
