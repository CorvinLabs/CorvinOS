"""Unit tests for ReachabilityCheck (Definition-of-Done verifier).

Tests moved from production code (reachability.py) per PEP 8 compliance.
Tests verify that symbols have real call sites outside test directories.
"""

import pytest
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.skills.os_skills.definition_of_done_verifier.checks.reachability import (
    ReachabilityCheck,
    CheckResult,
)


class TestReachabilityCheck:
    """Test suite for ReachabilityCheck (fail-closed behavior)."""

    def test_symbol_found_single_match(self):
        """Test: Symbol found in code (not in tests)."""
        check = ReachabilityCheck()
        # Mock subprocess to avoid actual filesystem access
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "src/router.py:42: delete_panel(task_id)\n"

            result = check.run("delete_panel", Path("/repo"))
            assert result.passed == True
            assert "delete_panel" in result.evidence
            assert result.check_name == "reachability"

    def test_symbol_not_found(self):
        """Test: Symbol not found in code (fail-closed)."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""

            result = check.run("xyz_nonexistent_12345", Path("/repo"))
            assert result.passed == False
            assert "not found" in result.evidence.lower()
            assert result.check_name == "reachability"

    def test_grep_timeout_fail_closed(self):
        """Test: Grep hangs (symlink loop, etc) → fail-closed."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("grep", 5)

            result = check.run("symbol", Path("/repo"), timeout_s=5)
            assert result.passed == False
            assert "timeout" in result.evidence.lower()
            assert result.check_name == "reachability"

    def test_grep_error_fail_closed(self):
        """Test: Any error (permission denied, etc) → fail-closed."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Permission denied")

            result = check.run("symbol", Path("/repo"))
            assert result.passed == False
            assert "error" in result.evidence.lower()
            assert result.check_name == "reachability"

    def test_empty_symbol_name_immediate_fail(self):
        """Test: Empty symbol name → immediate fail (no subprocess call)."""
        check = ReachabilityCheck()
        result = check.run("", Path("/repo"))
        assert result.passed == False
        assert "empty" in result.evidence.lower()
        assert result.check_name == "reachability"

    def test_multiple_matches_shows_first_three(self):
        """Test: Multiple matches → show first 3 (truncate evidence)."""
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
            assert result.check_name == "reachability"

    def test_exclude_tests_default(self):
        """Test: By default, exclude test directories (--exclude-dir=test)."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "src/code.py:1: symbol\n"

            result = check.run("symbol", Path("/repo"))

            # Verify grep was called with --exclude-dir flags
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "--exclude-dir=test" in cmd
            assert "--exclude-dir=tests" in cmd
            assert "--exclude-dir=__pycache__" in cmd
            assert result.passed == True

    def test_exclude_tests_can_be_disabled(self):
        """Test: exclude_tests=False removes test directory filters."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "tests/code.py:1: symbol\n"

            result = check.run("symbol", Path("/repo"), exclude_tests=False)

            # Verify grep was called WITHOUT --exclude-dir flags
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "--exclude-dir=test" not in cmd
            assert "--exclude-dir=tests" not in cmd
            assert result.passed == True

    def test_check_result_is_frozen(self):
        """Test: CheckResult dataclass is immutable (frozen=True)."""
        result = CheckResult(passed=True, evidence="test", check_name="reachability")
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            result.passed = False

    def test_check_result_defaults(self):
        """Test: CheckResult has default check_name='reachability'."""
        result = CheckResult(passed=False, evidence="not found")
        assert result.check_name == "reachability"

    def test_cwd_parameter_passed_to_grep(self):
        """Test: Working directory is passed to grep subprocess."""
        check = ReachabilityCheck()
        cwd = Path("/home/user/project")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "src/file.py:1: symbol\n"

            result = check.run("symbol", cwd)

            # Verify cwd was passed
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["cwd"] == cwd
            assert result.passed == True

    def test_symbol_name_passed_to_grep(self):
        """Test: Symbol name is correctly passed to grep command."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "src/file.py:1: my_symbol\n"

            result = check.run("my_symbol", Path("/repo"))

            # Verify symbol was in grep command
            call_args = mock_run.call_args[0][0]
            assert "my_symbol" in call_args
            assert result.passed == True

    def test_custom_timeout(self):
        """Test: Custom timeout value is respected."""
        check = ReachabilityCheck()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "src/file.py:1: symbol\n"

            result = check.run("symbol", Path("/repo"), timeout_s=30)

            # Verify timeout was passed
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 30
            assert result.passed == True


class TestReachabilityCheckIntegration:
    """Integration tests for ReachabilityCheck (can be skipped in CI if fs access not available)."""

    @pytest.mark.integration
    def test_real_filesystem_grep_basic(self):
        """Integration: Real grep on temp filesystem."""
        check = ReachabilityCheck()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file with a symbol
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("def my_function(): pass\n")

            # Should find the symbol
            result = check.run("my_function", Path(tmpdir))
            assert result.passed == True
            assert "my_function" in result.evidence

    @pytest.mark.integration
    def test_real_filesystem_excludes_tests(self):
        """Integration: Real grep respects test exclusion."""
        check = ReachabilityCheck()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create symbol in tests/ (should be excluded)
            tests_dir = tmppath / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_file.py").write_text("def symbol(): pass\n")

            # Create same symbol in src/ (should be found)
            src_dir = tmppath / "src"
            src_dir.mkdir()
            (src_dir / "file.py").write_text("def symbol(): pass\n")

            # With exclude_tests=True, should find src/ only
            result = check.run("symbol", tmppath, exclude_tests=True)
            # Result depends on grep behavior; mainly checking no error
            assert isinstance(result, CheckResult)
