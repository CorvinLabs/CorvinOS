"""ReachabilityCheck: Is there a real call site outside tests?"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CheckResult:
    """Immutable result of a single check."""
    passed: bool
    evidence: str
    check_name: str = "reachability"


class ReachabilityCheck:
    """Grep-based reachability check (fail-closed)."""

    def run(
        self,
        symbol_name: str,
        cwd: Path,
        timeout_s: int = 5,
        exclude_tests: bool = True,
    ) -> CheckResult:
        """
        Search for real call site of symbol.

        Returns CheckResult with passed=(symbol found outside tests) + evidence.
        Fail-closed: timeout, error, or no matches → passed=False.
        """
        if not symbol_name or not symbol_name.strip():
            return CheckResult(
                passed=False,
                evidence="Symbol name is empty",
                check_name="reachability"
            )

        # Grep for symbol name (exclude test directories by default)
        try:
            cmd = ["grep", "-r", symbol_name]
            if exclude_tests:
                cmd.extend(["--exclude-dir=test", "--exclude-dir=tests", "--exclude-dir=__pycache__"])
            cmd.append(str(cwd))

            result = subprocess.run(
                cmd,
                timeout=timeout_s,
                cwd=cwd,
                capture_output=True,
                text=True,
                shell=False,
            )

            if result.returncode == 0 and result.stdout:
                # Found matches
                lines = result.stdout.strip().split("\n")[:3]
                evidence = "\n".join(lines)
                return CheckResult(
                    passed=True,
                    evidence=evidence,
                    check_name="reachability"
                )
            else:
                # No matches
                return CheckResult(
                    passed=False,
                    evidence=f"Symbol '{symbol_name}' not found in call sites",
                    check_name="reachability"
                )

        except subprocess.TimeoutExpired:
            return CheckResult(
                passed=False,
                evidence=f"Grep timeout (>{timeout_s}s) — symbol search took too long",
                check_name="reachability"
            )

        except Exception as e:
            # Fail-closed: any error → fail
            return CheckResult(
                passed=False,
                evidence=f"Grep error: {type(e).__name__}: {str(e)[:50]}",
                check_name="reachability"
            )


# ============================================================================
# UNIT TESTS (can be run with pytest)
# ============================================================================

import pytest
import tempfile
from unittest.mock import patch


class TestReachabilityCheck:

    def test_symbol_found_single_match(self):
        """Symbol found in code (not in tests)."""
        check = ReachabilityCheck()
        # We'll mock the subprocess to avoid actual filesystem access
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "src/router.py:42: delete_panel(task_id)\n"

            result = check.run("delete_panel", Path("/repo"))
            assert result.passed == True
            assert "delete_panel" in result.evidence

    def test_symbol_not_found(self):
        """Symbol not found in code."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""

            result = check.run("xyz_nonexistent_12345", Path("/repo"))
            assert result.passed == False
            assert "not found" in result.evidence.lower()

    def test_grep_timeout(self):
        """Grep hangs (symlink loop, etc) → fail-closed."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("grep", 5)

            result = check.run("symbol", Path("/repo"), timeout_s=5)
            assert result.passed == False
            assert "timeout" in result.evidence.lower()

    def test_grep_error_fail_closed(self):
        """Any error (permission, etc) → fail-closed."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Permission denied")

            result = check.run("symbol", Path("/repo"))
            assert result.passed == False
            assert "error" in result.evidence.lower()

    def test_empty_symbol_name(self):
        """Empty symbol → immediate fail."""
        check = ReachabilityCheck()
        result = check.run("", Path("/repo"))
        assert result.passed == False
        assert "empty" in result.evidence.lower()

    def test_multiple_matches_shows_first_three(self):
        """Multiple matches → show first 3."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = (
                "src/a.py:1: foo()\n"
                "src/b.py:2: foo()\n"
                "src/c.py:3: foo()\n"
                "src/d.py:4: foo()\n"
            )

            result = check.run("foo", Path("/repo"))
            assert result.passed == True
            lines = result.evidence.split("\n")
            assert len(lines) == 3  # Only first 3

    def test_exclude_tests_default(self):
        """By default, exclude test directories."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "src/code.py:1: symbol\n"

            result = check.run("symbol", Path("/repo"))

            # Verify grep was called with --exclude-dir=test
            call_args = mock_run.call_args
            assert "--exclude-dir=test" in call_args[0][0]
            assert result.passed == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
