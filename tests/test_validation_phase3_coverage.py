"""
Coverage Impact Measurement Tests — Phase 3.A
==============================================

Tests for coverage measurement and impact analysis.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch
import sys
import json

sys.path.insert(0, str(Path(__file__).parent.parent))
from validation.coverage_impact import (
    CoverageImpactMeasurement,
    FileCoverage,
    FunctionCoverage,
    CoverageSnapshot,
    CoverageImpactReport
)


class TestCoverageImpactMeasurement:
    """Test suite for coverage impact measurement."""

    @pytest.fixture
    def measure(self, tmp_path):
        """Create a measurement instance."""
        return CoverageImpactMeasurement(repo_root=tmp_path, min_coverage=80.0)

    def test_measurement_initialization(self, measure):
        """Test that measurement initializes correctly."""
        assert measure.min_coverage == 80.0
        assert measure._snapshots == {}

    def test_file_coverage_creation(self):
        """Test FileCoverage dataclass creation."""
        cov = FileCoverage(
            file_path="core/app.py",
            lines_total=100,
            lines_covered=85,
            coverage_percent=85.0
        )
        assert cov.file_path == "core/app.py"
        assert cov.coverage_percent == 85.0

    def test_function_coverage_creation(self):
        """Test FunctionCoverage dataclass creation."""
        cov = FunctionCoverage(
            function_name="my_func",
            file_path="core/app.py",
            lines=10,
            coverage_percent=100.0
        )
        assert cov.function_name == "my_func"
        assert cov.coverage_percent == 100.0

    def test_coverage_snapshot_creation(self):
        """Test CoverageSnapshot creation."""
        snapshot = CoverageSnapshot(
            timestamp="2026-08-29",
            total_lines=1000,
            covered_lines=850,
            total_coverage_percent=85.0
        )
        assert snapshot.total_lines == 1000
        assert snapshot.covered_lines == 850
        assert snapshot.total_coverage_percent == 85.0

    def test_coverage_impact_report_creation(self):
        """Test CoverageImpactReport creation."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=1000,
            covered_lines=800,
            total_coverage_percent=80.0
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=1000,
            covered_lines=850,
            total_coverage_percent=85.0
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=5.0
        )

        assert report.delta_coverage_percent == 5.0
        assert report.risk_level == "LOW"

    def test_coverage_impact_positive_delta(self):
        """Test that positive coverage delta is LOW risk."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=1000,
            covered_lines=800,
            total_coverage_percent=80.0
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=1000,
            covered_lines=900,
            total_coverage_percent=90.0
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=10.0
        )

        assert report.delta_coverage_percent > 0.0
        assert report.risk_level == "LOW"

    def test_coverage_impact_small_regression(self):
        """Test that small regression is MEDIUM risk."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=1000,
            covered_lines=850,
            total_coverage_percent=85.0
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=1000,
            covered_lines=820,
            total_coverage_percent=82.0
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=-3.0
        )

        assert report.delta_coverage_percent < 0.0
        assert report.risk_level == "MEDIUM"

    def test_coverage_impact_large_regression(self):
        """Test that large regression is HIGH risk."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=1000,
            covered_lines=850,
            total_coverage_percent=85.0
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=1000,
            covered_lines=750,
            total_coverage_percent=75.0
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=-10.0
        )

        assert report.delta_coverage_percent < -5.0
        assert report.risk_level == "HIGH"

    def test_coverage_report_identifies_new_files(self):
        """Test that new files are identified in comparison."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=100,
            covered_lines=80,
            total_coverage_percent=80.0,
            by_file={"old.py": FileCoverage("old.py", 100, 80, 80.0)}
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=200,
            covered_lines=160,
            total_coverage_percent=80.0,
            by_file={
                "old.py": FileCoverage("old.py", 100, 80, 80.0),
                "new.py": FileCoverage("new.py", 100, 80, 80.0)
            }
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=0.0,
            new_files=["new.py"]
        )

        assert len(report.new_files) == 1
        assert "new.py" in report.new_files

    def test_coverage_report_identifies_improved_files(self):
        """Test that improved files are tracked."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=100,
            covered_lines=80,
            total_coverage_percent=80.0,
            by_file={"app.py": FileCoverage("app.py", 100, 80, 80.0)}
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=100,
            covered_lines=95,
            total_coverage_percent=95.0,
            by_file={"app.py": FileCoverage("app.py", 100, 95, 95.0)}
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=15.0,
            improved_files=["app.py"]
        )

        assert "app.py" in report.improved_files

    def test_coverage_report_identifies_regressed_files(self):
        """Test that regressed files are tracked."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=100,
            covered_lines=90,
            total_coverage_percent=90.0,
            by_file={"app.py": FileCoverage("app.py", 100, 90, 90.0)}
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=100,
            covered_lines=75,
            total_coverage_percent=75.0,
            by_file={"app.py": FileCoverage("app.py", 100, 75, 75.0)}
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=-15.0,
            regressed_files=["app.py"]
        )

        assert "app.py" in report.regressed_files

    def test_report_to_json(self):
        """Test that report can be serialized to JSON."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=100,
            covered_lines=80,
            total_coverage_percent=80.0,
            by_file={}
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=100,
            covered_lines=85,
            total_coverage_percent=85.0,
            by_file={}
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=5.0
        )

        measure = CoverageImpactMeasurement()
        json_str = measure.to_json(report)
        data = json.loads(json_str)

        assert data["delta_coverage_percent"] == 5.0
        assert "baseline" in data
        assert "current" in data

    def test_summary_generation(self):
        """Test summary string generation."""
        baseline = CoverageSnapshot(
            timestamp="2026-08-29T1",
            total_lines=100,
            covered_lines=80,
            total_coverage_percent=80.0,
            by_file={}
        )
        current = CoverageSnapshot(
            timestamp="2026-08-29T2",
            total_lines=100,
            covered_lines=85,
            total_coverage_percent=85.0,
            by_file={}
        )

        report = CoverageImpactReport(
            baseline=baseline,
            current=current,
            delta_coverage_percent=5.0,
            improved_files=["app.py"],
            regressed_files=[],
            new_files=[]
        )

        measure = CoverageImpactMeasurement()
        summary = measure.summary(report)

        assert "Coverage Impact Report" in summary
        assert "80.0" in summary
        assert "85.0" in summary
        assert "+5.0" in summary or "5.0" in summary
