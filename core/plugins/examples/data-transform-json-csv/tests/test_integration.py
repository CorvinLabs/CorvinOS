"""Integration tests for the JSON ↔ CSV Data Transformer Plugin.

These tests verify:
- Plugin registration with the registry
- Plugin discovery and loading
- End-to-end transformation workflows
- Multi-step conversions (JSON → CSV → JSON)
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# sys.path is set up by tests/__init__.py during discovery
from plugin import DataTransformPlugin
from corvin_plugins.protocol import PluginContext


class TestPluginRegistration(unittest.TestCase):
    """Test plugin registration and discovery."""

    def test_plugin_has_required_attributes(self) -> None:
        """Plugin has all required interface attributes."""
        plugin = DataTransformPlugin()

        # Required CorvinPlugin attributes
        self.assertTrue(hasattr(plugin, "plugin_id"))
        self.assertTrue(hasattr(plugin, "plugin_type"))
        self.assertTrue(hasattr(plugin, "version"))
        self.assertTrue(hasattr(plugin, "display_name"))

        # Required CorvinPlugin methods
        self.assertTrue(callable(plugin.on_load))
        self.assertTrue(callable(plugin.on_unload))
        self.assertTrue(callable(plugin.health_check))

        # Verify attribute types
        self.assertIsInstance(plugin.plugin_id, str)
        self.assertIsInstance(plugin.plugin_type, str)
        self.assertIsInstance(plugin.version, str)
        self.assertIsInstance(plugin.display_name, str)

    def test_plugin_type_is_valid(self) -> None:
        """Plugin declares a valid plugin_type."""
        plugin = DataTransformPlugin()
        self.assertIn(plugin.plugin_type, ["custom", "notification_backend", "recall_backend"])

    def test_plugin_id_is_valid(self) -> None:
        """Plugin ID follows naming conventions."""
        plugin = DataTransformPlugin()
        plugin_id = plugin.plugin_id

        # Should be lowercase with hyphens
        self.assertEqual(plugin_id, plugin_id.lower())
        self.assertNotIn("_", plugin_id)
        self.assertNotIn(" ", plugin_id)


class TestPluginLifecycleIntegration(unittest.TestCase):
    """Test plugin lifecycle with mock registry."""

    def setUp(self) -> None:
        """Create plugin and context."""
        self.plugin = DataTransformPlugin()
        self.audit_events: list[tuple[str, dict]] = []

    def mock_audit_emit(self, event_type: str, details: dict) -> None:
        """Mock audit emitter."""
        self.audit_events.append((event_type, details))

    def test_full_lifecycle(self) -> None:
        """Test complete plugin lifecycle."""
        # Create context
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"max_rows": 1000}
        ctx.audit_emit = self.mock_audit_emit

        # Load
        self.plugin.on_load(ctx)
        self.assertTrue(self.plugin._enabled)
        self.assertEqual(len(self.audit_events), 1)
        self.assertEqual(self.audit_events[0][0], "plugin.loaded")

        # Health check while loaded
        status = self.plugin.health_check()
        self.assertTrue(status.ok)

        # Perform conversions
        json_data = [{"id": "1", "name": "Alice"}]
        csv_result = self.plugin.json_to_csv(json_data)
        self.assertTrue(len(csv_result) > 0)

        # Unload
        self.plugin.on_unload()
        self.assertFalse(self.plugin._enabled)
        self.assertEqual(len(self.audit_events), 3)  # loaded + conversion + unloaded

        # Health check after unload
        status = self.plugin.health_check()
        self.assertFalse(status.ok)


class TestEndToEndConversion(unittest.TestCase):
    """Test end-to-end conversion workflows."""

    def setUp(self) -> None:
        """Setup plugin for E2E tests."""
        self.plugin = DataTransformPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}
        ctx.audit_emit = MagicMock()
        self.plugin.on_load(ctx)

    def test_json_to_csv_to_json_roundtrip(self) -> None:
        """Test roundtrip: JSON → CSV → JSON."""
        # Original data
        original = [
            {"id": "1", "name": "Alice", "score": "95.5"},
            {"id": "2", "name": "Bob", "score": "87.3"},
        ]

        # Convert to CSV
        csv_data = self.plugin.json_to_csv(original)
        self.assertTrue(len(csv_data) > 0)

        # Convert back to JSON
        recovered = self.plugin.csv_to_json(csv_data)

        # Verify data integrity
        self.assertEqual(len(recovered), len(original))
        self.assertEqual(recovered[0]["name"], "Alice")
        self.assertEqual(recovered[1]["name"], "Bob")

    def test_large_dataset_conversion(self) -> None:
        """Test conversion of larger dataset."""
        # Create 100-row dataset
        data = [
            {"row_id": str(i), "value": f"val_{i}", "active": "true" if i % 2 == 0 else "false"}
            for i in range(100)
        ]

        # Convert to CSV
        csv_data = self.plugin.json_to_csv(data)

        # Verify row count
        lines = csv_data.strip().split("\n")
        self.assertEqual(len(lines), 101)  # 100 rows + 1 header

        # Convert back
        recovered = self.plugin.csv_to_json(csv_data)
        self.assertEqual(len(recovered), 100)

    def test_complex_json_structure(self) -> None:
        """Test with various data types in JSON."""
        data = [
            {
                "id": "1",
                "name": "Alice",
                "age": "30",
                "active": "true",
                "score": "95.5",
                "notes": "Top performer",
            },
            {
                "id": "2",
                "name": "Bob",
                "age": "25",
                "active": "false",
                "score": "87.3",
                "notes": "On leave",
            },
        ]

        # Convert to CSV
        csv_data = self.plugin.json_to_csv(data)

        # Verify all fields present
        self.assertIn("id", csv_data)
        self.assertIn("name", csv_data)
        self.assertIn("age", csv_data)
        self.assertIn("active", csv_data)
        self.assertIn("score", csv_data)
        self.assertIn("notes", csv_data)

    def test_csv_with_special_content(self) -> None:
        """Test CSV parsing with special content."""
        csv_data = """id,text,comment
