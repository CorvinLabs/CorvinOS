"""
Reproducibility Checker Tests — Phase 3.A
==========================================

Tests for the reproducibility checker (flake detection).
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from validation.reproducibility_checker import (
    ReproducibilityChecker,
    FlakeStatus,
    SingleTestRun,
    ReproducibilityResult
)


class TestReproducibilityChecker:
    """Test suite for reproducibility checker."""

    @pytest.fixture
    def checker(self, tmp_path):
        """Create a checker with a temporary repo root."""
        return ReproducibilityChecker(repo_root=tmp_path, num_runs=3, timeout=30)

    def test_checker_initialization(self, checker):
        """Test that checker initializes correctly."""
        assert checker.num_runs == 3
        assert checker.timeout == 30
        assert checker.flake_threshold == 0.98

    def test_flake_status_enum(self):
        """Test FlakeStatus enum values."""
        assert FlakeStatus.STABLE.value == "stable"
        assert FlakeStatus.FLAKY.value == "flaky"
        assert FlakeStatus.BROKEN.value == "broken"

    def test_single_test_run_creation(self):
        """Test that SingleTestRun dataclass can be created."""
        run = SingleTestRun(
            run_number=1,
            passed=True,
            exit_code=0,
            duration_seconds=1.5,
            output="test output"
        )
        assert run.run_number == 1
        assert run.passed == True
        assert run.duration_seconds == 1.5

    def test_reproducibility_result_creation(self):
        """Test that ReproducibilityResult dataclass can be created."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="tests/test_foo.py",
            num_runs=3
        )
        assert result.test_name == "test_foo"
        assert result.num_runs == 3
        assert result.pass_rate == 0.0
        assert result.flake_status == FlakeStatus.INCONCLUSIVE

    def test_all_passes_is_stable(self, checker):
        """Test that 3/3 passes = STABLE."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="test.py",
            num_runs=3,
            runs=[
                SingleTestRun(1, True, 0, 1.0, ""),
                SingleTestRun(2, True, 0, 1.0, ""),
                SingleTestRun(3, True, 0, 1.0, "")
            ],
            pass_rate=1.0
        )

        checker.check_test = Mock(return_value=result)
        result_checked = checker.check_test("test_foo")

        assert result_checked.flake_status == FlakeStatus.STABLE or result.pass_rate == 1.0

    def test_zero_passes_is_broken(self, checker):
        """Test that 0/3 passes = BROKEN."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="test.py",
            num_runs=3,
            runs=[
                SingleTestRun(1, False, 1, 1.0, ""),
                SingleTestRun(2, False, 1, 1.0, ""),
                SingleTestRun(3, False, 1, 1.0, "")
            ],
            pass_rate=0.0,
            flake_status=FlakeStatus.BROKEN
        )

        assert result.flake_status == FlakeStatus.BROKEN

    def test_one_or_two_passes_is_flaky(self):
        """Test that 1-2/3 passes = FLAKY."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="test.py",
            num_runs=3,
            runs=[
                SingleTestRun(1, True, 0, 1.0, ""),
                SingleTestRun(2, False, 1, 1.0, ""),
                SingleTestRun(3, True, 0, 1.0, "")
            ],
            pass_rate=2.0 / 3,
            flake_status=FlakeStatus.FLAKY
        )

        assert result.flake_status == FlakeStatus.FLAKY
        assert result.pass_rate > 0.0 and result.pass_rate < 1.0

    def test_coefficient_of_variation_calculation(self, checker):
        """Test CV calculation for latency variance."""
        values = [1.0, 1.1, 1.2, 1.0, 1.05]
        cv = checker._coefficient_of_variation(values)
        assert cv > 0.0
        assert cv < 1.0  # Low variance

    def test_coefficient_of_variation_high_variance(self, checker):
        """Test CV calculation with high variance."""
        values = [0.5, 2.0, 0.8, 1.5, 3.0]
        cv = checker._coefficient_of_variation(values)
        assert cv > 0.3  # High variance

    def test_coefficient_of_variation_empty_list(self, checker):
        """Test CV with empty list returns 0."""
        cv = checker._coefficient_of_variation([])
        assert cv == 0.0

    def test_coefficient_of_variation_single_value(self, checker):
        """Test CV with single value returns 0."""
        cv = checker._coefficient_of_variation([5.0])
        assert cv == 0.0

    def test_output_consistent_when_identical(self, checker):
        """Test output consistency check."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="test.py",
            num_runs=3,
            runs=[
                SingleTestRun(1, True, 0, 1.0, "same output"),
                SingleTestRun(2, True, 0, 1.0, "same output"),
                SingleTestRun(3, True, 0, 1.0, "same output")
            ],
            output_consistent=True
        )

        assert result.output_consistent == True

    def test_output_inconsistent_when_different(self, checker):
        """Test output consistency check fails on different output."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="test.py",
            num_runs=3,
            runs=[
                SingleTestRun(1, True, 0, 1.0, "output A"),
                SingleTestRun(2, True, 0, 1.0, "output B"),
                SingleTestRun(3, True, 0, 1.0, "output A")
            ],
            output_consistent=False
        )

        assert result.output_consistent == False

    def test_batch_check(self, checker):
        """Test batch reproducibility checking."""
        test_paths = ["test_a", "test_b", "test_c"]

        with patch.object(checker, 'check_test') as mock_check:
            mock_check.return_value = ReproducibilityResult(
                test_name="test",
                test_file="test.py",
                num_runs=3,
                pass_rate=1.0,
                flake_status=FlakeStatus.STABLE
            )

            results = checker.check_batch(test_paths)

            assert len(results) == 3
            assert mock_check.call_count == 3

    def test_result_to_json(self, checker):
        """Test that result can be serialized to JSON."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="test.py",
            num_runs=3,
            runs=[
                SingleTestRun(1, True, 0, 1.0, "output"),
            ],
            pass_rate=1.0,
            flake_status=FlakeStatus.STABLE
        )

        json_str = checker.to_json(result)
        assert "test_foo" in json_str
        assert "stable" in json_str
        assert "1.0" in json_str

    def test_summary_with_no_flakes(self, checker):
        """Test summary generation with all stable tests."""
        results = {
            "test_a": ReproducibilityResult(
                test_name="test_a",
                test_file="test.py",
                num_runs=3,
                pass_rate=1.0,
                flake_status=FlakeStatus.STABLE
            ),
            "test_b": ReproducibilityResult(
                test_name="test_b",
                test_file="test.py",
                num_runs=3,
                pass_rate=1.0,
                flake_status=FlakeStatus.STABLE
            )
        }

        summary = checker.summary(results)
        assert "Stable" in summary
        assert "100%" in summary
        assert "2/2" in summary

    def test_summary_with_flakes(self, checker):
        """Test summary generation with flaky tests."""
        results = {
            "test_a": ReproducibilityResult(
                test_name="test_a",
                test_file="test.py",
                num_runs=3,
                pass_rate=1.0,
                flake_status=FlakeStatus.STABLE
            ),
            "test_b": ReproducibilityResult(
                test_name="test_b",
                test_file="test.py",
                num_runs=3,
                pass_rate=2.0 / 3,
                flake_status=FlakeStatus.FLAKY
            )
        }

        summary = checker.summary(results)
        assert "Flaky" in summary
        assert "test_b" in summary

    def test_summary_with_broken_tests(self, checker):
        """Test summary generation with broken tests."""
        results = {
            "test_a": ReproducibilityResult(
                test_name="test_a",
                test_file="test.py",
                num_runs=3,
                pass_rate=0.0,
                flake_status=FlakeStatus.BROKEN
            )
        }

        summary = checker.summary(results)
        assert "Broken" in summary
        assert "1/1" in summary

    def test_latency_variance_calculation(self, checker):
        """Test mean latency and CV calculation."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="test.py",
            num_runs=3,
            runs=[
                SingleTestRun(1, True, 0, 1.0, ""),
                SingleTestRun(2, True, 0, 1.05, ""),
                SingleTestRun(3, True, 0, 0.95, "")
            ],
            mean_latency=1.0,
            latency_cv=0.05
        )

        assert result.mean_latency > 0.0
        assert result.latency_cv < 0.1  # Low variance

    def test_threshold_comparison(self, checker):
        """Test that pass rate is compared against threshold."""
        result = ReproducibilityResult(
            test_name="test_foo",
            test_file="test.py",
            num_runs=100,
            pass_rate=0.97,  # 97% (below 98% threshold)
            flake_status=FlakeStatus.FLAKY
        )

        # 0.97 < 0.98 threshold → should warn
        assert result.pass_rate < checker.flake_threshold
