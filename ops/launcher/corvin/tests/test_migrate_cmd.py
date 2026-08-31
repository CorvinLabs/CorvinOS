"""Unit tests for Phase D migration CLI commands — ADR-0007.

Tests for helper functions and CLI dispatch. Full migration E2E tests
are in operator/forge/tests/test_tenant_migrate.py.

Test categories:
  1. Helper functions: checksum, file counting, path validation
  2. CLI dispatch: commands registered and callable
  3. Data reporting: accurate file counts and sizes (mocked migrations)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[3]
LAUNCHER_DIR = Path(__file__).resolve().parents[1]

# Add paths so imports work
sys.path.insert(0, str(LAUNCHER_DIR))

from migrate_cmd import (  # noqa: E402
    _compute_tree_checksum,
    _count_files_and_size,
    cmd_verify_isolation,
    cmd_tenant_data_report,
    dispatch,
)


class _SandboxBase(unittest.TestCase):
    """Shared sandbox setup for migration tests."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="corvin-migrate-test-")
        self.home = Path(self._tmp) / "corvin"
        self.home.mkdir(parents=True, exist_ok=True)
        self._saved_env = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(self.home)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        if self._saved_env is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._saved_env

    def _create_legacy_layout(self):
        """Create a legacy ~/.corvin/global/ + sessions/ structure."""
        # Create some data in legacy global
        (self.home / "global" / "forge").mkdir(parents=True, exist_ok=True)
        (self.home / "global" / "forge" / "audit.jsonl").write_text(
            '{"event":"test","data":{}}\n', encoding="utf-8"
        )
        (self.home / "global" / "forge" / "tools.json").write_text(
            '{"tools":[]}', encoding="utf-8"
        )
        (self.home / "global" / "roles").mkdir(parents=True, exist_ok=True)
        (self.home / "global" / "roles" / "admin.json").write_text(
            '{"admin":true}', encoding="utf-8"
        )

        # Create some data in legacy sessions
        (self.home / "sessions" / "discord:chatA").mkdir(parents=True, exist_ok=True)
        (self.home / "sessions" / "discord:chatA" / "session.json").write_text(
            '{"started":true}', encoding="utf-8"
        )
        (self.home / "sessions" / "discord:chatA" / "artifacts.jsonl").write_text(
            '{"id":"art1","type":"text"}\n', encoding="utf-8"
        )

        # Create some data in forge
        (self.home / "forge" / "bundles").mkdir(parents=True, exist_ok=True)
        (self.home / "forge" / "bundles" / "math.yaml").write_text(
            "name: math\nversion: 1\n", encoding="utf-8"
        )

        # Create skill-forge
        (self.home / "skill-forge" / "skills").mkdir(parents=True, exist_ok=True)
        (self.home / "skill-forge" / "skills" / "skill1.yaml").write_text(
            "skill: skill1\n", encoding="utf-8"
        )


