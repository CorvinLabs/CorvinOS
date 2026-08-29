"""
Measurement Report & Gate Decision Tests — Phase 3.A
=====================================================

Tests for measurement report generation and Go/No-Go gate logic.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch
import sys
import json

sys.path.insert(0, str(Path(__file__).parent.parent))
from validation.measurement_report import (
    MeasurementReport,
    MeasurementReportBuilder,
    GateDecision,
    GateMetrics
)


class TestMeasurementReport:
    """Test suite for measurement reports and gate decisions."""

    def test_gate_metrics_defaults(self):
        """Test that gate metrics have correct defaults."""
        metrics = GateMetrics()
        assert metrics.pass_rate_threshold == 0.80
        assert metrics.flake_threshold == 0.98
        assert metrics.coverage_regression_threshold == -5.0
        assert metrics.min_e2e_coverage == 0.80

    def test_gate_decision_enum(self):
        """Test GateDecision enum values."""
        assert GateDecision.GO.value == "go"
        assert GateDecision.NO_GO.value == "no_go"
        assert GateDecision.HOLD.value == "hold"

    def test_measurement_report_creation(self):
        """Test that MeasurementReport can be created."""
        report = MeasurementReport(timestamp="2026-08-29T10:00:00")
        assert report.timestamp == "2026-08-29T10:00:00"
        assert report.phase == "3A"
        assert report.decision == GateDecision.HOLD

    def test_builder_initialization(self, tmp_path):
        """Test that builder initializes correctly."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        assert builder.report.timestamp is not None
        assert builder.report.decision == GateDecision.HOLD

    def test_builder_add_e2e_results(self, tmp_path):
        """Test adding E2E results to builder."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        e2e_results = {
            "endpoint_1": {"reachability_status": "pass"},
            "endpoint_2": {"reachability_status": "pass"}
        }

        builder.add_e2e_results(e2e_results)

        assert builder.report.e2e_wiring_results == e2e_results
        assert builder.report.total_e2e_checks == 2

    def test_builder_add_reproducibility_results(self, tmp_path):
        """Test adding reproducibility results to builder."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        repro_results = {
            "test_a": {"flake_status": "stable"},
            "test_b": {"flake_status": "stable"}
        }

        builder.add_reproducibility_results(repro_results)

        assert builder.report.reproducibility_results == repro_results
        assert builder.report.total_tests_for_flake == 2

    def test_builder_add_coverage_impact(self, tmp_path):
        """Test adding coverage impact result to builder."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        coverage_result = {
            "delta_coverage_percent": 2.5,
            "risk_level": "LOW"
        }

        builder.add_coverage_impact(coverage_result)

        assert builder.report.coverage_impact_result == coverage_result

    def test_gate_decision_all_pass(self, tmp_path):
        """Test that gate decision is GO when all metrics pass."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)

        e2e_results = {
            "endpoint_1": {"reachability_status": "pass"},
            "endpoint_2": {"reachability_status": "pass"}
        }
        repro_results = {
            "test_a": {"flake_status": "stable"},
            "test_b": {"flake_status": "stable"}
        }
        coverage_result = {
            "delta_coverage_percent": 2.5,
            "risk_level": "LOW"
        }

        builder.add_e2e_results(e2e_results)
        builder.add_reproducibility_results(repro_results)
        builder.add_coverage_impact(coverage_result)

        decision = builder.compute_gate_decision()

        assert decision == GateDecision.GO
        assert len(builder.report.blocking_issues) == 0

    def test_gate_decision_no_go_on_e2e_fail(self, tmp_path):
        """Test that gate decision is NO_GO if E2E check fails."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)

        e2e_results = {
            "endpoint_1": {"reachability_status": "fail"}
        }
        repro_results = {
            "test_a": {"flake_status": "stable"}
        }

        builder.add_e2e_results(e2e_results)
        builder.add_reproducibility_results(repro_results)

        decision = builder.compute_gate_decision()

        assert decision == GateDecision.NO_GO
        assert len(builder.report.blocking_issues) > 0
        assert "unreachable" in builder.report.blocking_issues[0].lower()

    def test_gate_decision_no_go_on_broken_test(self, tmp_path):
        """Test that gate decision is NO_GO if test is broken."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)

        e2e_results = {
            "endpoint_1": {"reachability_status": "pass"}
        }
        repro_results = {
            "test_a": {"flake_status": "broken"}
        }

        builder.add_e2e_results(e2e_results)
        builder.add_reproducibility_results(repro_results)

        decision = builder.compute_gate_decision()

        assert decision == GateDecision.NO_GO
        assert len(builder.report.blocking_issues) > 0
        assert "broken" in builder.report.blocking_issues[0].lower()

    def test_gate_decision_warning_on_flaky_test(self, tmp_path):
        """Test that gate gives warning on flaky (but not broken) test."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)

        e2e_results = {
            "endpoint_1": {"reachability_status": "pass"}
        }
        repro_results = {
            "test_a": {"flake_status": "stable"},
            "test_b": {"flake_status": "flaky"}
        }

        builder.add_e2e_results(e2e_results)
        builder.add_reproducibility_results(repro_results)

        decision = builder.compute_gate_decision()

        assert decision == GateDecision.GO  # Still GO, but with warning
        assert len(builder.report.warnings) > 0
        assert "flaky" in builder.report.warnings[0].lower()

    def test_gate_decision_no_go_on_coverage_regression(self, tmp_path):
        """Test that gate decision is NO_GO if coverage regresses too much."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)

        e2e_results = {
            "endpoint_1": {"reachability_status": "pass"}
        }
        repro_results = {
            "test_a": {"flake_status": "stable"}
        }
        coverage_result = {
            "delta_coverage_percent": -6.0,  # Exceeds -5% threshold
            "risk_level": "HIGH"
        }

        builder.add_e2e_results(e2e_results)
        builder.add_reproducibility_results(repro_results)
        builder.add_coverage_impact(coverage_result)

        decision = builder.compute_gate_decision()

        assert decision == GateDecision.NO_GO
        assert len(builder.report.blocking_issues) > 0
        assert "coverage" in builder.report.blocking_issues[0].lower()

    def test_gate_decision_warning_on_coverage_high_risk(self, tmp_path):
        """Test that gate gives warning on HIGH risk coverage."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)

        e2e_results = {
            "endpoint_1": {"reachability_status": "pass"}
        }
        repro_results = {
            "test_a": {"flake_status": "stable"}
        }
        coverage_result = {
            "delta_coverage_percent": -3.0,  # Within threshold but HIGH risk
            "risk_level": "HIGH"
        }

        builder.add_e2e_results(e2e_results)
        builder.add_reproducibility_results(repro_results)
        builder.add_coverage_impact(coverage_result)

        decision = builder.compute_gate_decision()

        assert decision == GateDecision.GO
        assert len(builder.report.warnings) > 0
        assert "risk" in builder.report.warnings[0].lower()

    def test_report_to_json(self, tmp_path):
        """Test that report can be serialized to JSON."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        builder.add_e2e_results({"endpoint_1": {"reachability_status": "pass"}})
        builder.add_reproducibility_results({"test_a": {"flake_status": "stable"}})
        builder.compute_gate_decision()

        json_str = builder.to_json()
        data = json.loads(json_str)

        assert data["timestamp"] is not None
        assert data["decision"] == "go"
        assert data["total_e2e_checks"] == 1

    def test_report_summary_generation(self, tmp_path):
        """Test summary string generation."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        builder.add_e2e_results({"endpoint_1": {"reachability_status": "pass"}})
        builder.add_reproducibility_results({"test_a": {"flake_status": "stable"}})
        builder.compute_gate_decision()

        summary = builder.summary()

        assert "PHASE 3 MEASUREMENT REPORT" in summary
        assert "GATE DECISION" in summary
        assert "GO" in summary

    def test_report_save_to_file(self, tmp_path):
        """Test that report can be saved to file."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        builder.add_e2e_results({"endpoint_1": {"reachability_status": "pass"}})
        builder.compute_gate_decision()

        output_path = builder.save_to_file(tmp_path / "test_report.json")

        assert output_path.exists()
        with open(output_path) as f:
            data = json.load(f)
        assert data["decision"] == "go"

    def test_summarize_e2e_with_results(self, tmp_path):
        """Test E2E summary generation."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        e2e_results = {
            "endpoint_1": {"reachability_status": "pass"},
            "endpoint_2": {"reachability_status": "pass"}
        }
        builder.add_e2e_results(e2e_results)

        summary = builder._summarize_e2e()

        assert "2/2" in summary or "reachable" in summary.lower()

    def test_summarize_reproducibility_with_results(self, tmp_path):
        """Test reproducibility summary generation."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        repro_results = {
            "test_a": {"flake_status": "stable"},
            "test_b": {"flake_status": "flaky"}
        }
        builder.add_reproducibility_results(repro_results)

        summary = builder._summarize_reproducibility()

        assert "Stable" in summary
        assert "Flaky" in summary

    def test_summarize_coverage_with_results(self, tmp_path):
        """Test coverage summary generation."""
        builder = MeasurementReportBuilder(repo_root=tmp_path)
        coverage_result = {
            "baseline": {"total_coverage_percent": 80.0},
            "current": {"total_coverage_percent": 82.0},
            "delta_coverage_percent": 2.0
        }
        builder.add_coverage_impact(coverage_result)

        summary = builder._summarize_coverage()

        assert "80.0" in summary or "coverage" in summary.lower()
        assert "2.0" in summary or "+" in summary
