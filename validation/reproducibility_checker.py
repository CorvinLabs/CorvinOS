"""
Reproducibility Checker — Phase 3.A
====================================

Detects flaky tests by running them 3 consecutive times and measuring:
- Pass rate across runs (target: 100%, warn if <98%)
- Latency variance (target: <10% CV)
- Output consistency (warn if output differs)

Fail-closed: any test that fails ≥1 of 3 runs is marked flaky.
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


class FlakeStatus(Enum):
    """Flake test result."""
    STABLE = "stable"      # 3/3 passes
    FLAKY = "flaky"        # 1-2/3 passes
    BROKEN = "broken"      # 0/3 passes
    INCONCLUSIVE = "inconclusive"  # error during run


@dataclass
class SingleTestRun:
    """Result of one test execution."""
    run_number: int
    passed: bool
    exit_code: int
    duration_seconds: float
    output: str
    error: Optional[str] = None


@dataclass
class ReproducibilityResult:
    """Aggregate flakiness data for a test."""
    test_name: str
    test_file: str
    num_runs: int
    runs: List[SingleTestRun] = field(default_factory=list)
    pass_rate: float = 0.0  # 0.0 to 1.0
    flake_status: FlakeStatus = FlakeStatus.INCONCLUSIVE
    mean_latency: float = 0.0
    latency_cv: float = 0.0  # coefficient of variation
    output_consistent: bool = True


class ReproducibilityChecker:
    """
    Runs tests N times and measures flakiness.

    Usage:
        checker = ReproducibilityChecker(repo_root="/path/to/CorvinOS", num_runs=3)
        result = checker.check_test("tests/test_foo.py::test_bar")
        if result.flake_status != FlakeStatus.STABLE:
            print(f"⚠ {result.test_name} is flaky: {result.pass_rate * 100}%")
    """

    def __init__(
        self,
        repo_root: Path = None,
        num_runs: int = 3,
        timeout: int = 30,
        flake_threshold: float = 0.98
    ):
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self.num_runs = num_runs
        self.timeout = timeout
        self.flake_threshold = flake_threshold  # ≥98% pass rate = stable

    def check_test(self, test_path: str) -> ReproducibilityResult:
        """
        Run a single test N times and measure flakiness.

        Args:
            test_path: "tests/test_foo.py::test_bar" or just "test_bar" (finds first match)

        Returns:
            ReproducibilityResult with flake_status (stable/flaky/broken)
        """
        logger.info(f"[REPRODUCIBILITY] Checking: {test_path} ({self.num_runs} runs)")

        # Extract test name
        test_name = test_path.split("::")[-1]
        test_file = test_path.split("::")[0] if "::" in test_path else test_path

        result = ReproducibilityResult(
            test_name=test_name,
            test_file=test_file,
            num_runs=self.num_runs
        )

        # Run test N times
        passes = 0
        latencies = []
        outputs = []

        for run_num in range(1, self.num_runs + 1):
            logger.info(f"  Run {run_num}/{self.num_runs}...", end=" ")
            try:
                run_result = self._run_single_test(test_path)
                result.runs.append(run_result)

                if run_result.passed:
                    passes += 1
                    logger.info("✓ PASS")
                else:
                    logger.info(f"✗ FAIL (exit {run_result.exit_code})")

                latencies.append(run_result.duration_seconds)
                outputs.append(run_result.output)

            except Exception as e:
                logger.error(f"✗ ERROR: {e}")
                result.runs.append(SingleTestRun(
                    run_number=run_num,
                    passed=False,
                    exit_code=-1,
                    duration_seconds=0.0,
                    output="",
                    error=str(e)
                ))

        # Aggregate
        result.pass_rate = passes / self.num_runs
        result.latency_cv = self._coefficient_of_variation(latencies)
        result.mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
        result.output_consistent = len(set(outputs)) == 1  # All outputs identical

        # Classify
        if result.pass_rate == 1.0:
            result.flake_status = FlakeStatus.STABLE
        elif result.pass_rate == 0.0:
            result.flake_status = FlakeStatus.BROKEN
        else:
            result.flake_status = FlakeStatus.FLAKY

        # Warn if pass rate below threshold
        if result.pass_rate < self.flake_threshold:
            logger.warning(
                f"  ⚠ FLAKY: {result.pass_rate * 100:.1f}% pass rate "
                f"(target ≥{self.flake_threshold * 100:.0f}%)"
            )

        logger.info(f"[REPRODUCIBILITY] Result: {result.flake_status.value} "
                   f"({result.pass_rate * 100:.0f}%)")
        return result

    def check_batch(self, test_paths: List[str]) -> Dict[str, ReproducibilityResult]:
        """Run reproducibility check on multiple tests."""
        results = {}
        for test_path in test_paths:
            results[test_path] = self.check_test(test_path)
        return results

    def _run_single_test(self, test_path: str) -> SingleTestRun:
        """Execute one test run via pytest."""
        start_time = time.time()
        try:
            cmd = ["python", "-m", "pytest", test_path, "-xvs", "--tb=short"]
            proc = subprocess.run(
                cmd,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
            duration = time.time() - start_time

            return SingleTestRun(
                run_number=0,  # Set by caller
                passed=proc.returncode == 0,
                exit_code=proc.returncode,
                duration_seconds=duration,
                output=proc.stdout,
                error=proc.stderr if proc.returncode != 0 else None
            )
        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return SingleTestRun(
                run_number=0,
                passed=False,
                exit_code=-1,
                duration_seconds=duration,
                output="",
                error=f"Timeout after {self.timeout}s"
            )
        except Exception as e:
            duration = time.time() - start_time
            return SingleTestRun(
                run_number=0,
                passed=False,
                exit_code=-1,
                duration_seconds=duration,
                output="",
                error=str(e)
            )

    @staticmethod
    def _coefficient_of_variation(values: List[float]) -> float:
        """Compute CV = std / mean. Higher = more variance."""
        if not values or len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        if mean == 0:
            return 0.0
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std = variance ** 0.5
        return std / mean

    def to_json(self, result: ReproducibilityResult) -> str:
        """Export result as JSON."""
        data = asdict(result)
        data['flake_status'] = result.flake_status.value
        data['runs'] = [asdict(run) for run in result.runs]
        return json.dumps(data, indent=2)

    def summary(self, results: Dict[str, ReproducibilityResult]) -> str:
        """Generate a human-readable summary."""
        stable = sum(1 for r in results.values() if r.flake_status == FlakeStatus.STABLE)
        flaky = sum(1 for r in results.values() if r.flake_status == FlakeStatus.FLAKY)
        broken = sum(1 for r in results.values() if r.flake_status == FlakeStatus.BROKEN)

        summary_lines = [
            f"Reproducibility Check Summary ({self.num_runs} runs each):",
            f"  Stable:  {stable}/{len(results)} ({stable/len(results)*100:.0f}%)",
            f"  Flaky:   {flaky}/{len(results)} ({flaky/len(results)*100:.0f}%)",
            f"  Broken:  {broken}/{len(results)} ({broken/len(results)*100:.0f}%)",
        ]

        if flaky > 0:
            summary_lines.append("\nFlaky tests:")
            for name, r in results.items():
                if r.flake_status == FlakeStatus.FLAKY:
                    summary_lines.append(f"  {name}: {r.pass_rate*100:.0f}% (CV={r.latency_cv:.2f})")

        return "\n".join(summary_lines)
