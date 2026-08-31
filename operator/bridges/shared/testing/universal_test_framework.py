"""
ADR-0087 M9: Universal Testing Framework

Goal: Unified Tier-1/2/3 test runner across all 5 WorkerEngines.

Problem: Each engine (CC, Codex, OpenCode, Hermes, Copilot) requires different
test setups and validation. M9 provides a single framework abstraction.

Architecture:
  - EngineTestRunner: Base class for all engine tests
  - TierValidator: Validate test tier coverage (Tier-1 syntax, Tier-2 unit, Tier-3 E2E)
  - TestCase: Dataclass describing a single test
  - TestMatrix: Run same test across all 5 engines + compare results
  - CoverageReporter: Generate tier coverage report

Constraints:
  - No new audit events (testing framework is internal)
  - No breaking changes to M1–M8 APIs
  - Tier-1: syntax/structure validation only (no spawning)
  - Tier-2: unit tests (mocked spawning)
  - Tier-3: E2E tests (real spawning, all 5 engines)

Compliance (from CLAUDE.md):
  - L10/L16/L33: No changes (framework does not mutate state)
  - ADR-0007: All test results scoped to tenant_id

Test Coverage Standard (from M1–M4):
  - Tier-1: All 5 engines pass syntax validation
  - Tier-2: All 5 engines pass unit tests
  - Tier-3: All 5 engines pass E2E tests (real behavior)
"""

from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class TestTier(Enum):
    """Test tier classification."""
    TIER_1 = "tier_1"  # Syntax / structure validation
    TIER_2 = "tier_2"  # Unit tests (mocked spawning)
    TIER_3 = "tier_3"  # E2E tests (real spawning)


@dataclass
class TestCase:
    """Single test case definition."""
    name: str
    tier: TestTier
    description: str
    engine_ids: List[str] = field(default_factory=lambda: [
        "claude_code", "codex", "opencode", "hermes", "copilot"
    ])  # Run on all engines by default
    input_data: Dict[str, Any] = field(default_factory=dict)
    expected_output: Dict[str, Any] = field(default_factory=dict)
    setup_fn: Optional[Callable] = None  # Optional setup (Tier-2/3)
    teardown_fn: Optional[Callable] = None  # Optional teardown


@dataclass
class TestResult:
    """Result from running a single test."""
    test_name: str
    engine_id: str
    tier: TestTier
    status: str  # "pass" | "fail" | "skip"
    error: Optional[str] = None
    duration_ms: int = 0


class EngineTestRunner:
    """
    Base runner for tests on a single engine.

    Subclasses implement engine-specific logic:
      - ClaudeCodeTestRunner
      - CodexTestRunner
      - OpenCodeTestRunner
      - HermesTestRunner
      - CopilotTestRunner
    """

    def __init__(self, engine_id: str, tenant_id: str = "_default"):
        """
        Initialize runner for an engine.

        Args:
            engine_id: Engine identifier ("claude_code", "hermes", etc.)
            tenant_id: Tenant ID (ADR-0007)
        """
        self.engine_id = engine_id
        self.tenant_id = tenant_id

    def run_test(self, test_case: TestCase) -> TestResult:
        """
        Run a single test case on this engine.

        Args:
            test_case: Test to run

        Returns:
            TestResult with status and optional error

        Must be implemented by subclass.
        """
        # TODO: Implement in Iteration 2
        raise NotImplementedError("M9 Iteration 1 spec")

    def validate_tier_1(self, test_case: TestCase) -> bool:
        """
        Tier-1: Syntax and structure validation.

        Args:
            test_case: Test to validate

        Returns:
            True if passes Tier-1 validation

        Must be implemented by subclass.
        """
        # TODO: Implement in Iteration 2
        raise NotImplementedError("M9 Iteration 1 spec")

    def validate_tier_2(self, test_case: TestCase) -> bool:
        """
        Tier-2: Unit test (mocked spawning).

        Args:
            test_case: Test to run

        Returns:
            True if passes Tier-2

        Must be implemented by subclass.
        """
        # TODO: Implement in Iteration 2
        raise NotImplementedError("M9 Iteration 1 spec")

    def validate_tier_3(self, test_case: TestCase) -> bool:
        """
        Tier-3: E2E test (real spawning).

        Args:
            test_case: Test to run

        Returns:
            True if passes Tier-3

        Must be implemented by subclass.
        """
        # TODO: Implement in Iteration 2
        raise NotImplementedError("M9 Iteration 1 spec")


