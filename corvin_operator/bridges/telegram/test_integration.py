"""test_integration.py — Unit tests for integration.py

Tests integration.format_orchestration_for_telegram() and outbox writing.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from integration import (
    format_orchestration_for_telegram,
    _get_telegram_outbox_dir,
    get_handler_module,
)


class TestGetOutboxDir:
    """Test outbox directory resolution."""

    def test_outbox_dir_uses_corvin_home_env(self):
        """Test that CORVIN_HOME env var is respected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                outbox = _get_telegram_outbox_dir()
                assert str(outbox).startswith(tmpdir)
                assert "telegram" in str(outbox)

    def test_outbox_dir_default_home(self):
        """Test default ~/.corvin path when CORVIN_HOME is not set."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove CORVIN_HOME if present
            if "CORVIN_HOME" in os.environ:
                del os.environ["CORVIN_HOME"]

            outbox = _get_telegram_outbox_dir()
            # Should contain .corvin
            assert ".corvin" in str(outbox)
            assert "telegram" in str(outbox)

    def test_outbox_dir_path_structure(self):
        """Test the full path structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                outbox = _get_telegram_outbox_dir()
                expected_parts = ["bridges", "telegram", "outbox"]
                outbox_str = str(outbox)
                for part in expected_parts:
                    assert part in outbox_str


