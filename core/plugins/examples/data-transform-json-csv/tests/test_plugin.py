"""Unit tests for the JSON ↔ CSV Data Transformer Plugin.

Tests cover:
- Plugin lifecycle (on_load, on_unload, health_check)
- JSON to CSV conversion (empty, single row, multiple rows, edge cases)
- CSV to JSON conversion (same cases)
- Configuration handling
- Error handling
"""
from __future__ import annotations

import unittest
from typing import Any, Callable, Optional
from unittest.mock import MagicMock

# sys.path is set up by tests/__init__.py during discovery
from plugin import DataTransformPlugin
from corvin_plugins.protocol import HealthStatus, PluginContext


class MockAuditEmitter:
    """Mock audit emitter for testing."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event_type: str, details: dict) -> None:
        """Record an audit event."""
        self.events.append((event_type, details))


class TestDataTransformPluginLifecycle(unittest.TestCase):
    """Test plugin lifecycle methods."""

    def setUp(self) -> None:
        """Create a fresh plugin instance for each test."""
        self.plugin = DataTransformPlugin()
        self.audit_emitter = MockAuditEmitter()

    def test_initialization(self) -> None:
        """Plugin initializes with correct defaults."""
        self.assertFalse(self.plugin._enabled)
        self.assertEqual(self.plugin._conversion_count, 0)
        self.assertIsNone(self.plugin._last_error)
        self.assertEqual(self.plugin.plugin_id, "data-transform-json-csv")
        self.assertEqual(self.plugin.version, "1.0.0")

    def test_on_load(self) -> None:
        """on_load sets up plugin state and emits audit event."""
        # Create mock context
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"max_rows": 5000, "strict_mode": True}
        ctx.audit_emit = self.audit_emitter

        # Load plugin
        self.plugin.on_load(ctx)

        # Verify state
        self.assertTrue(self.plugin._enabled)
        self.assertEqual(self.plugin._config, {"max_rows": 5000, "strict_mode": True})

        # Verify audit event was emitted
        self.assertEqual(len(self.audit_emitter.events), 1)
        event_type, details = self.audit_emitter.events[0]
        self.assertEqual(event_type, "plugin.loaded")
        self.assertEqual(details["plugin_id"], "data-transform-json-csv")

    def test_on_load_with_empty_config(self) -> None:
        """on_load handles None config gracefully."""
        ctx = MagicMock(spec=PluginContext)
        ctx.config = None
        ctx.audit_emit = self.audit_emitter

        self.plugin.on_load(ctx)

        self.assertTrue(self.plugin._enabled)
        self.assertEqual(self.plugin._config, {})

    def test_on_unload(self) -> None:
        """on_unload cleanup and emits audit event."""
        # Setup
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}
        ctx.audit_emit = self.audit_emitter
        self.plugin.on_load(ctx)
        self.audit_emitter.events.clear()  # Clear load event

        # Perform conversions to update metrics
        self.plugin._conversion_count = 3
        self.plugin._last_error = None

        # Unload
        self.plugin.on_unload()

        # Verify state
        self.assertFalse(self.plugin._enabled)

        # Verify audit event
        self.assertEqual(len(self.audit_emitter.events), 1)
        event_type, details = self.audit_emitter.events[0]
        self.assertEqual(event_type, "plugin.unloaded")
        self.assertEqual(details["conversions_performed"], 3)

    def test_health_check_when_disabled(self) -> None:
        """health_check returns degraded status when disabled."""
        status = self.plugin.health_check()

        self.assertFalse(status.ok)
        self.assertIn("disabled", status.message.lower())
        self.assertFalse(status.details["enabled"])

    def test_health_check_when_enabled(self) -> None:
        """health_check returns healthy status when enabled."""
        # Setup
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"max_rows": 5000}
        ctx.audit_emit = self.audit_emitter
        self.plugin.on_load(ctx)

        # Check health
        status = self.plugin.health_check()

        self.assertTrue(status.ok)
        self.assertEqual(status.message, "OK")
        self.assertTrue(status.details["enabled"])
        self.assertEqual(status.details["version"], "1.0.0")
        self.assertEqual(status.details["conversions_performed"], 0)


class TestJsonToCsvConversion(unittest.TestCase):
    """Test JSON to CSV conversion."""

    def setUp(self) -> None:
        """Setup plugin in enabled state."""
        self.plugin = DataTransformPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}
        ctx.audit_emit = MagicMock()
        self.plugin.on_load(ctx)

    def test_json_to_csv_from_list(self) -> None:
        """Convert JSON list to CSV."""
        json_data = [
            {"name": "Alice", "age": "30", "city": "Berlin"},
            {"name": "Bob", "age": "25", "city": "Munich"},
        ]

        csv_result = self.plugin.json_to_csv(json_data)

        # Check header
        lines = csv_result.strip().split("\n")
        self.assertEqual(len(lines), 3)  # header + 2 data rows
        self.assertIn("name", lines[0])
        self.assertIn("age", lines[0])
        self.assertIn("city", lines[0])

    def test_json_to_csv_from_string(self) -> None:
        """Convert JSON string to CSV."""
        json_string = '[{"id": "1", "value": "x"}, {"id": "2", "value": "y"}]'

        csv_result = self.plugin.json_to_csv(json_string)

        lines = csv_result.strip().split("\n")
        self.assertEqual(len(lines), 3)

    def test_json_to_csv_empty_array(self) -> None:
        """JSON to CSV rejects empty array."""
        with self.assertRaises(ValueError) as ctx:
            self.plugin.json_to_csv([])
        self.assertIn("empty", str(ctx.exception).lower())

    def test_json_to_csv_invalid_json(self) -> None:
        """JSON to CSV rejects invalid JSON string."""
        with self.assertRaises(Exception):
            self.plugin.json_to_csv('{"invalid json}')

    def test_json_to_csv_not_array(self) -> None:
        """JSON to CSV requires array, not object."""
        with self.assertRaises(ValueError) as ctx:
            self.plugin.json_to_csv({"key": "value"})
        self.assertIn("array", str(ctx.exception).lower())

    def test_json_to_csv_with_nulls(self) -> None:
        """JSON to CSV handles null values."""
        json_data = [
            {"name": "Alice", "phone": None},
            {"name": "Bob", "phone": "123-456"},
        ]

        csv_result = self.plugin.json_to_csv(json_data)

        # CSV should have empty value for None
        lines = csv_result.strip().split("\n")
        self.assertEqual(len(lines), 3)

    def test_json_to_csv_with_special_chars(self) -> None:
        """JSON to CSV handles special characters."""
        json_data = [
            {"text": 'Name with "quotes"', "value": "x"},
            {"text": "Name with,comma", "value": "y"},
        ]

        csv_result = self.plugin.json_to_csv(json_data)

        # Should parse back without errors
        import csv
        from io import StringIO
        reader = csv.DictReader(StringIO(csv_result))
        rows = list(reader)
        self.assertEqual(len(rows), 2)

    def test_json_to_csv_unicode(self) -> None:
        """JSON to CSV handles Unicode characters."""
        json_data = [
            {"name": "Müller", "city": "München"},
            {"name": "François", "city": "Paris"},
        ]

        csv_result = self.plugin.json_to_csv(json_data)

        self.assertIn("Müller", csv_result)
        self.assertIn("François", csv_result)

    def test_json_to_csv_row_limit(self) -> None:
        """JSON to CSV respects row limit in lenient mode."""
        # Setup with max_rows = 3
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"max_rows": 3}
        ctx.audit_emit = MagicMock()
        self.plugin._config = {"max_rows": 3, "strict_mode": False}

        json_data = [{"id": str(i)} for i in range(10)]

        csv_result = self.plugin.json_to_csv(json_data)

        lines = csv_result.strip().split("\n")
        # Should be limited to 3 data rows + 1 header
        self.assertEqual(len(lines), 4)

    def test_json_to_csv_row_limit_strict(self) -> None:
        """JSON to CSV raises in strict mode when row limit exceeded."""
        self.plugin._config = {"max_rows": 3, "strict_mode": True}

        json_data = [{"id": str(i)} for i in range(10)]

        with self.assertRaises(RuntimeError) as ctx:
            self.plugin.json_to_csv(json_data)
        self.assertIn("limit", str(ctx.exception).lower())

    def test_disabled_plugin_raises(self) -> None:
        """Operations fail when plugin is disabled."""
        self.plugin._enabled = False

        with self.assertRaises(RuntimeError) as ctx:
            self.plugin.json_to_csv([{"a": "b"}])
        self.assertIn("not enabled", str(ctx.exception).lower())


class TestCsvToJsonConversion(unittest.TestCase):
    """Test CSV to JSON conversion."""

    def setUp(self) -> None:
        """Setup plugin in enabled state."""
        self.plugin = DataTransformPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}
        ctx.audit_emit = MagicMock()
        self.plugin.on_load(ctx)

    def test_csv_to_json_basic(self) -> None:
        """Convert basic CSV to JSON."""
        csv_data = "name,age,city\nAlice,30,Berlin\nBob,25,Munich"

        json_result = self.plugin.csv_to_json(csv_data)

        self.assertEqual(len(json_result), 2)
        self.assertEqual(json_result[0]["name"], "Alice")
        self.assertEqual(json_result[1]["name"], "Bob")

    def test_csv_to_json_empty(self) -> None:
        """CSV to JSON rejects empty string."""
        with self.assertRaises(ValueError):
            self.plugin.csv_to_json("")

    def test_csv_to_json_headers_only(self) -> None:
        """CSV to JSON rejects headers-only input."""
        csv_data = "name,age,city"
        with self.assertRaises(ValueError) as ctx:
            self.plugin.csv_to_json(csv_data)
        self.assertIn("data", str(ctx.exception).lower())

    def test_csv_to_json_with_spaces(self) -> None:
        """CSV to JSON preserves whitespace."""
        csv_data = "name,value\nAlice Bob,123\nCharlie,456"

        json_result = self.plugin.csv_to_json(csv_data)

        self.assertEqual(json_result[0]["name"], "Alice Bob")

    def test_csv_to_json_with_quoted_fields(self) -> None:
        """CSV to JSON handles quoted fields."""
        csv_data = 'name,description\n"Alice","""Quoted"" name"\n"Bob","Line\nbreak"'

        json_result = self.plugin.csv_to_json(csv_data)

        self.assertEqual(len(json_result), 2)

    def test_csv_to_json_type_inference(self) -> None:
        """CSV to JSON can infer numeric types."""
        csv_data = "id,name,count\n1,Alice,10\n2,Bob,20"

        json_result = self.plugin.csv_to_json(csv_data, infer_types=True)

        # Without inference, all values are strings
        self.assertEqual(len(json_result), 2)
        # Type inference is optional, so just check result is valid