class TestHelperFunctions(unittest.TestCase):
    """Test helper functions: checksum and file counting."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="corvin-helper-test-")
        self.test_dir = Path(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_count_files_and_size(self):
        """_count_files_and_size should return accurate counts."""
        # Create test structure
        (self.test_dir / "dir1").mkdir()
        (self.test_dir / "dir1" / "file1.txt").write_text("hello", encoding="utf-8")
        (self.test_dir / "dir1" / "file2.txt").write_text("world!", encoding="utf-8")
        (self.test_dir / "dir2").mkdir()
        (self.test_dir / "dir2" / "file3.txt").write_text("test", encoding="utf-8")

        files, size = _count_files_and_size(self.test_dir)

        self.assertEqual(files, 3)
        self.assertGreater(size, 0)
        # "hello" (5) + "world!" (6) + "test" (4) = 15 bytes minimum
        self.assertGreaterEqual(size, 15)

    def test_count_files_empty_dir(self):
        """_count_files_and_size should return 0 for empty dir."""
        empty_dir = self.test_dir / "empty"
        empty_dir.mkdir()

        files, size = _count_files_and_size(empty_dir)

        self.assertEqual(files, 0)
        self.assertEqual(size, 0)

    def test_compute_tree_checksum(self):
        """_compute_tree_checksum should be deterministic."""
        # Create test files
        (self.test_dir / "file1.txt").write_text("content1", encoding="utf-8")
        (self.test_dir / "file2.txt").write_text("content2", encoding="utf-8")

        # Compute twice
        checksum1 = _compute_tree_checksum(self.test_dir)
        checksum2 = _compute_tree_checksum(self.test_dir)

        # Should be identical
        self.assertEqual(checksum1, checksum2)
        self.assertEqual(len(checksum1), 64)  # SHA256 is 64 hex chars

    def test_checksum_changes_with_content(self):
        """_compute_tree_checksum should change when content changes."""
        (self.test_dir / "file1.txt").write_text("content1", encoding="utf-8")
        checksum1 = _compute_tree_checksum(self.test_dir)

        # Modify content
        (self.test_dir / "file1.txt").write_text("different", encoding="utf-8")
        checksum2 = _compute_tree_checksum(self.test_dir)

        # Should be different
        self.assertNotEqual(checksum1, checksum2)


class TestDataReporting(_SandboxBase):
    """Test data reporting: accurate file counts and sizes."""

    def test_data_report_no_tenants(self):
        """Data report on fresh install should succeed."""
        class Args:
            pass

        result = cmd_tenant_data_report(Args())
        self.assertEqual(result, 0)

    def test_data_report_with_tenant_structure(self):
        """Data report should show accurate file counts and sizes."""
        # Create a tenant structure manually
        (self.home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
        (self.home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl").write_text(
            '{"event":"test"}\n', encoding="utf-8"
        )
        (self.home / "tenants" / "_default" / "global" / "forge" / "tools.json").write_text(
            '{"tools":[]}\n', encoding="utf-8"
        )
        (self.home / "tenants" / "_default" / "sessions").mkdir(parents=True)
        (self.home / "tenants" / "_default" / "sessions" / "test.json").write_text(
            '{"started":true}\n', encoding="utf-8"
        )

        class Args:
            pass

        result = cmd_tenant_data_report(Args())
        self.assertEqual(result, 0)

        # Verify JSON report was written
        json_report = self.home / ".tenant-data-report.json"
        self.assertTrue(json_report.exists(), "JSON report should be written")

        # Verify JSON is valid and has expected structure
        report = json.loads(json_report.read_text())
        self.assertIn("total_tenants", report)
        self.assertIn("total_files", report)
        self.assertIn("tenants", report)

        # Should have _default tenant with files
        self.assertIn("_default", report["tenants"])
        self.assertGreater(report["tenants"]["_default"]["files"], 0)

        # Total should match sum of tenant files
        self.assertEqual(
            report["total_files"],
            sum(t["files"] for t in report["tenants"].values())
        )


class TestCLIDispatch(unittest.TestCase):
    """Test CLI dispatch logic."""

    def test_dispatch_verify_isolation(self):
        """dispatch() should route verify-isolation command."""
        class Args:
            migrate_cmd = "verify-isolation"
            tenant_id = None

        # Just verify the dispatch doesn't crash
        # The actual command logic requires CORVIN_HOME to be set
        result = dispatch(Args())
        # Result should be an int (exit code)
        self.assertIsInstance(result, int)

    def test_dispatch_tenant_data_report(self):
        """dispatch() should route tenant-data-report command."""
        class Args:
            migrate_cmd = "tenant-data-report"

        result = dispatch(Args())
        self.assertIsInstance(result, int)

    def test_dispatch_unknown_command(self):
        """dispatch() should handle unknown commands."""
        class Args:
            migrate_cmd = "unknown"

        result = dispatch(Args())
        self.assertEqual(result, 1)  # Should return error code


if __name__ == "__main__":
    unittest.main()