1,"Simple text","No special chars"
2,"Contains, comma","With quote \\"inside\\""
3,"Multi-line text with
line break","Normal"""

        result = self.plugin.csv_to_json(csv_data)

        self.assertEqual(len(result), 3)
        self.assertIn("id", result[0])
        self.assertIn("text", result[0])

    def test_incremental_updates(self) -> None:
        """Test handling multiple sequential conversions."""
        datasets = [
            [{"id": "1", "value": "a"}],
            [{"id": "2", "value": "b"}, {"id": "3", "value": "c"}],
            [{"id": "4", "value": "d"}, {"id": "5", "value": "e"}, {"id": "6", "value": "f"}],
        ]

        results = []
        for dataset in datasets:
            csv_data = self.plugin.json_to_csv(dataset)
            json_result = self.plugin.csv_to_json(csv_data)
            results.append(json_result)

        # Verify all conversions succeeded
        self.assertEqual(len(results), 3)
        self.assertEqual(len(results[0]), 1)
        self.assertEqual(len(results[1]), 2)
        self.assertEqual(len(results[2]), 3)

    def test_conversion_metric_tracking(self) -> None:
        """Test that conversions are tracked in metrics."""
        initial_count = self.plugin._conversion_count

        # Perform several conversions
        for i in range(5):
            data = [{"id": str(i)}]
            self.plugin.json_to_csv(data)

        # Verify count increased
        self.assertEqual(self.plugin._conversion_count, initial_count + 5)

    def test_csv_validation_in_workflow(self) -> None:
        """Test CSV validation as part of workflow."""
        # Create and validate CSV
        csv_data = "id,name\n1,Alice\n2,Bob"

        validation = self.plugin.validate_csv(csv_data)
        self.assertTrue(validation["is_valid"])

        # Use validated CSV
        json_result = self.plugin.csv_to_json(csv_data)
        self.assertEqual(len(json_result), 2)


class TestErrorRecovery(unittest.TestCase):
    """Test error handling and recovery."""

    def setUp(self) -> None:
        """Setup plugin."""
        self.plugin = DataTransformPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"strict_mode": False}
        ctx.audit_emit = MagicMock()
        self.plugin.on_load(ctx)

    def test_recovery_from_invalid_json(self) -> None:
        """Plugin recovers from failed conversion."""
        # First: attempt invalid conversion
        with self.assertRaises(Exception):
            self.plugin.json_to_csv('{"invalid": ')

        # Verify plugin is still functional
        valid_data = [{"id": "1"}]
        csv_result = self.plugin.json_to_csv(valid_data)
        self.assertTrue(len(csv_result) > 0)

    def test_error_tracking(self) -> None:
        """Plugin tracks error messages."""
        self.assertIsNone(self.plugin._last_error)

        # Trigger error
        with self.assertRaises(ValueError):
            self.plugin.json_to_csv([])

        # Verify error was tracked
        self.assertIsNotNone(self.plugin._last_error)
        self.assertIn("empty", self.plugin._last_error.lower())

    def test_successful_conversion_clears_error(self) -> None:
        """Successful conversion clears previous error."""
        # First: trigger error
        with self.assertRaises(ValueError):
            self.plugin.json_to_csv([])
        self.assertIsNotNone(self.plugin._last_error)

        # Then: successful conversion
        data = [{"id": "1"}]
        self.plugin.json_to_csv(data)

        # Error should be cleared
        self.assertIsNone(self.plugin._last_error)


class TestManifestCompatibility(unittest.TestCase):
    """Test that plugin implementation matches manifest."""

    def test_manifest_exists(self) -> None:
        """Manifest file exists."""
        manifest_path = Path(__file__).parent.parent / "manifest.yaml"
        self.assertTrue(manifest_path.exists(), "manifest.yaml not found")

    def test_plugin_entry_point(self) -> None:
        """Plugin can be instantiated from entry point."""
        # The manifest specifies: plugin.py::DataTransformPlugin
        plugin = DataTransformPlugin()
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.plugin_id, "data-transform-json-csv")

    def test_version_match(self) -> None:
        """Plugin version matches manifest."""
        plugin = DataTransformPlugin()
        # Manifest specifies version: "1.0.0"
        self.assertEqual(plugin.version, "1.0.0")


if __name__ == "__main__":
    unittest.main()