class TierValidator:
    """Validate test tier classification and coverage."""

    @staticmethod
    def validate_tier_classification(test_case: TestCase) -> bool:
        """
        Validate that test tier is valid.

        Args:
            test_case: Test to validate

        Returns:
            True if tier is valid

        Rules:
          - Tier-1: No spawning, syntax only
          - Tier-2: Mocked spawning
          - Tier-3: Real spawning
        """
        return test_case.tier in (TestTier.TIER_1, TestTier.TIER_2, TestTier.TIER_3)

    @staticmethod
    def validate_coverage(test_results: List[TestResult]) -> Dict[str, Any]:
        """
        Validate tier coverage across all engines.

        Args:
            test_results: List of test results

        Returns:
            {
                "tier_1_coverage": float (0.0-1.0),
                "tier_2_coverage": float,
                "tier_3_coverage": float,
                "missing_engines": {tier: [engine_ids]},
            }
        """
        all_engines = {"claude_code", "codex", "opencode", "hermes", "copilot"}
        coverage = {}

        for tier in [TestTier.TIER_1, TestTier.TIER_2, TestTier.TIER_3]:
            tier_results = [r for r in test_results if r.tier == tier]
            if not tier_results:
                coverage[f"{tier.value}_coverage"] = 0.0
                coverage[f"missing_{tier.value}"] = list(all_engines)
                continue

            passed_results = [r for r in tier_results if r.status == "pass"]
            coverage[f"{tier.value}_coverage"] = len(passed_results) / len(tier_results)

            # Find engines that didn't run this tier
            tested_engines = {r.engine_id for r in tier_results}
            missing = list(all_engines - tested_engines)
            if missing:
                coverage[f"missing_{tier.value}"] = missing

        return coverage


class TestMatrix:
    """
    Run same test across all 5 engines and compare results.

    Example:
        matrix = TestMatrix([runner_cc, runner_codex, runner_hermes, ...])
        results = matrix.run_test(test_case)
        comparison = matrix.compare_results(results)
    """

    def __init__(self, runners: Dict[str, EngineTestRunner]):
        """
        Initialize matrix with runners for all engines.

        Args:
            runners: Dict mapping engine_id -> EngineTestRunner
        """
        self.runners = runners

    def run_test(self, test_case: TestCase) -> List[TestResult]:
        """
        Run test across all engines (that support it).

        Args:
            test_case: Test to run

        Returns:
            List of TestResult (one per engine)
        """
        import time

        results = []
        for engine_id in test_case.engine_ids:
            if engine_id not in self.runners:
                results.append(TestResult(
                    test_name=test_case.name,
                    engine_id=engine_id,
                    tier=test_case.tier,
                    status="skip",
                    error=f"Runner not found for {engine_id}",
                ))
                continue

            runner = self.runners[engine_id]

            # Setup
            if test_case.setup_fn:
                test_case.setup_fn()

            # Run test
            start_time = time.time()
            try:
                if test_case.tier == TestTier.TIER_1:
                    passed = runner.validate_tier_1(test_case)
                elif test_case.tier == TestTier.TIER_2:
                    passed = runner.validate_tier_2(test_case)
                elif test_case.tier == TestTier.TIER_3:
                    passed = runner.validate_tier_3(test_case)
                else:
                    passed = False

                duration_ms = int((time.time() - start_time) * 1000)
                results.append(TestResult(
                    test_name=test_case.name,
                    engine_id=engine_id,
                    tier=test_case.tier,
                    status="pass" if passed else "fail",
                    duration_ms=duration_ms,
                ))
            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)
                results.append(TestResult(
                    test_name=test_case.name,
                    engine_id=engine_id,
                    tier=test_case.tier,
                    status="fail",
                    error=str(e),
                    duration_ms=duration_ms,
                ))

            # Teardown
            if test_case.teardown_fn:
                test_case.teardown_fn()

        return results

    def compare_results(self, results: List[TestResult]) -> Dict[str, Any]:
        """
        Compare results across all engines.

        Args:
            results: List of TestResult from all engines

        Returns:
            {
                "all_pass": bool,
                "engines_pass": [engine_ids],
                "engines_fail": [engine_ids],
                "differences": {engine_id: [differences]},
            }
        """
        engines_pass = [r.engine_id for r in results if r.status == "pass"]
        engines_fail = [r.engine_id for r in results if r.status == "fail"]
        engines_skip = [r.engine_id for r in results if r.status == "skip"]

        all_pass = len(engines_fail) == 0 and len(engines_skip) == 0

        differences = {}
        for r in results:
            if r.status == "fail" and r.error:
                differences[r.engine_id] = [r.error]

        return {
            "all_pass": all_pass,
            "engines_pass": engines_pass,
            "engines_fail": engines_fail,
            "engines_skip": engines_skip,
            "differences": differences,
        }


