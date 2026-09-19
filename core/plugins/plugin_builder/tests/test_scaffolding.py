"""Tests for enhanced scaffolding system (ADR-0262).

Tests:
- Lifecycle hook template generation
- Scaffold file creation
- Plugin code generation
- Test module generation
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ..scaffolding import (
    EnhancedScaffolder,
    LifecycleHookTemplate,
    bootstrap_scaffold,
)


class TestLifecycleHookTemplate(unittest.TestCase):
    """Test LifecycleHookTemplate."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.template = LifecycleHookTemplate(
            plugin_id="test.plugin",
            plugin_type="data_connector",
            plugin_name="Test Plugin",
            description="A test plugin",
            author="Test Author",
        )

    def test_template_creation(self) -> None:
        """Test creating a lifecycle hook template."""
        self.assertEqual(self.template.plugin_id, "test.plugin")
        self.assertEqual(self.template.plugin_type, "data_connector")
        self.assertTrue(self.template.include_on_load)
        self.assertTrue(self.template.include_on_execute)
        self.assertTrue(self.template.include_on_unload)

    def test_plugin_code_generation(self) -> None:
        """Test generating plugin code."""
        code = self.template.generate_plugin_code()

        self.assertIn("class TestPluginPlugin", code)
        self.assertIn("def on_load", code)
        self.assertIn("def on_execute", code)
        self.assertIn("def on_unload", code)
        self.assertIn("def health_check", code)

    def test_plugin_code_selective_hooks(self) -> None:
        """Test generating plugin code with selective hooks."""
        template = LifecycleHookTemplate(
            plugin_id="min.plugin",
            plugin_type="provider",
            plugin_name="Minimal Plugin",
            description="Minimal plugin",
            include_on_execute=False,
        )

        code = template.generate_plugin_code()

        self.assertIn("def on_load", code)
        self.assertNotIn("def on_execute", code)
        self.assertIn("def on_unload", code)

    def test_conftest_generation(self) -> None:
        """Test generating conftest.py."""
        conftest = self.template.generate_conftest()

        self.assertIn("pytest", conftest)
        self.assertIn("plugin_context_fixture", conftest)
        self.assertIn("plugin_registry_fixture", conftest)

    def test_test_module_generation(self) -> None:
        """Test generating test module."""
        test_code = self.template.generate_test_module()

        self.assertIn("class TestPluginLifecycle", test_code)
        self.assertIn("test_plugin_init", test_code)
        self.assertIn("test_health_check", test_code)

    def test_class_name_generation(self) -> None:
        """Test Python class name generation."""
        names = [
            (LifecycleHookTemplate("simple", "p", "s", "d"), "SimplePlugin"),
            (
                LifecycleHookTemplate("my.plugin", "p", "s", "d"),
                "MyPluginPlugin",
            ),
            (
                LifecycleHookTemplate("my-plugin", "p", "s", "d"),
                "MyPluginPlugin",
            ),
        ]

        for template, expected_name in names:
            self.assertEqual(template._class_name(), expected_name)


class TestEnhancedScaffolder(unittest.TestCase):
    """Test EnhancedScaffolder."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)
        self.scaffolder = EnhancedScaffolder(self.output_dir)

    def tearDown(self) -> None:
        """Clean up test resources."""
        self.temp_dir.cleanup()

    def test_scaffold_creation(self) -> None:
        """Test creating a plugin scaffold."""
        template = LifecycleHookTemplate(
            plugin_id="test.plugin",
            plugin_type="data_connector",
            plugin_name="Test Plugin",
            description="Test plugin",
            author="Test Author",
        )

        files_created = self.scaffolder.scaffold(template)

        # Check key files were created
        self.assertIn("plugin", files_created)
        self.assertIn("tests", files_created)
        self.assertIn("setup", files_created)
        self.assertIn("pyproject", files_created)

        # Verify files exist
        for file_path in files_created.values():
            self.assertTrue(file_path.exists(), f"{file_path} should exist")

    def test_scaffold_directory_structure(self) -> None:
        """Test the directory structure of a scaffold."""
        template = LifecycleHookTemplate(
            plugin_id="my.plugin",
            plugin_type="provider",
            plugin_name="My Plugin",
            description="Test",
        )

        files = self.scaffolder.scaffold(template)
        plugin_dir = files["plugin"].parent

        # Check expected directories/files
        self.assertTrue((plugin_dir / "plugin.py").exists())
        self.assertTrue((plugin_dir / "__init__.py").exists())
        self.assertTrue((plugin_dir / "tests").is_dir())
        self.assertTrue((plugin_dir / "tests" / "conftest.py").exists())
        self.assertTrue((plugin_dir / "tests" / "test_plugin.py").exists())
        self.assertTrue((plugin_dir / "setup.py").exists())
        self.assertTrue((plugin_dir / "pyproject.toml").exists())
        self.assertTrue((plugin_dir / "README.md").exists())


class TestBootstrapScaffold(unittest.TestCase):
    """Test bootstrap_scaffold function."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        """Clean up test resources."""
        self.temp_dir.cleanup()

    def test_bootstrap_creates_complete_scaffold(self) -> None:
        """Test bootstrap_scaffold creates a complete scaffold."""
        files = bootstrap_scaffold(
            output_dir=self.output_dir,
            plugin_id="bootstrap.test",
            plugin_name="Bootstrap Test",
            plugin_type="data_connector",
            description="Bootstrap test plugin",
            author="Test Author",
        )

        # Verify files were created
        self.assertGreater(len(files), 0)

        for file_path in files.values():
            self.assertTrue(file_path.exists(), f"{file_path} should exist")

    def test_bootstrap_with_different_plugin_types(self) -> None:
        """Test bootstrap with different plugin types."""
        for plugin_type in ["data_connector", "provider", "skill", "mcp_server"]:
            with self.subTest(plugin_type=plugin_type):
                temp_dir = tempfile.TemporaryDirectory()
                try:
                    files = bootstrap_scaffold(
                        output_dir=temp_dir.name,
                        plugin_id=f"test.{plugin_type}",
                        plugin_name=f"Test {plugin_type}",
                        plugin_type=plugin_type,
                    )

                    self.assertGreater(len(files), 0)
                finally:
                    temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
