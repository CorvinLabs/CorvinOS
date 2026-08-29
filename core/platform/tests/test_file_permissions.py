"""Tests for platform-specific file permission setup.

Tests verify:
  - setup_file_permissions() correctly sets mode on Unix
  - setup_file_permissions() handles Windows gracefully
  - audit file permissions are restricted
  - socket directory permissions are restricted
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from core.platform.file_permissions import (
    _IS_WINDOWS,
    _setup_unix_permissions,
    setup_audit_file_permissions,
    setup_corvin_home_permissions,
    setup_file_permissions,
    setup_socket_directory_permissions,
)


class TestFilePermissionsUnix:
    """Unix-specific file permission tests."""

    def test_setup_unix_permissions_sets_mode(self):
        """Verify os.chmod() is called with correct mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test")

            # Set to 0o644 (world-readable)
            os.chmod(test_file, 0o644)
            assert test_file.stat().st_mode & 0o777 == 0o644

            # Setup should restrict to 0o600
            result = _setup_unix_permissions(test_file, 0o600)
            assert result is True
            assert test_file.stat().st_mode & 0o777 == 0o600

    def test_setup_unix_permissions_nonexistent_file(self):
        """Verify _setup_unix_permissions returns False for nonexistent file."""
        nonexistent = Path("/tmp/nonexistent_file_xyz123")
        result = _setup_unix_permissions(nonexistent)
        assert result is False

    @staticmethod
    def test_setup_audit_file_permissions():
        """Verify audit file is set to 0o600."""
        if _IS_WINDOWS:
            # Windows test is in TestFilePermissionsWindows
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            audit_file.write_text('{"event": "test"}\n')

            # Set to world-readable first
            os.chmod(audit_file, 0o644)

            # Setup should restrict
            result = setup_audit_file_permissions(audit_file)
            assert result is True
            assert audit_file.stat().st_mode & 0o777 == 0o600

    @staticmethod
    def test_setup_socket_directory_permissions():
        """Verify socket directory is set to 0o700."""
        if _IS_WINDOWS:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            socket_dir = Path(tmpdir) / "run"
            socket_dir.mkdir()

            # Set to world-readable first
            os.chmod(socket_dir, 0o755)

            # Setup should restrict
            result = setup_socket_directory_permissions(socket_dir)
            assert result is True
            assert socket_dir.stat().st_mode & 0o777 == 0o700


class TestFilePermissionsWindows:
    """Windows-specific file permission tests (degraded mode)."""

    def test_setup_file_permissions_windows_degrades_gracefully(self):
        """Verify setup_file_permissions() doesn't crash on Windows without win32security."""
        # This test runs on all platforms; on Unix it verifies the Unix path works,
        # on Windows it verifies degradation.
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test")

            # Should not raise an exception
            result = setup_file_permissions(test_file, mode=0o600, require_success=False)
            # On Unix, should succeed; on Windows without win32security, should degrade gracefully
            if _IS_WINDOWS:
                # Windows: best-effort, may return False if win32security unavailable
                assert isinstance(result, bool)
            else:
                # Unix: should succeed
                assert result is True

    def test_setup_file_permissions_nonexistent_raises_if_required(self):
        """Verify require_success=True raises on nonexistent file."""
        nonexistent = Path("/tmp/nonexistent_xyz123")
        try:
            setup_file_permissions(nonexistent, require_success=True)
            assert False, "Should have raised RuntimeError"
        except RuntimeError:
            pass  # Expected

    def test_setup_file_permissions_nonexistent_returns_false_if_optional(self):
        """Verify require_success=False returns False on nonexistent file."""
        nonexistent = Path("/tmp/nonexistent_xyz123")
        result = setup_file_permissions(nonexistent, require_success=False)
        assert result is False


class TestCorvinHomePermissions:
    """Test permission setup for entire corvin_home directory tree."""

    @staticmethod
    def test_setup_corvin_home_permissions_creates_results_dict():
        """Verify setup_corvin_home_permissions() returns dict of results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corvin_home = Path(tmpdir)

            # Create directory structure
            (corvin_home / "tenants" / "_default").mkdir(parents=True)
            (corvin_home / "run").mkdir(parents=True)
            (corvin_home / "audit.jsonl").touch()
            (corvin_home / "tenants" / "_default" / "audit.jsonl").touch()

            results = setup_corvin_home_permissions(corvin_home)

            # Should have results for files that exist
            assert isinstance(results, dict)
            assert len(results) > 0

            # Verify audit file path is in results
            audit_path = str(corvin_home / "audit.jsonl")
            assert audit_path in results

    @staticmethod
    def test_setup_corvin_home_permissions_ignores_missing_dirs():
        """Verify setup_corvin_home_permissions() ignores missing directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corvin_home = Path(tmpdir)
            # Don't create any subdirectories

            results = setup_corvin_home_permissions(corvin_home)
            # Should return empty dict (no files to setup)
            assert isinstance(results, dict)