class CoverageReporter:
    """Generate test coverage report for all tiers and engines."""

    def __init__(self, test_results: List[TestResult]):
        """
        Initialize reporter with test results.

        Args:
            test_results: List of all test results
        """
        self.test_results = test_results

    def generate_summary(self) -> Dict[str, Any]:
        """
        Generate coverage summary.

        Returns:
            {
                "total_tests": int,
                "passed": int,
                "failed": int,
                "tier_coverage": {
                    "tier_1": {"total": int, "passed": int, "pct": float},
                    "tier_2": {...},
                    "tier_3": {...},
                },
                "engine_coverage": {
                    "claude_code": {"total": int, "passed": int, "pct": float},
                    "codex": {...},
                    ...
                },
            }
        """
        total = len(self.test_results)
        passed = len([r for r in self.test_results if r.status == "pass"])
        failed = len([r for r in self.test_results if r.status == "fail"])

        tier_coverage = {}
        for tier in [TestTier.TIER_1, TestTier.TIER_2, TestTier.TIER_3]:
            tier_results = [r for r in self.test_results if r.tier == tier]
            tier_passed = len([r for r in tier_results if r.status == "pass"])
            tier_total = len(tier_results)
            pct = (tier_passed / tier_total * 100) if tier_total > 0 else 0

            tier_coverage[tier.value] = {
                "total": tier_total,
                "passed": tier_passed,
                "pct": round(pct, 1),
            }

        engine_coverage = {}
        all_engines = {"claude_code", "codex", "opencode", "hermes", "copilot"}
        for engine_id in all_engines:
            engine_results = [r for r in self.test_results if r.engine_id == engine_id]
            engine_passed = len([r for r in engine_results if r.status == "pass"])
            engine_total = len(engine_results)
            pct = (engine_passed / engine_total * 100) if engine_total > 0 else 0

            engine_coverage[engine_id] = {
                "total": engine_total,
                "passed": engine_passed,
                "pct": round(pct, 1),
            }

        return {
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "tier_coverage": tier_coverage,
            "engine_coverage": engine_coverage,
        }

    def report_to_json(self, output_file: str) -> None:
        """
        Write coverage report to JSON file.

        Args:
            output_file: Path to output file
        """
        summary = self.generate_summary()

        # Include individual results
        detailed_results = [
            {
                "test_name": r.test_name,
                "engine_id": r.engine_id,
                "tier": r.tier.value,
                "status": r.status,
                "error": r.error,
                "duration_ms": r.duration_ms,
            }
            for r in self.test_results
        ]

        report = {
            "summary": summary,
            "results": detailed_results,
        }

        Path(output_file).write_text(json.dumps(report, indent=2))
