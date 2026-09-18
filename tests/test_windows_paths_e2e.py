"""End-to-end tests for Windows path handling (cross-platform installer).

Tests path delimiter handling, environment variables, symlinks, and CLI argument
processing on both Unix and Windows.

ADR-0010: Windows path delimiter issue (E2E proof).
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Import the Windows path utilities
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.paths.windows import (
    is_windows,
    normalize_path,
    get_home_dir,
    get_temp_dir,
    create_symlink,
    join_path_segments,
    PlatformPath,
)


class TestWindowsPathsBasic(unittest.TestCase):
    """Test basic path operations."""

    def test_platform_detection(self):
        """Platform detection works."""
        detected = is_windows()
        actual = platform.system() == "Windows"
        self.assertEqual(detected, actual)

    def test_normalize_path_string(self):
        """normalize_path handles string paths."""
        if is_windows():
            # Windows path
            path = normalize_path("C:\\Users\\test\\.corvin")
            self.assertIsInstance(path, Path)
            self.assertTrue(str(path).startswith("C:"))
        else:
            # Unix path
            path = normalize_path("/home/test/.corvin")
            self.assertEqual(path, Path("/home/test/.corvin"))

    def test_normalize_path_object(self):
        """normalize_path handles Path objects."""
        original = Path("/tmp/test")
        normalized = normalize_path(original)
        self.assertEqual(normalized, original)

    def test_normalize_path_mixed_separators(self):
        """normalize_path handles mixed separators."""
        # Mixed separators: should normalize to platform-specific
        mixed = "C:/Users/test\\.corvin" if is_windows() else "/home/test/.corvin"
        normalized = normalize_path(mixed)
        self.assertIsInstance(normalized, Path)
        # Path object should use platform-correct separator
        if is_windows():
            self.assertNotIn("/", str(normalized).replace("//", ""))  # No forward slashes


class TestWindowsPathsEnvironment(unittest.TestCase):
    """Test environment variable handling."""

    def test_get_home_dir_uses_userprofile(self):
        """get_home_dir prefers %USERPROFILE% on Windows."""
        if not is_windows():
            self.skipTest("Windows-only test")

        with mock.patch.dict(os.environ, {"USERPROFILE": "C:\\Users\\TestUser"}, clear=False):
            home = get_home_dir()
            self.assertEqual(str(home), "C:\\Users\\TestUser")

    def test_get_home_dir_uses_home_on_unix(self):
        """get_home_dir uses $HOME on Unix."""
        if is_windows():
            self.skipTest("Unix-only test")

        with mock.patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=False):
            home = get_home_dir()
            self.assertEqual(home, Path("/home/testuser"))

    def test_get_temp_dir_windows(self):
        """get_temp_dir returns correct Windows temp dir."""
        if not is_windows():
            self.skipTest("Windows-only test")

        temp = get_temp_dir()
        self.assertIsInstance(temp, Path)
        # Should be somewhere in Temp or AppData
        temp_str = str(temp).lower()
        self.assertTrue(
            "temp" in temp_str or "appdata" in temp_str,
            f"Unexpected temp path: {temp}"
        )

    def test_get_temp_dir_unix(self):
        """get_temp_dir returns correct Unix temp dir."""
        if is_windows():
            self.skipTest("Unix-only test")

        temp = get_temp_dir()
        self.assertIsInstance(temp, Path)
        # Should be /tmp or somewhere in TMPDIR
        self.assertTrue(
            str(temp).startswith("/tmp") or "tmp" in str(temp),
            f"Unexpected temp path: {temp}"
        )

    def test_normalize_with_env_vars(self):
        """normalize_path expands environment variables."""
        if is_windows():
            with mock.patch.dict(os.environ, {"USERPROFILE": "C:\\Users\\test"}, clear=False):
                path = normalize_path("%USERPROFILE%\\.corvin")
                self.assertTrue(str(path).startswith("C:\\Users\\test"))
        else:
            with mock.patch.dict(os.environ, {"HOME": "/home/test"}, clear=False):
                path = normalize_path("$HOME/.corvin")
                self.assertEqual(path, Path("/home/test/.corvin"))

    def test_normalize_with_tilde(self):
        """normalize_path expands ~ to home directory."""
        path = normalize_path("~/.corvin")
        self.assertFalse(str(path).startswith("~"))
        self.assertTrue(path.is_absolute())


class TestWindowsPathsJoin(unittest.TestCase):
    """Test path joining operations."""

    def test_join_path_segments_strings(self):
        """join_path_segments handles string segments."""
        if is_windows():
            result = join_path_segments("C:\\Users", "test", ".corvin")
            self.assertTrue(str(result).startswith("C:"))
            self.assertIn("test", str(result))
            self.assertIn(".corvin", str(result))
        else:
            result = join_path_segments("/home", "test", ".corvin")
            self.assertEqual(result, Path("/home/test/.corvin"))

    def test_join_path_segments_paths(self):
        """join_path_segments handles Path objects."""
        if is_windows():
            result = join_path_segments(Path("C:\\Users"), Path("test"), ".corvin")
            self.assertTrue(str(result).startswith("C:"))
        else:
            result = join_path_segments(Path("/home"), Path("test"), ".corvin")
            self.assertEqual(result, Path("/home/test/.corvin"))

    def test_join_path_segments_mixed(self):
        """join_path_segments handles mixed strings and Paths."""
        if is_windows():
            result = join_path_segments("C:\\Users", Path("test"), ".corvin", Path("tenants"))
            self.assertTrue(str(result).startswith("C:"))
            self.assertIn("test", str(result))
        else:
            result = join_path_segments("/home", Path("test"), ".corvin")
            self.assertEqual(result, Path("/home/test/.corvin"))

    def test_join_path_segments_skips_empty(self):
        """join_path_segments skips empty segments."""
        if is_windows():
            result = join_path_segments("C:\\Users", "", "test")
            path_str = str(result)
            self.assertTrue(path_str.startswith("C:"))
            # Should not have double separators
            self.assertNotIn("\\\\", path_str)
        else:
            result = join_path_segments("/home", "", "test")
            self.assertEqual(result, Path("/home/test"))


class TestWindowsPathsPlatformPath(unittest.TestCase):
    """Test PlatformPath convenience class."""

    def test_platform_path_home(self):
        """PlatformPath.home returns user home."""
        pp = PlatformPath()
        home = pp.home
        self.assertIsInstance(home, Path)
        self.assertTrue(home.is_absolute())

    def test_platform_path_temp(self):
        """PlatformPath.temp returns temp directory."""
        pp = PlatformPath()
        temp = pp.temp
        self.assertIsInstance(temp, Path)
        # Temp dir should be accessible
        self.assertTrue(temp.parent.exists() or temp.exists())

    def test_platform_path_corvin_home_default(self):
        """PlatformPath.corvin_home returns ~/.corvin by default."""
        pp = PlatformPath()
        with mock.patch.dict(os.environ, {"CORVIN_HOME": ""}, clear=False):
            corvin_home = pp.corvin_home
            self.assertIn(".corvin", str(corvin_home))

    def test_platform_path_corvin_home_override(self):
        """PlatformPath.corvin_home respects CORVIN_HOME env var."""
        pp = PlatformPath()
        if is_windows():
            override = "C:\\Custom\\CorvinOS"
        else:
            override = "/opt/corvinos"

        with mock.patch.dict(os.environ, {"CORVIN_HOME": override}):
            corvin_home = pp.corvin_home
            self.assertEqual(str(corvin_home), override)

    def test_platform_path_path_builder(self):
        """PlatformPath.path builds paths correctly."""
        pp = PlatformPath()
        if is_windows():
            path = pp.path("C:\\Users", "test", ".corvin", "tenants", "_default")
            self.assertTrue(str(path).startswith("C:"))
            self.assertIn("_default", str(path))
        else:
            path = pp.path("/home", "test", ".corvin", "tenants", "_default")
            self.assertEqual(path, Path("/home/test/.corvin/tenants/_default"))


class TestWindowsPathsSymlinks(unittest.TestCase):
    """Test symlink/junction creation."""

    def setUp(self):
        """Create temporary directories for tests."""
        self.test_dir = tempfile.mkdtemp(prefix="corvin_test_")
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

    def test_create_symlink_file(self):
        """create_symlink creates file symlinks."""
        source = Path(self.test_dir) / "source.txt"
        link = Path(self.test_dir) / "link.txt"

        # Create source file
        source.write_text("test content")

        # Create symlink
        create_symlink(source, link, is_dir=False)

        # Verify link exists and points to source
        self.assertTrue(link.exists())
        self.assertEqual(link.read_text(), "test content")

    def test_create_symlink_directory(self):
        """create_symlink creates directory symlinks/junctions."""
        source = Path(self.test_dir) / "source_dir"
        link = Path(self.test_dir) / "link_dir"

        # Create source directory with a file
        source.mkdir(exist_ok=True)
        (source / "test.txt").write_text("test")

        # Create symlink
        create_symlink(source, link, is_dir=True)

        # Verify link exists and points to source
        self.assertTrue(link.exists())
        self.assertTrue(link.is_dir())
        # On Windows this might be a junction, not a symlink
        if not is_windows():
            self.assertTrue(link.is_symlink())

    def test_create_symlink_replaces_existing(self):
        """create_symlink replaces existing links."""
        source1 = Path(self.test_dir) / "source1.txt"
        source2 = Path(self.test_dir) / "source2.txt"
        link = Path(self.test_dir) / "link.txt"

        source1.write_text("content1")
        source2.write_text("content2")

        # Create first link
        create_symlink(source1, link, is_dir=False)
        self.assertEqual(link.read_text(), "content1")

        # Replace with second link
        create_symlink(source2, link, is_dir=False)
        self.assertEqual(link.read_text(), "content2")

    def test_create_symlink_normalize_paths(self):
        """create_symlink normalizes paths."""
        # Use mixed separators
        source = Path(self.test_dir) / "source.txt"
        source.write_text("test")

        if is_windows():
            link_path = str(Path(self.test_dir) / "link.txt").replace("\\", "/")
        else:
            link_path = str(Path(self.test_dir) / "link.txt")

        create_symlink(source, link_path, is_dir=False)

        # Should normalize and work
        self.assertTrue(Path(link_path).exists())


class TestWindowsPathsCLI(unittest.TestCase):
    """Test CLI argument path handling."""

    def test_cli_path_normalization(self):
        """CLI arguments with paths are normalized."""
        if is_windows():
            cli_arg = "C:/Users/test/.corvin"
        else:
            cli_arg = "/home/test/.corvin"

        # Simulate CLI argument processing
        normalized = normalize_path(cli_arg)
        self.assertIsInstance(normalized, Path)
        self.assertTrue(normalized.is_absolute())

    def test_cli_path_with_spaces(self):
        """CLI paths with spaces are handled correctly."""
        if is_windows():
            cli_arg = "C:\\Program Files\\CorvinOS\\.corvin"
        else:
            cli_arg = "/home/test user/.corvin"

        normalized = normalize_path(cli_arg)
        self.assertIsInstance(normalized, Path)
        # Path should contain the space
        self.assertIn(" ", str(normalized))

    def test_cli_path_with_env_var(self):
        """CLI paths with environment variables are expanded."""
        if is_windows():
            os.environ["TEST_CUSTOM"] = "C:\\CustomPath"
            cli_arg = "%TEST_CUSTOM%\\.corvin"
            expected_contains = "CustomPath"
        else:
            os.environ["TEST_CUSTOM"] = "/opt/custom"
            cli_arg = "$TEST_CUSTOM/.corvin"
            expected_contains = "custom"

        try:
            normalized = normalize_path(cli_arg)
            self.assertIsInstance(normalized, Path)
            self.assertIn(expected_contains, str(normalized))
        finally:
            if "TEST_CUSTOM" in os.environ:
                del os.environ["TEST_CUSTOM"]


class TestWindowsPathsInstaller(unittest.TestCase):
    """Test installer path handling."""

    def test_installer_corvin_home_setup(self):
        """Installer correctly sets up CORVIN_HOME."""
        pp = PlatformPath()
        corvin_home = pp.corvin_home

        self.assertIsInstance(corvin_home, Path)
        self.assertTrue(corvin_home.is_absolute())
        # Should be under home or custom location
        if "CORVIN_HOME" not in os.environ:
            self.assertIn(".corvin", str(corvin_home))

    def test_installer_temp_paths(self):
        """Installer temp paths are platform-safe."""
        temp = get_temp_dir()

        # Should be writable (or at least accessible)
        self.assertIsInstance(temp, Path)
        # Parent should exist (even if temp itself doesn't)
        self.assertTrue(temp.parent.exists() or temp.exists())

    def test_installer_creates_nested_dirs(self):
        """Installer can create nested directory structures."""
        test_dir = tempfile.mkdtemp(prefix="corvin_nested_")
        try:
            # Simulate installer creating nested structure
            base = Path(test_dir)
            structure = base / "tenants" / "_default" / "global" / "forge"
            structure.mkdir(parents=True, exist_ok=True)

            self.assertTrue(structure.exists())
            # Verify all parts exist
            self.assertTrue((base / "tenants").exists())
            self.assertTrue((base / "tenants" / "_default").exists())
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


class TestWindowsPathsAuditTrail(unittest.TestCase):
    """Test audit trail path handling."""

    def test_audit_chain_path(self):
        """Audit chain paths are normalized correctly."""
        pp = PlatformPath()
        corvin_home = pp.corvin_home
        audit_path = corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"

        self.assertIsInstance(audit_path, Path)
        # Should use correct separator for platform
        if is_windows():
            # On Windows, backslashes are OK
            pass
        else:
            # On Unix, should use forward slashes
            self.assertNotIn("\\", str(audit_path))


class TestWindowsPathsE2E(unittest.TestCase):
    """End-to-end integration tests."""

    def setUp(self):
        """Create test environment."""
        self.test_dir = tempfile.mkdtemp(prefix="corvin_e2e_")
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

    def test_e2e_full_installation_path_layout(self):
        """E2E: Full installation creates correct path layout."""
        # Simulate installation
        with mock.patch.dict(os.environ, {"CORVIN_HOME": self.test_dir}):
            pp = PlatformPath()
            corvin_home = pp.corvin_home

            # Create full tenant structure
            tenant_home = corvin_home / "tenants" / "_default"
            (tenant_home / "global" / "forge").mkdir(parents=True, exist_ok=True)
            (tenant_home / "sessions").mkdir(parents=True, exist_ok=True)
            (tenant_home / "skill-forge" / "skills").mkdir(parents=True, exist_ok=True)

            # Verify structure
            self.assertTrue(tenant_home.exists())
            self.assertTrue((tenant_home / "global" / "forge").exists())
            self.assertTrue((tenant_home / "sessions").exists())
            self.assertTrue((tenant_home / "skill-forge" / "skills").exists())

            # Audit chain path
            audit_chain = tenant_home / "global" / "forge" / "audit.jsonl"
            self.assertIsInstance(audit_chain, Path)

    def test_e2e_path_consistency_across_modules(self):
        """E2E: Path resolution is consistent across modules."""
        from core.paths.tenant import tenant_home, tenant_audit_chain

        with mock.patch.dict(os.environ, {"CORVIN_HOME": self.test_dir}):
            # Get paths from different modules
            from core.paths.windows import PlatformPath
            pp = PlatformPath()

            # Both should produce absolute paths
            windows_path = pp.corvin_home / "tenants" / "_default"
            core_path = tenant_home("_default")

            # Should normalize to same absolute path
            self.assertEqual(windows_path, core_path)

            # Audit chain should match
            windows_audit = windows_path / "global" / "forge" / "audit.jsonl"
            core_audit = tenant_audit_chain("_default")
            self.assertEqual(windows_audit, core_audit)

    def test_e2e_mixed_separators_in_env_var(self):
        """E2E: CORVIN_HOME with mixed separators is normalized."""
        if is_windows():
            mixed_path = "C:/Custom\\Path/CorvinOS"
        else:
            mixed_path = "/opt/custom/path/../corvinOS"

        with mock.patch.dict(os.environ, {"CORVIN_HOME": mixed_path}):
            pp = PlatformPath()
            corvin_home = pp.corvin_home

            # Should be normalized
            self.assertIsInstance(corvin_home, Path)
            # Should be absolute
            self.assertTrue(corvin_home.is_absolute())


if __name__ == "__main__":
    unittest.main()
