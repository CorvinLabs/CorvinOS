"""Unit tests for metrics recorders.

Phase 2, k=3: Verify metric recorders emit audit events.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path
_THIS_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _THIS_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

# Add operator/forge to path for security_events
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO / "operator" / "forge") not in sys.path:
    sys.path.insert(0, str(_REPO / "operator" / "forge"))

from metrics_recorders import (
    EngineMetricsCollector,
    WorkflowMetricsCollector,
    ContextMetricsCollector,
)


class EngineMetricsCollectorTests(unittest.TestCase):
    """Test EngineMetricsCollector."""

    def setUp(self):
        """Set up test fixtures."""
        self.tenant_id = "_default"
        self.engine_id = "claude_code"

    @patch("forge.paths.tenant_global_dir")
    def test_record_success_emits_event(self, mock_tenant_dir):
        """record_success emits an audit event."""
        # Mock the forge.paths import
        with patch("forge.security_events.write_event") as mock_write:
            with tempfile.TemporaryDirectory() as td:
                mock_tenant_dir.return_value = Path(td) / "global"

                EngineMetricsCollector.record_success(
                    self.tenant_id,
                    self.engine_id,
                    latency_ms=1234,
                    tokens_used=567,
                )

                # Verify the event was written
                mock_write.assert_called_once()
                args, kwargs = mock_write.call_args
                self.assertEqual(args[1], "engine.execution_completed")
                self.assertEqual(kwargs["severity"], "INFO")
                self.assertIn("engine_id", kwargs["details"])
                self.assertIn("latency_ms", kwargs["details"])
                self.assertIn("tokens_used", kwargs["details"])

    @patch("forge.paths.tenant_global_dir")
    def test_record_error_emits_event(self, mock_tenant_dir):
        """record_error emits an audit event."""
        with patch("forge.security_events.write_event") as mock_write:
            mock_tenant_dir.return_value = Path("/fake/tenant")

            EngineMetricsCollector.record_error(
                self.tenant_id,
                self.engine_id,
                error_type="timeout",
                latency_ms=500,
            )

            # Verify the event was written
            mock_write.assert_called_once()
            args, kwargs = mock_write.call_args
            self.assertEqual(args[1], "engine.execution_failed")
            self.assertEqual(kwargs["severity"], "WARNING")
            self.assertEqual(kwargs["details"]["error_type"], "timeout")

    def test_record_success_handles_exception(self):
        """record_success gracefully handles exceptions."""
        # Should not raise even if security_events is broken
        EngineMetricsCollector.record_success(
            self.tenant_id,
            self.engine_id,
            latency_ms=100,
        )

    def test_record_error_handles_exception(self):
        """record_error gracefully handles exceptions."""
        # Should not raise even if security_events is broken
        EngineMetricsCollector.record_error(
            self.tenant_id,
            self.engine_id,
            error_type="crash",
            latency_ms=50,
        )


class WorkflowMetricsCollectorTests(unittest.TestCase):
    """Test WorkflowMetricsCollector."""

    @patch("forge.paths.tenant_global_dir")
    def test_record_completion_time(self, mock_tenant_dir):
        """record_completion_time emits an audit event."""
        with patch("forge.security_events.write_event") as mock_write:
            mock_tenant_dir.return_value = Path("/fake/tenant")

            WorkflowMetricsCollector.record_completion_time(
                "_default",
                "workflow-123",
                status="completed",
                duration_ms=5000,
            )

            # Verify the event was written
            mock_write.assert_called_once()
            args, kwargs = mock_write.call_args
            self.assertEqual(args[1], "workflow.completed")
            self.assertEqual(kwargs["details"]["workflow_id"], "workflow-123")
            self.assertEqual(kwargs["details"]["status"], "completed")


class ContextMetricsCollectorTests(unittest.TestCase):
    """Test ContextMetricsCollector."""

    @patch("forge.paths.tenant_global_dir")
    def test_record_push(self, mock_tenant_dir):
        """record_push emits an audit event."""
        with patch("forge.security_events.write_event") as mock_write:
            mock_tenant_dir.return_value = Path("/fake/tenant")

            ContextMetricsCollector.record_push(
                "_default",
                "ctx-123",
                context_size_bytes=1024,
            )

            # Verify the event was written
            mock_write.assert_called_once()
            args, kwargs = mock_write.call_args
            self.assertEqual(args[1], "context.push")
            self.assertEqual(kwargs["details"]["context_id"], "ctx-123")
            self.assertEqual(kwargs["details"]["size_bytes"], 1024)

    @patch("forge.paths.tenant_global_dir")
    def test_record_pop(self, mock_tenant_dir):
        """record_pop emits an audit event."""
        with patch("forge.security_events.write_event") as mock_write:
            mock_tenant_dir.return_value = Path("/fake/tenant")

            ContextMetricsCollector.record_pop("_default", "ctx-123")

            # Verify the event was written
            mock_write.assert_called_once()
            args, kwargs = mock_write.call_args
            self.assertEqual(args[1], "context.pop")
            self.assertEqual(kwargs["details"]["context_id"], "ctx-123")


if __name__ == "__main__":
    unittest.main()