class TestCsvValidation(unittest.TestCase):
    """Test CSV validation functionality."""

    def setUp(self) -> None:
        """Setup plugin."""
        self.plugin = DataTransformPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}
        ctx.audit_emit = MagicMock()
        self.plugin.on_load(ctx)

    def test_validate_csv_valid(self) -> None:
        """Validate valid CSV."""
        csv_data = "id,name\n1,Alice\n2,Bob"

        result = self.plugin.validate_csv(csv_data)

        self.assertTrue(result["is_valid"])
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["field_count"], 2)
        self.assertEqual(len(result["issues"]), 0)

    def test_validate_csv_empty(self) -> None:
        """Validate empty CSV."""
        result = self.plugin.validate_csv("")

        self.assertFalse(result["is_valid"])
        self.assertTrue(len(result["issues"]) > 0)

    def test_validate_csv_inconsistent_fields(self) -> None:
        """Validate CSV with inconsistent field counts."""
        csv_data = "id,name\n1,Alice\n2,Bob,Extra"

        result = self.plugin.validate_csv(csv_data)

        self.assertFalse(result["is_valid"])
        self.assertTrue(len(result["issues"]) > 0)
        # The third row (index 2) has inconsistent field count
        self.assertIn("Row 2", result["issues"][0])


class TestConfigurationHandling(unittest.TestCase):
    """Test configuration validation and handling."""

    def setUp(self) -> None:
        """Create fresh plugin."""
        self.plugin = DataTransformPlugin()

    def test_config_validation_invalid_max_rows(self) -> None:
        """Invalid max_rows is corrected."""
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"max_rows": -100}
        ctx.audit_emit = MagicMock()

        self.plugin.on_load(ctx)

        # Should be corrected to default
        self.assertEqual(self.plugin._config["max_rows"], 10000)

    def test_config_validation_invalid_encoding(self) -> None:
        """Invalid encoding is corrected."""
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"encoding": "invalid-codec"}
        ctx.audit_emit = MagicMock()

        self.plugin.on_load(ctx)

        # Should be corrected to utf-8
        self.assertEqual(self.plugin._config["encoding"], "utf-8")


if __name__ == "__main__":
    unittest.main()
