"""Tests for Plugin-Builder v2 integration (ADR-0262).

Tests the complete workflow:
- Scaffolding generation
- Test execution
- Package building
- Development plan execution
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ..v2_integration import (
    PluginDeveloper,
    PluginDevelopmentPlan,
    develop_plugin,
)


class TestPluginDevelopmentPlan(unittest.TestCase):
    """Test PluginDevelopmentPlan dataclass."""

    def test_plan_creation(self) -> None:
        """Test creating a development plan."""
        plan = PluginDevelopmentPlan(
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
            description="Test plugin",
            author="Test Author",
        )

        self.assertEqual(plan.plugin_id, "my.plugin")
        self.assertEqual(plan.plugin_name, "My Plugin")
        self.assertEqual(plan.plugin_type, "data_connector")
        self.assertEqual(plan.steps, ["scaffold", "test", "build"])

    def test_plan_with_custom_steps(self) -> None:
        """Test plan with custom steps."""
        plan = PluginDevelopmentPlan(
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            steps=["scaffold"],
        )

        self.assertEqual(plan.steps, ["scaffold"])


class TestPluginDeveloper(unittest.TestCase):
    """Test PluginDeveloper workflow orchestration."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        """Clean up test resources."""
        self.temp_dir.cleanup()

    def test_scaffold_step(self) -> None:
        """Test the scaffold development step."""
        plan = PluginDevelopmentPlan(
            plugin_id="test.plugin",
            plugin_name="Test Plugin",
            steps=["scaffold"],
        )

        developer = PluginDeveloper()
        result = developer.develop(plan, self.output_dir)

        self.assertTrue(result.scaffold_dir is not None)
        self.assertTrue(result.scaffold_dir.exists())
        self.assertTrue(
            (result.scaffold_dir / "plugin.py").exists(),
            "plugin.py should be created",
        )

    def test_develop_plugin_function(self) -> None:
        """Test the develop_plugin convenience function."""
        result = develop_plugin(
            plugin_id="simple.plugin",
            plugin_name="Simple Plugin",
            output_dir=self.output_dir,
            steps=["scaffold"],
        )

        self.assertTrue(result.scaffold_dir is not None)
        self.assertTrue(result.scaffold_dir.exists())

    def test_unknown_step_handling(self) -> None:
        """Test handling of unknown steps."""
        plan = PluginDevelopmentPlan(
            plugin_id="test.plugin",
            plugin_name="Test Plugin",
            steps=["unknown_step"],
        )

        developer = PluginDeveloper()
        result = developer.develop(plan, self.output_dir)

        self.assertFalse(result.success)
        self.assertTrue(
            any("Unknown step" in e for e in result.errors),
            "Should report unknown step error",
        )


class TestDevelopmentResult(unittest.TestCase):
    """Test DevelopmentResult dataclass."""

    def test_result_creation(self) -> None:
        """Test creating a development result."""
        from ..v2_integration import DevelopmentResult

        result = DevelopmentResult(
            success=True,
            scaffold_dir=Path("/tmp/plugin"),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.scaffold_dir, Path("/tmp/plugin"))
        self.assertIsNotNone(result.errors)
        self.assertIsNotNone(result.warnings)


if __name__ == "__main__":
    unittest.main()