class TestFormatOrchestrationForTelegram:
    """Test format_orchestration_for_telegram() integration."""

    def test_write_orchestration_envelope_to_outbox(self):
        """Test writing orchestration envelope to outbox."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                result = format_orchestration_for_telegram(
                    batch_id="batch_001",
                    task_count=3,
                    success_count=3,
                    failed_tasks=[],
                    total_duration_secs=323,
                    voice_attachment_path=None,
                )

                assert result == {"default": True}

                # Verify envelope was written
                outbox = _get_telegram_outbox_dir()
                files = list(outbox.glob("orchestration_*.json"))
                assert len(files) == 1

                # Verify envelope content
                with open(files[0]) as f:
                    envelope = json.load(f)

                assert envelope["message_type"] == "orchestration_complete"
                assert envelope["batch_id"] == "batch_001"
                assert envelope["task_count"] == 3
                assert envelope["success_count"] == 3
                assert envelope["event_type"] == "ORCHESTRATION_COMPLETE_SUCCESS"

    def test_write_mixed_event(self):
        """Test writing a mixed event (with failures)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                failed_tasks = [
                    {"task_id": "task_001", "error": "Timeout"},
                    {"task_id": "task_002", "error": "Connection failed"},
                ]
                result = format_orchestration_for_telegram(
                    batch_id="batch_002",
                    task_count=5,
                    success_count=3,
                    failed_tasks=failed_tasks,
                    total_duration_secs=500,
                )

                assert result == {"default": True}

                # Verify event type
                outbox = _get_telegram_outbox_dir()
                files = list(outbox.glob("orchestration_*.json"))
                with open(files[0]) as f:
                    envelope = json.load(f)

                assert envelope["event_type"] == "ORCHESTRATION_COMPLETE_MIXED"
                assert envelope["success_count"] == 3
                assert len(envelope["failed_tasks"]) == 2

    def test_write_with_voice_attachment(self):
        """Test writing envelope with voice attachment path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                voice_path = "/path/to/summary.ogg"
                result = format_orchestration_for_telegram(
                    batch_id="batch_003",
                    task_count=2,
                    success_count=2,
                    failed_tasks=[],
                    total_duration_secs=120,
                    voice_attachment_path=voice_path,
                )

                assert result == {"default": True}

                # Verify voice path is in envelope
                outbox = _get_telegram_outbox_dir()
                files = list(outbox.glob("orchestration_*.json"))
                with open(files[0]) as f:
                    envelope = json.load(f)

                assert envelope["voice_attachment_path"] == voice_path

    def test_write_per_chat_envelopes(self):
        """Test writing per-chat envelopes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                target_chats = [123456, 789012]
                result = format_orchestration_for_telegram(
                    batch_id="batch_004",
                    task_count=1,
                    success_count=1,
                    failed_tasks=[],
                    total_duration_secs=60,
                    target_chats=target_chats,
                )

                # Should have result for both chats
                assert 123456 in result
                assert 789012 in result
                assert result[123456] is True
                assert result[789012] is True

                # Should have written two files
                outbox = _get_telegram_outbox_dir()
                files = list(outbox.glob("orchestration_*.json"))
                assert len(files) == 2

                # Verify each envelope has target_chat_id
                for f in files:
                    with open(f) as fh:
                        envelope = json.load(fh)
                    assert "target_chat_id" in envelope
                    assert envelope["target_chat_id"] in target_chats

    def test_per_chat_write_with_string_chat_ids(self):
        """Test that string chat IDs are coerced to int."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                target_chats = ["123456", "789012"]
                result = format_orchestration_for_telegram(
                    batch_id="batch_005",
                    task_count=1,
                    success_count=1,
                    failed_tasks=[],
                    total_duration_secs=60,
                    target_chats=target_chats,
                )

                # Result keys should be integers
                assert 123456 in result
                assert 789012 in result

    def test_outbox_creation_if_missing(self):
        """Test that outbox directory is created if missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                # Outbox should not exist yet
                outbox = _get_telegram_outbox_dir()
                assert not outbox.exists()

                # Call should create it
                format_orchestration_for_telegram(
                    batch_id="batch_006",
                    task_count=1,
                    success_count=1,
                    failed_tasks=[],
                    total_duration_secs=60,
                )

                # Now it should exist
                assert outbox.exists()
                assert outbox.is_dir()

    def test_atomic_write_file_rename(self):
        """Test that writes use atomic temp+rename pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                format_orchestration_for_telegram(
                    batch_id="batch_007",
                    task_count=1,
                    success_count=1,
                    failed_tasks=[],
                    total_duration_secs=60,
                )

                outbox = _get_telegram_outbox_dir()
                # Should only have .json files, not .json.tmp
                files = list(outbox.glob("*.json*"))
                assert len([f for f in files if f.suffix == ".json"]) >= 1
                assert len([f for f in files if f.suffix == ".tmp"]) == 0

    def test_write_failure_returns_false(self):
        """Test that write failures return False on open() error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("integration._get_telegram_outbox_dir") as mock_outbox:
                mock_outbox.return_value = Path(tmpdir)

                # Patch open() to raise PermissionError
                with patch("builtins.open", side_effect=PermissionError("Access denied")):
                    result = format_orchestration_for_telegram(
                        batch_id="batch_008",
                        task_count=1,
                        success_count=1,
                        failed_tasks=[],
                        total_duration_secs=60,
                    )

                    assert result == {"default": False}

    def test_envelope_schema_fields(self):
        """Test that all required fields are in the envelope."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                format_orchestration_for_telegram(
                    batch_id="batch_009",
                    task_count=5,
                    success_count=4,
                    failed_tasks=[{"task_id": "t1", "error": "err"}],
                    total_duration_secs=300,
                    voice_attachment_path="/path.ogg",
                )

                outbox = _get_telegram_outbox_dir()
                with open(list(outbox.glob("*.json"))[0]) as f:
                    envelope = json.load(f)

                required_fields = [
                    "channel",
                    "message_type",
                    "event_type",
                    "batch_id",
                    "task_count",
                    "success_count",
                    "failed_tasks",
                    "voice_attachment_path",
                    "timestamp",
                ]
                for field in required_fields:
                    assert field in envelope

    def test_filename_includes_batch_id(self):
        """Test that outbox filename includes batch_id prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"CORVIN_HOME": tmpdir}):
                batch_id = "batch_special_2026_09_22"
                format_orchestration_for_telegram(
                    batch_id=batch_id,
                    task_count=1,
                    success_count=1,
                    failed_tasks=[],
                    total_duration_secs=60,
                )

                outbox = _get_telegram_outbox_dir()
                files = list(outbox.glob("*.json"))
                assert len(files) == 1
                # Filename should contain truncated batch_id
                assert "batch_sp" in files[0].name


class TestGetHandlerModule:
    """Test lazy import of orchestration_handler."""

    def test_get_handler_module_success(self):
        """Test successful import of orchestration_handler."""
        module = get_handler_module()
        # Either should succeed or log warning
        if module:
            assert hasattr(module, "handle_orchestration_complete")

    def test_get_handler_module_handles_import_error(self):
        """Test that import errors are handled gracefully."""
        # Simulate import error by patching the import mechanism
        with patch("builtins.__import__", side_effect=ImportError("No module")):
            # Should not raise, just log warning
            module = get_handler_module()
            # Should return None on import error
            assert module is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
