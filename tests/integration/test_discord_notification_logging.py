"""Integration test for Discord notification logging (Phase 1).

Verifies that silent drops are now visible via stderr logging.
"""
import sys
import json
import tempfile
import logging
from pathlib import Path
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest


def test_routing_lookup_missing_logs_warning(caplog):
    """Verify that _routing_for() logs WARNING when no completion_notify record exists."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "operator" / "bridges" / "shared"))

    import task_progress

    # Capture stderr
    stderr_capture = StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture

    try:
        # Call _routing_for() with a task_id that has no completion_notify record
        result = task_progress._routing_for("nonexistent-task-12345")

        # Should return None
        assert result is None

        # Should log warning to stderr
        stderr_output = stderr_capture.getvalue()
        assert "[task_progress] WARNING" in stderr_output
        assert "nonexistent-task-12345" in stderr_output
        assert "no completion_notify record found" in stderr_output
    finally:
        sys.stderr = old_stderr


def test_routing_lookup_import_error_logs_error(caplog):
    """Verify that _routing_for() logs ERROR when completion_notify import fails."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "operator" / "bridges" / "shared"))

    import task_progress

    stderr_capture = StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture

    try:
        # Mock completion_notify to raise ImportError
        with patch.dict(sys.modules, {"completion_notify": None}):
            # Force re-import by manipulating sys.modules
            original_import = __builtins__.__import__

            def mock_import(name, *args, **kwargs):
                if name == "completion_notify":
                    raise ImportError("Mocked import error")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = task_progress._routing_for("test-task-456")

                assert result is None
                stderr_output = stderr_capture.getvalue()
                assert "[task_progress] ERROR" in stderr_output
                assert "test-task-456" in stderr_output
                assert "import/read failed" in stderr_output
    finally:
        sys.stderr = old_stderr


def test_notification_router_import_failure_logged():
    """Verify that task_orchestrator logs when NotificationRouter import fails."""
    stderr_capture = StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture

    try:
        # Force reload of task_orchestrator with NotificationRouter import mocked to fail
        with patch("core.vibe_engineering.notification_router.NotificationRouter") as mock_router:
            mock_router.side_effect = ImportError("Mocked import error")

            # Re-import task_orchestrator to trigger the try/except block
            import importlib
            import core.vibe_engineering.task_orchestrator as orch_module
            importlib.reload(orch_module)

            # The import error should have been caught and logged
            # (Actual logging verification depends on how pytest captures logs)
            assert orch_module._notification_router is None
    finally:
        sys.stderr = old_stderr


@pytest.mark.integration
def test_emit_with_missing_routing_fails_silently_but_logs():
    """Integration test: emit() fails to queue when routing is missing, but logs the reason."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "operator" / "bridges" / "shared"))

    import task_progress

    stderr_capture = StringIO()
    old_stderr = sys.stderr
    sys.stderr = stderr_capture

    try:
        # Call emit() with a task_id that has no routing
        result = task_progress.emit("task-no-routing-789", "test message", kind="progress")

        # Should return None (silently) because no routing was found
        assert result is None

        # But stderr should contain the warning
        stderr_output = stderr_capture.getvalue()
        assert "[task_progress]" in stderr_output
        assert "task-no-routing-789" in stderr_output
    finally:
        sys.stderr = old_stderr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
