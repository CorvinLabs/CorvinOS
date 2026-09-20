"""Layer 2: Test-based validation for generated skills.

Checks:
- Discover and execute pytest tests
- Measure and enforce code coverage >= 85%
- Parse pytest output: pass/fail, coverage %
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .result import ValidationResult


class TestValidator:
    """Runs pytest and validates test coverage (test-based validation)."""

    def __init__(self, min_coverage: float = 85.0):
        """Initialize with minimum required coverage percentage.

        Args:
            min_coverage: Minimum required code coverage in percent (default: 85.0).
        """
        self.min_coverage = min_coverage

    def validate(self, skill_dir: Path) -> ValidationResult:
        """Run pytest and validate test coverage.

        Args:
            skill_dir: Path to skill directory (must contain tests/ with test_*.py files).

        Returns:
            ValidationResult with passed=True iff all tests pass and coverage >= min_coverage.
        """
        errors: list[str] = []
        warnings: list[str] = []
        metrics: dict[str, Any] = {}

        # Convert to Path if string
        skill_dir = Path(skill_dir)

        # Check 1: Skill directory exists
        if not skill_dir.is_dir():
            errors.append(f"skill_dir does not exist: {skill_dir}")
            return ValidationResult(
                layer=2,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )

        # Check 2: tests/ directory exists
        tests_dir = skill_dir / "tests"
        if not tests_dir.is_dir():
            errors.append(f"tests/ directory not found at {tests_dir}")
            return ValidationResult(
                layer=2,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )

        # Check 3: At least one test file exists
        test_files = [
            f for f in tests_dir.glob("**/*.py")
            if f.name.startswith("test_") or f.name.endswith("_test.py")
        ]
        if not test_files:
            errors.append(f"No test files found in {tests_dir}")
            return ValidationResult(
                layer=2,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )

        metrics["test_files_found"] = len(test_files)

        # Check 4: Run pytest with coverage
        try:
            result = subprocess.run(
                [
                    "pytest",
                    str(tests_dir),
                    "--cov=src/",
                    "--cov-fail-under=0",  # Don't fail on coverage, we'll check manually
                    "-v",
                    "--tb=short",
                ],
                cwd=str(skill_dir),
                capture_output=True,
                text=True,
                timeout=120,  # 2-minute timeout
            )
        except subprocess.TimeoutExpired:
            errors.append("pytest execution timed out (>120 seconds)")
            return ValidationResult(
                layer=2,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )
        except FileNotFoundError:
            errors.append("pytest not found in PATH (is pytest installed?)")
            return ValidationResult(
                layer=2,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )
        except Exception as e:
            errors.append(f"Failed to run pytest: {e}")
            return ValidationResult(
                layer=2,
                passed=False,
                errors=errors,
                warnings=warnings,
                metrics=metrics,
            )

        # Check 5: Parse pytest output for test results
        stdout = result.stdout
        stderr = result.stderr

        # Look for coverage percentage in output
        coverage_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", stdout)
        if coverage_match:
            coverage_pct = float(coverage_match.group(1))
            metrics["coverage_percent"] = coverage_pct

            if coverage_pct < self.min_coverage:
                errors.append(
                    f"Code coverage {coverage_pct}% is below minimum {self.min_coverage}%"
                )
        else:
            # If coverage not found, try to extract from summary
            if "FAILED" in stdout or result.returncode != 0:
                # Tests may have failed, parse the failure
                if "FAILED" in stdout:
                    failed_match = re.findall(r"FAILED.*", stdout)
                    if failed_match:
                        errors.append(f"Test failures: {failed_match[0]}")
                    else:
                        errors.append("Tests failed (see pytest output)")
                else:
                    warnings.append("Could not parse pytest coverage output")

        # Check 6: Parse test count and pass/fail status
        passed_match = re.search(r"(\d+) passed", stdout)
        failed_match = re.search(r"(\d+) failed", stdout)

        if passed_match:
            passed_count = int(passed_match.group(1))
            metrics["tests_passed"] = passed_count

        if failed_match:
            failed_count = int(failed_match.group(1))
            metrics["tests_failed"] = failed_count
            errors.append(f"{failed_count} test(s) failed")

        # If pytest returned non-zero and we haven't already recorded failures
        if result.returncode != 0 and not errors:
            errors.append(f"pytest exited with code {result.returncode}")

        # Store full pytest output for debugging
        if result.stderr:
            metrics["pytest_stderr_snippet"] = stderr[:200]

        return ValidationResult(
            layer=2,
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            metrics=metrics,
        )
