"""Tests for plugin testing framework (ADR-0262).

Tests:
- Pytest fixtures
- Test runners
- Plugin structure validation
- Audit trail recording
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ..testing_framework import (
    CorvinPluginTestCase,
    PluginContext,
    PluginRegistry,
    PluginTestRunner,
    validate_plugin_structure,
)


class TestPluginContext(unittest.TestCase):
    """Test PluginContext."""

    def test_context_creation(self) -> None:
        """Test creating a plugin context."""
        context = PluginContext(
            plugin_id="test.plugin",
            tenant_id="_default",
            session_id="test-session",
        )

        self.assertEqual(context.plugin_id, "test.plugin")
        self.assertEqual(context.tenant_id, "_default")
        self.assertEqual(context.session_id, "test-session")

    def test_audit_event_recording(self) -> None:
        """Test recording audit events."""
        context = PluginContext(plugin_id="test.plugin")

        context.log_audit_event(
            "plugin_executed",
            {"status": "success", "duration_ms": 42},
        )

        events = context.get_audit_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "plugin_executed")
        self.assertEqual(events[0]["payload"]["status"], "success")


class TestPluginRegistry(unittest.TestCase):
    """Test PluginRegistry."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.registry = PluginRegistry()

    def test_register_and_get_plugin(self) -> None:
        """Test registering and retrieving a plugin."""
        plugin_instance = object()
        self.registry.register("test.plugin", plugin_instance)

        retrieved = self.registry.get_active("test.plugin")
        self.assertIs(retrieved, plugin_instance)

    def test_register_with_config(self) -> None:
        """Test registering a plugin with configuration."""
        config = {"key": "value"}
        plugin = object()

        self.registry.register("test.plugin", plugin, config)
        retrieved_config = self.registry.get_config("test.plugin")

        self.assertEqual(retrieved_config, config)

    def test_registry_clear(self) -> None:
        """Test clearing the registry."""
        plugin = object()
        self.registry.register("test.plugin", plugin)

        self.registry.clear()

        self.assertIsNone(self.registry.get_active("test.plugin"))


class TestCorvinPluginTestCase(unittest.TestCase):
    """Test CorvinPluginTestCase base class."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.test_case = CorvinPluginTestCase()
        self.test_case.setUp()

    def tearDown(self) -> None:
        """Clean up test resources."""
        self.test_case.tearDown()

    def test_plugin_home_directory(self) -> None:
        """Test that plugin home directory is created."""
        self.assertTrue(
            self.test_case.plugin_home.exists(),
            "Plugin home should be created",
        )
        self.assertTrue(
            (self.test_case.plugin_home / "audit.jsonl").exists(),
            "Audit file should exist",
        )

    def test_register_and_retrieve_plugin(self) -> None:
        """Test plugin registration."""
        plugin = object()
        self.test_case.register_plugin("test.plugin", plugin)

        retrieved = self.test_case.get_plugin("test.plugin")
        self.assertIs(retrieved, plugin)

    def test_audit_event_assertion(self) -> None:
        """Test asserting audit events."""
        # Record a test event
        self.test_case.audit_events.append(
            {
                "event_type": "plugin_executed",
                "payload": {"status": "success"},
            }
        )

        # This should not raise
        self.test_case.assert_audit_event(
            "plugin_executed",
            {"status"},
        )

    def test_no_audit_event_assertion(self) -> None:
        """Test asserting no audit events."""
        # This should not raise (no events recorded)
        self.test_case.assert_no_audit_event("plugin_executed")

        # Record an event
        self.test_case.audit_events.append(
            {"event_type": "plugin_executed"}
        )

        # This should raise
        with self.assertRaises(AssertionError):
            self.test_case.assert_no_audit_event("plugin_executed")


class TestPluginTestRunner(unittest.TestCase):
    """Test PluginTestRunner."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.plugin_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        """Clean up test resources."""
        self.temp_dir.cleanup()

    def test_runner_initialization(self) -> None:
        """Test initializing a test runner."""
        runner = PluginTestRunner(self.plugin_dir)

        self.assertEqual(runner.plugin_dir, self.plugin_dir)
        self.assertEqual(runner.test_dir, self.plugin_dir / "tests")

    def test_validate_structure_missing_plugin_file(self) -> None:
        """Test structure validation with missing plugin file."""
        runner = PluginTestRunner(self.plugin_dir)
        structure = runner.validate_structure()

        self.assertFalse(structure.is_valid())
        self.assertTrue(
            any("plugin.py" in issue for issue in structure.issues),
            "Should report missing plugin.py",
        )

    def test_validate_structure_missing_tests(self) -> None:
        """Test structure validation with missing tests."""
        # Create plugin.py
        (self.plugin_dir / "plugin.py").touch()

        runner = PluginTestRunner(self.plugin_dir)
        structure = runner.validate_structure()

        self.assertFalse(structure.is_valid())
        self.assertTrue(
            any("tests" in issue.lower() for issue in structure.issues),
            "Should report missing tests",
        )

    def test_validate_structure_missing_build_config(self) -> None:
        """Test structure validation with missing build config."""
        # Create plugin.py and tests
        (self.plugin_dir / "plugin.py").touch()
        tests_dir = self.plugin_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_plugin.py").touch()

        runner = PluginTestRunner(self.plugin_dir)
        structure = runner.validate_structure()

        self.assertFalse(structure.is_valid())
        self.assertTrue(
            any("setup.py" in issue or "pyproject.toml" in issue for issue in structure.issues),
            "Should report missing build config",
        )

    def test_validate_structure_complete(self) -> None:
        """Test structure validation with complete scaffold."""
        # Create required files
        (self.plugin_dir / "plugin.py").touch()
        (self.plugin_dir / "setup.py").touch()

        tests_dir = self.plugin_dir / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_plugin.py").touch()

        runner = PluginTestRunner(self.plugin_dir)
        structure = runner.validate_structure()

        self.assertTrue(structure.is_valid())


class TestValidatePluginStructure(unittest.TestCase):
    """Test validate_plugin_structure function."""

    def test_validate_complete_plugin(self) -> None:
        """Test validating a complete plugin."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir)

            # Create required structure
            (plugin_dir / "plugin.py").write_text(
                "def on_load(): pass\ndef on_unload(): pass"
            )
            (plugin_dir / "setup.py").touch()

            tests_dir = plugin_dir / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_plugin.py").touch()

            structure = validate_plugin_structure(plugin_dir)

            self.assertTrue(structure.is_valid())
            self.assertTrue(structure.has_lifecycle_hooks)


if __name__ == "__main__":
    unittest.main()
