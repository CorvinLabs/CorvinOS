"""Tests for plugin build system (ADR-0262).

Tests:
- setup.py generation
- pyproject.toml generation
- Build metadata
- Package validation
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ..build_system import (
    PackageBuilder,
    PackageMetadata,
    generate_pyproject_toml,
    generate_setup_py,
    validate_package,
)


class TestPackageMetadata(unittest.TestCase):
    """Test PackageMetadata dataclass."""

    def test_metadata_creation(self) -> None:
        """Test creating package metadata."""
        metadata = PackageMetadata(
            name="test-plugin",
            version="1.0.0",
            description="Test plugin",
            author="Test Author",
        )

        self.assertEqual(metadata.name, "test-plugin")
        self.assertEqual(metadata.version, "1.0.0")
        self.assertIsNotNone(metadata.dependencies)
        self.assertIsNotNone(metadata.dev_dependencies)

    def test_metadata_defaults(self) -> None:
        """Test metadata default values."""
        metadata = PackageMetadata(name="test")

        self.assertEqual(metadata.version, "0.1.0")
        self.assertEqual(metadata.python_requires, ">=3.10")
        self.assertIn("corvin-plugins", metadata.dependencies)


class TestPackageBuilder(unittest.TestCase):
    """Test PackageBuilder."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.plugin_dir = Path(self.temp_dir.name)
        self.metadata = PackageMetadata(
            name="test-plugin",
            version="0.1.0",
            description="Test plugin",
        )

    def tearDown(self) -> None:
        """Clean up test resources."""
        self.temp_dir.cleanup()

    def test_builder_creation(self) -> None:
        """Test creating a package builder."""
        builder = PackageBuilder(self.plugin_dir, self.metadata)

        self.assertEqual(builder.metadata.name, "test-plugin")
        self.assertTrue(
            builder.dist_dir == self.plugin_dir / "dist",
        )

    def test_generate_setup_py(self) -> None:
        """Test setup.py generation."""
        builder = PackageBuilder(self.plugin_dir, self.metadata)
        setup_py = builder.generate_setup_py()

        self.assertIn("test-plugin", setup_py)
        self.assertIn("0.1.0", setup_py)
        self.assertIn("setup(", setup_py)
        self.assertIn("corvin-plugins", setup_py)

    def test_generate_pyproject_toml(self) -> None:
        """Test pyproject.toml generation."""
        builder = PackageBuilder(self.plugin_dir, self.metadata)
        pyproject = builder.generate_pyproject_toml()

        self.assertIn("test-plugin", pyproject)
        self.assertIn("0.1.0", pyproject)
        self.assertIn("[project]", pyproject)
        self.assertIn("corvin-plugins", pyproject)

    def test_write_build_files(self) -> None:
        """Test writing build files."""
        builder = PackageBuilder(self.plugin_dir, self.metadata)
        files = builder.write_build_files()

        self.assertEqual(len(files), 2)  # setup.py and pyproject.toml

        # Check files exist
        self.assertTrue((self.plugin_dir / "setup.py").exists())
        self.assertTrue((self.plugin_dir / "pyproject.toml").exists())

        # Check content
        setup_content = (self.plugin_dir / "setup.py").read_text()
        self.assertIn("test-plugin", setup_content)

        pyproject_content = (self.plugin_dir / "pyproject.toml").read_text()
        self.assertIn("test-plugin", pyproject_content)


class TestGenerateFunctions(unittest.TestCase):
    """Test module-level generate functions."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.metadata = PackageMetadata(name="test-pkg")

    def test_generate_setup_py(self) -> None:
        """Test generate_setup_py function."""
        setup_py = generate_setup_py(self.metadata)

        self.assertIn("test-pkg", setup_py)
        self.assertIn("setup(", setup_py)

    def test_generate_pyproject_toml(self) -> None:
        """Test generate_pyproject_toml function."""
        pyproject = generate_pyproject_toml(self.metadata)

        self.assertIn("test-pkg", pyproject)
        self.assertIn("[project]", pyproject)


class TestValidatePackage(unittest.TestCase):
    """Test package validation."""

    def test_validate_nonexistent_package(self) -> None:
        """Test validation of nonexistent package."""
        result = validate_package("/nonexistent/path/package.whl")

        self.assertFalse(result["valid"])
        self.assertTrue(any("not found" in issue.lower() for issue in result["issues"]))

    def test_validate_wrong_extension(self) -> None:
        """Test validation of file with wrong extension."""
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            tmp.write(b"test")
            tmp.flush()

            result = validate_package(tmp.name)

            self.assertFalse(result["valid"])
            self.assertTrue(
                any(".whl" in issue for issue in result["issues"]),
                "Should mention .whl extension",
            )

    def test_validate_empty_wheel(self) -> None:
        """Test validation of empty wheel file."""
        with tempfile.NamedTemporaryFile(suffix=".whl", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            result = validate_package(tmp_path)

            self.assertFalse(result["valid"])
            self.assertTrue(
                any("empty" in issue.lower() for issue in result["issues"]),
                "Should mention empty file",
            )
        finally:
            tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
