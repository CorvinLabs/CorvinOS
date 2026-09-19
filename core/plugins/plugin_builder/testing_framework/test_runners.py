"""Test runners and validation for plugin scaffolds (ADR-0262).

Provides:
- PluginTestRunner: orchestrates test discovery and execution
- validate_plugin_structure: verifies generated plugin structure
- run_plugin_tests: CLI entry point for running plugin tests

Generated scaffolds include a test runner that calls these functions.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TestResult:
    """Result of a plugin test run.

    Attributes:
        passed: Number of passed tests
        failed: Number of failed tests
        skipped: Number of skipped tests
        errors: List of error messages
        exit_code: Process exit code (0 = success)
    """

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] | None = None
    exit_code: int = 0

    def __post_init__(self) -> None:
        """Initialize errors list if None."""
        if self.errors is None:
            self.errors = []

    def is_success(self) -> bool:
        """Check if all tests passed."""
        return self.exit_code == 0 and self.failed == 0


@dataclass
class PluginStructure:
    """Validated plugin structure information.

    Attributes:
        plugin_id: Plugin identifier
        plugin_type: Type of plugin (provider, skill, mcp_server, etc.)
        has_plugin_file: Whether plugin.py or equivalent exists
        has_tests: Whether test files exist
        has_build_config: Whether setup.py or pyproject.toml exists
        has_lifecycle_hooks: Whether on_load/on_unload are defined
        issues: List of validation issues found
    """

    plugin_id: str
    plugin_type: str
    has_plugin_file: bool = False
    has_tests: bool = False
    has_build_config: bool = False
    has_lifecycle_hooks: bool = False
    issues: list[str] | None = None

    def __post_init__(self) -> None:
        """Initialize issues list if None."""
        if self.issues is None:
            self.issues = []

    def is_valid(self) -> bool:
        """Check if the plugin structure is valid."""
        # Minimum requirements: plugin file exists, has build config
        required = [self.has_plugin_file, self.has_build_config]
        return all(required) and len(self.issues) == 0


class PluginTestRunner:
    """Orchestrates test discovery and execution for a plugin scaffold.

    Usage:
        runner = PluginTestRunner(plugin_dir)
        structure = runner.validate_structure()
        result = runner.run_tests()
    """

    def __init__(self, plugin_dir: Path | str):
        """Initialize the test runner.

        Args:
            plugin_dir: Root directory of the plugin scaffold
        """
        self.plugin_dir = Path(plugin_dir).resolve()
        self.test_dir = self.plugin_dir / "tests"

    def validate_structure(self) -> PluginStructure:
        """Validate that the plugin scaffold has the required structure.

        Returns:
            PluginStructure: validation result with any issues found
        """
        issues: list[str] = []
        plugin_id = self.plugin_dir.name

        # Check for plugin implementation file
        has_plugin_file = False
        for pattern in ["plugin.py", "plugin.md", "*_plugin.py"]:
            if any(self.plugin_dir.glob(pattern)):
                has_plugin_file = True
                break
        if not has_plugin_file:
            issues.append("No plugin.py, plugin.md, or *_plugin.py found")

        # Check for tests
        has_tests = self.test_dir.is_dir() and any(
            self.test_dir.glob("test_*.py")
        )
        if not has_tests:
            issues.append("No tests/ directory with test_*.py files found")

        # Check for build configuration
        has_build_config = any(
            self.plugin_dir.glob(pattern)
            for pattern in ["setup.py", "pyproject.toml", "setup.cfg"]
        )
        if not has_build_config:
            issues.append("No setup.py or pyproject.toml found")

        # Check for lifecycle hooks (basic check)
        has_lifecycle_hooks = False
        plugin_file = None
        for pattern in ["plugin.py", "*_plugin.py"]:
            candidates = list(self.plugin_dir.glob(pattern))
            if candidates:
                plugin_file = candidates[0]
                break

        if plugin_file and plugin_file.is_file():
            content = plugin_file.read_text(encoding="utf-8")
            has_lifecycle_hooks = (
                "def on_load" in content or "def on_unload" in content
            )

        structure_type = "provider"  # default, could be inferred from metadata
        return PluginStructure(
            plugin_id=plugin_id,
            plugin_type=structure_type,
            has_plugin_file=has_plugin_file,
            has_tests=has_tests,
            has_build_config=has_build_config,
            has_lifecycle_hooks=has_lifecycle_hooks,
            issues=issues,
        )

    def run_tests(
        self, verbose: bool = False, pytest_args: list[str] | None = None
    ) -> TestResult:
        """Run the plugin's test suite using pytest.

        Args:
            verbose: If True, run pytest in verbose mode
            pytest_args: Additional arguments to pass to pytest

        Returns:
            TestResult: summary of test execution
        """
        if not self.test_dir.is_dir():
            return TestResult(
                exit_code=1,
                errors=["No tests/ directory found"],
            )

        cmd = [sys.executable, "-m", "pytest", str(self.test_dir)]
        if verbose:
            cmd.append("-v")
        if pytest_args:
            cmd.extend(pytest_args)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.plugin_dir,
                capture_output=True,
                text=True,
                timeout=300,
            )
            exit_code = result.returncode
            # Parse output for test counts (simplified)
            stdout = result.stdout + result.stderr
            passed = stdout.count(" passed")
            failed = stdout.count(" failed")
            skipped = stdout.count(" skipped")

            return TestResult(
                passed=passed,
                failed=failed,
                skipped=skipped,
                exit_code=exit_code,
                errors=[] if exit_code == 0 else [stdout],
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                exit_code=1,
                errors=["Test execution timed out (>300s)"],
            )
        except Exception as e:
            return TestResult(
                exit_code=1,
                errors=[f"Test execution failed: {e}"],
            )


def validate_plugin_structure(plugin_dir: Path | str) -> PluginStructure:
    """Validate a plugin scaffold structure.

    Args:
        plugin_dir: Root directory of the plugin scaffold

    Returns:
        PluginStructure: validation result with any issues found
    """
    runner = PluginTestRunner(plugin_dir)
    return runner.validate_structure()


def run_plugin_tests(
    plugin_dir: Path | str,
    verbose: bool = False,
    stop_on_failure: bool = False,
) -> TestResult:
    """Run a plugin's test suite.

    Args:
        plugin_dir: Root directory of the plugin scaffold
        verbose: If True, run tests in verbose mode
        stop_on_failure: If True, stop on first failure

    Returns:
        TestResult: summary of test execution
    """
    runner = PluginTestRunner(plugin_dir)

    # Validate structure first
    structure = runner.validate_structure()
    if not structure.is_valid():
        return TestResult(
            exit_code=1,
            errors=[f"Invalid plugin structure: {'; '.join(structure.issues)}"],
        )

    # Run tests
    pytest_args = []
    if stop_on_failure:
        pytest_args.append("-x")

    return runner.run_tests(verbose=verbose, pytest_args=pytest_args)
