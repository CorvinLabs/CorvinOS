"""
ADR-0087 M9 Tests — Universal Testing Framework

Tier-1: Test case structure, tier classification validation
Tier-2: Runner logic, coverage calculations
Tier-3: Full framework end-to-end (all 5 engines)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from universal_test_framework import (
    TestCase, TestTier, EngineTestRunner, TierValidator,
    TestMatrix, CoverageReporter, TestResult
)


# ============================================================================
# TIER-1: Test Case Structure & Tier Classification
# ============================================================================

class TestTestCaseTier1:
    """Test case structure validation."""

    def test_testcase_structure(self):
        """
        Given: TestCase dataclass
        When: instantiated with values
        Then: all fields present and correct type
        """
        tc = TestCase(
            name="test_system_prompt",
            tier=TestTier.TIER_2,
            description="Test system prompt injection",
            engine_ids=["claude_code", "hermes"],
            input_data={"prompt": "Hello"},
            expected_output={"contains_system": True},
        )

        assert tc.name == "test_system_prompt"
        assert tc.tier == TestTier.TIER_2
        assert len(tc.engine_ids) == 2
        assert tc.input_data["prompt"] == "Hello"

    def test_testcase_default_engines(self):
        """
        Given: TestCase without engine_ids
        When: instantiated
        Then: defaults to all 5 engines
        """
        tc = TestCase(
            name="test",
            tier=TestTier.TIER_1,
            description="Test",
        )

        assert len(tc.engine_ids) == 5
        assert set(tc.engine_ids) == {"claude_code", "codex", "opencode", "hermes", "copilot"}

    def test_tier_classification_valid(self):
        """
        Given: TestTier enum values
        When: accessed
        Then: all three tiers present (TIER_1, TIER_2, TIER_3)
        """
        assert TestTier.TIER_1.value == "tier_1"
        assert TestTier.TIER_2.value == "tier_2"
        assert TestTier.TIER_3.value == "tier_3"


class TestTierValidatorTier1:
    """Tier classification validation."""

    def test_validate_tier_classification_tier1(self):
        """
        Given: TestCase with tier=TIER_1
        When: validate_tier_classification() called
        Then: returns True
        """
        tc = TestCase(
            name="test",
            tier=TestTier.TIER_1,
            description="Syntax test",
        )

        result = TierValidator.validate_tier_classification(tc)
        assert result is True

    def test_validate_tier_classification_tier2(self):
        """
        Given: TestCase with tier=TIER_2
        When: validate_tier_classification() called
        Then: returns True
        """
        tc = TestCase(
            name="test",
            tier=TestTier.TIER_2,
            description="Unit test",
        )

        result = TierValidator.validate_tier_classification(tc)
        assert result is True


# ============================================================================
# TIER-2: Runner Logic & Coverage Calculation
# ============================================================================

class TestEngineTestRunnerTier2:
    """Engine test runner logic."""

    def test_runner_initialization(self):
        """
        Given: EngineTestRunner("claude_code")
        When: instantiated
        Then: engine_id and tenant_id set correctly
        """
        runner = EngineTestRunner("claude_code", tenant_id="_default")

        assert runner.engine_id == "claude_code"
        assert runner.tenant_id == "_default"

    def test_runner_must_implement_run_test(self):
        """
        Given: EngineTestRunner
        When: run_test() called
        Then: raises NotImplementedError (base class)
        """
        runner = EngineTestRunner("claude_code")
        tc = TestCase(name="test", tier=TestTier.TIER_1, description="Test")

        with pytest.raises(NotImplementedError):
            runner.run_test(tc)


class TestCoverageReporterTier2:
    """Coverage reporting logic."""

    def test_coverage_summary_empty(self):
        """
        Given: empty test results list
        When: generate_summary() called
        Then: returns zeros for all metrics
        """
        reporter = CoverageReporter([])
        summary = reporter.generate_summary()

        assert summary["total_tests"] == 0
        assert summary["passed"] == 0
        assert summary["failed"] == 0

    def test_coverage_summary_all_pass(self):
        """
        Given: 15 test results (all pass)
        When: generate_summary() called
        Then: passed=15, failed=0
        """
        results = [
            TestResult(
                test_name=f"test_{i}",
                engine_id="claude_code",
                tier=TestTier.TIER_1,
                status="pass",
            )
            for i in range(15)
        ]

        reporter = CoverageReporter(results)
        summary = reporter.generate_summary()

        assert summary["total_tests"] == 15
        assert summary["passed"] == 15
        assert summary["failed"] == 0

    def test_coverage_by_tier(self):
        """
        Given: test results with Tier-1/2/3 mix
        When: generate_summary() called
        Then: tier_coverage breakdown present
        """
        results = [
            TestResult("test_1", "claude_code", TestTier.TIER_1, "pass"),
            TestResult("test_2", "claude_code", TestTier.TIER_2, "pass"),
            TestResult("test_3", "claude_code", TestTier.TIER_2, "fail"),
            TestResult("test_4", "claude_code", TestTier.TIER_3, "pass"),
        ]

        reporter = CoverageReporter(results)
        summary = reporter.generate_summary()

        assert "tier_coverage" in summary
        assert summary["tier_coverage"]["tier_1"]["total"] == 1
        assert summary["tier_coverage"]["tier_1"]["passed"] == 1
        assert summary["tier_coverage"]["tier_2"]["total"] == 2
        assert summary["tier_coverage"]["tier_2"]["passed"] == 1
        assert summary["tier_coverage"]["tier_3"]["total"] == 1
        assert summary["tier_coverage"]["tier_3"]["passed"] == 1

    def test_coverage_by_engine(self):
        """
        Given: test results across all 5 engines
        When: generate_summary() called
        Then: engine_coverage for all 5 engines present
        """
        results = [
            TestResult("test", engine, TestTier.TIER_1, "pass")
            for engine in ["claude_code", "codex", "opencode", "hermes", "copilot"]
        ]

        reporter = CoverageReporter(results)
        summary = reporter.generate_summary()

        assert "engine_coverage" in summary
        assert len(summary["engine_coverage"]) == 5
        assert "claude_code" in summary["engine_coverage"]
        assert "copilot" in summary["engine_coverage"]


# ============================================================================
# TIER-3: Full Framework E2E (All 5 Engines)
# ============================================================================

class TestUniversalFrameworkE2E:
    """End-to-end universal framework validation."""

    def test_e2e_testmatrix_all_engines(self, tmp_path):
        """
        Tier-3: Run test across all 5 engines.
        Given: TestMatrix with all 5 engine runners
        When: run_test() called
        Then: returns results for all 5 engines
        """
        # Create mock runners for all 5 engines
        class MockRunner(EngineTestRunner):
            def validate_tier_1(self, test_case):
                # All engines pass Tier-1
                return True

        runners = {
            engine_id: MockRunner(engine_id)
            for engine_id in ["claude_code", "codex", "opencode", "hermes", "copilot"]
        }

        # Create test case
        test_case = TestCase(
            name="system_prompt_injection",
            tier=TestTier.TIER_1,
            description="Test system prompt injection",
            engine_ids=list(runners.keys()),
        )

        # Run test across all engines
        matrix = TestMatrix(runners)
        results = matrix.run_test(test_case)

        # Verify results
        assert len(results) == 5
        assert all(r.engine_id in runners for r in results)
        assert all(r.status == "pass" for r in results)

    def test_e2e_framework_full_workflow(self, tmp_path):
        """
        Tier-3: Full workflow end-to-end.
        Given: Complete test case, all runners, coverage reporter
        When: test executed and reported
        Then: coverage report complete and consistent
        """
        # Create mock runners
        class MockRunner(EngineTestRunner):
            def validate_tier_1(self, test_case):
                return self.engine_id != "hermes"  # hermes fails

            def validate_tier_2(self, test_case):
                return self.engine_id != "codex"  # codex fails

            def validate_tier_3(self, test_case):
                return True  # all pass E2E

        runners = {
            engine_id: MockRunner(engine_id)
            for engine_id in ["claude_code", "codex", "opencode", "hermes", "copilot"]
        }

        # Test across all tiers
        test_cases = [
            TestCase(
                name="test_tier_1",
                tier=TestTier.TIER_1,
                description="Tier 1 test",
                engine_ids=list(runners.keys()),
            ),
            TestCase(
                name="test_tier_2",
                tier=TestTier.TIER_2,
                description="Tier 2 test",
                engine_ids=list(runners.keys()),
            ),
            TestCase(
                name="test_tier_3",
                tier=TestTier.TIER_3,
                description="Tier 3 test",
                engine_ids=list(runners.keys()),
            ),
        ]

        matrix = TestMatrix(runners)
        all_results = []
        for test_case in test_cases:
            results = matrix.run_test(test_case)
            all_results.extend(results)

        # Generate coverage report
        reporter = CoverageReporter(all_results)
        summary = reporter.generate_summary()

        # Verify coverage
        assert summary["total_tests"] == 15  # 5 engines × 3 tiers
        assert summary["passed"] == 13  # (5-1) + (5-1) + 5 = 4 + 4 + 5 = 13
        assert summary["failed"] == 2  # hermes tier_1 + codex tier_2

        # Verify tier breakdown
        assert summary["tier_coverage"]["tier_1"]["passed"] == 4  # hermes fails
        assert summary["tier_coverage"]["tier_1"]["pct"] == 80.0
        assert summary["tier_coverage"]["tier_2"]["passed"] == 4  # codex fails
        assert summary["tier_coverage"]["tier_2"]["pct"] == 80.0
        assert summary["tier_coverage"]["tier_3"]["passed"] == 5  # all pass
        assert summary["tier_coverage"]["tier_3"]["pct"] == 100.0

        # Verify engine breakdown
        assert summary["engine_coverage"]["claude_code"]["passed"] == 3
        assert summary["engine_coverage"]["hermes"]["passed"] == 2  # fails tier_1
        assert summary["engine_coverage"]["codex"]["passed"] == 2  # fails tier_2

        # Export and verify JSON
        output_file = str(tmp_path / "report.json")
        reporter.report_to_json(output_file)

        import json
        with open(output_file) as f:
            exported = json.load(f)

        assert exported["summary"]["total_tests"] == 15
        assert len(exported["results"]) == 15
