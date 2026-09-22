"""test_orchestration_handler.py — Unit and integration tests for orchestration_handler.py

Tests:
  1. format_orchestration_summary — message formatting
  2. handle_orchestration_complete — send text + voice
  3. Error handling — graceful degradation on voice failure
  4. Sync wrapper — synchronous invocation
  5. HTML escaping — XSS prevention
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchestration_handler import (
    format_orchestration_summary,
    handle_orchestration_complete,
    handle_orchestration_complete_sync,
    _format_duration,
)


class TestFormatOrchestrationSummary:
    """Test message formatting."""

    def test_success_event_basic(self):
        """Test formatting of a successful orchestration event."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "task_count": 3,
            "success_count": 3,
            "failed_tasks": [],
            "timestamp": time.time(),
        }
        result = format_orchestration_summary(payload)
        assert "✅" in result
        assert "All Orchestration Tasks Complete" in result
        assert "3/3 completed" in result
        assert "<b>" in result  # HTML formatted
        assert "<i>" in result

    def test_mixed_event_with_failures(self):
        """Test formatting of an event with failed tasks."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_MIXED",
            "task_count": 5,
            "success_count": 3,
            "failed_tasks": [
                {"task_id": "task_001", "error": "Timeout after 300s"},
                {"task_id": "task_002", "error": "Connection refused"},
            ],
            "timestamp": time.time(),
        }
        result = format_orchestration_summary(payload)
        assert "⚠️" in result
        assert "3/5 Succeeded" in result
        assert "Failed Tasks" in result
        assert "task_001" in result
        assert "task_002" in result
        assert "Timeout after 300s" in result

    def test_html_escaping_prevents_xss(self):
        """Test that HTML special characters are escaped."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_MIXED",
            "task_count": 1,
            "success_count": 0,
            "failed_tasks": [
                {
                    "task_id": "bad_task",
                    "error": "Error: <script>alert('xss')</script>",
                }
            ],
            "timestamp": time.time(),
        }
        result = format_orchestration_summary(payload)
        # Should be escaped
        assert "&lt;script&gt;" in result
        assert "&lt;/script&gt;" in result
        assert "<script>" not in result

    def test_truncated_failed_tasks_list(self):
        """Test that long failed task lists are truncated."""
        failed_tasks = [
            {"task_id": f"task_{i:03d}", "error": f"Error {i}"}
            for i in range(10)
        ]
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_MIXED",
            "task_count": 10,
            "success_count": 0,
            "failed_tasks": failed_tasks,
            "timestamp": time.time(),
        }
        result = format_orchestration_summary(payload)
        # Should show first 5 and mention remaining
        assert "task_000" in result
        assert "task_004" in result
        assert "and 5 more" in result

    def test_missing_optional_fields(self):
        """Test handling of missing optional fields."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "task_count": 2,
            "success_count": 2,
            # missing failed_tasks, timestamp, etc.
        }
        result = format_orchestration_summary(payload)
        assert "✅" in result
        assert "2/2 completed" in result
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unknown_event_type(self):
        """Test handling of unknown event types."""
        payload = {
            "event_type": "UNKNOWN_EVENT",
            "task_count": 0,
            "success_count": 0,
            "failed_tasks": [],
        }
        result = format_orchestration_summary(payload)
        assert "🔔" in result
        assert "UNKNOWN_EVENT" in result


class TestHandleOrchestrationComplete:
    """Test async send operations."""

    @pytest.mark.asyncio
    async def test_send_text_message_success(self):
        """Test successful text message delivery."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "timestamp": time.time(),
        }
        chat_id = 123456
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=Mock(message_id=999))

        result = await handle_orchestration_complete(payload, chat_id, bot)

        assert result is True
        bot.send_message.assert_called_once()
        call_args = bot.send_message.call_args
        assert call_args[0][0] == chat_id
        assert "✅" in call_args[0][1]
        assert call_args[1]["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_voice_note_success(self):
        """Test successful voice note delivery."""
        with tempfile.NamedTemporaryFile(
            suffix=".ogg", delete=False
        ) as tmp:
            voice_path = tmp.name
            tmp.write(b"fake ogg data")

        try:
            payload = {
                "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
                "task_count": 1,
                "success_count": 1,
                "failed_tasks": [],
                "voice_attachment_path": voice_path,
                "timestamp": time.time(),
            }
            chat_id = 123456
            bot = AsyncMock()
            bot.send_message = AsyncMock(return_value=Mock(message_id=999))
            bot.send_voice = AsyncMock(return_value=Mock(voice={"file_id": "abc"}))

            result = await handle_orchestration_complete(payload, chat_id, bot)

            assert result is True
            bot.send_message.assert_called_once()
            bot.send_voice.assert_called_once()
            voice_call = bot.send_voice.call_args
            assert voice_call[0][0] == chat_id
            assert "completion summary" in voice_call[1]["caption"]

        finally:
            if os.path.exists(voice_path):
                os.unlink(voice_path)

    @pytest.mark.asyncio
    async def test_voice_note_missing_file_graceful(self):
        """Test graceful handling when voice file doesn't exist."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "voice_attachment_path": "/nonexistent/file.ogg",
            "timestamp": time.time(),
        }
        chat_id = 123456
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=Mock(message_id=999))
        bot.send_voice = AsyncMock()

        result = await handle_orchestration_complete(payload, chat_id, bot)

        # Should succeed because text was sent
        assert result is True
        bot.send_message.assert_called_once()
        # Voice should not be called
        bot.send_voice.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_message_failure_blocked(self):
        """Test that failure to send text message returns False."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "timestamp": time.time(),
        }
        chat_id = 123456
        bot = AsyncMock()
        bot.send_message = AsyncMock(
            side_effect=Exception("API error: invalid chat_id")
        )

        result = await handle_orchestration_complete(payload, chat_id, bot)

        assert result is False
        bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_voice_failure_does_not_block_text(self):
        """Test that voice failure doesn't prevent text delivery."""
        with tempfile.NamedTemporaryFile(
            suffix=".ogg", delete=False
        ) as tmp:
            voice_path = tmp.name
            tmp.write(b"fake ogg data")

        try:
            payload = {
                "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
                "task_count": 1,
                "success_count": 1,
                "failed_tasks": [],
                "voice_attachment_path": voice_path,
                "timestamp": time.time(),
            }
            chat_id = 123456
            bot = AsyncMock()
            bot.send_message = AsyncMock(return_value=Mock(message_id=999))
            bot.send_voice = AsyncMock(
                side_effect=Exception("Voice delivery failed")
            )

            result = await handle_orchestration_complete(payload, chat_id, bot)

            # Should still succeed because text was sent
            assert result is True
            bot.send_message.assert_called_once()
            bot.send_voice.assert_called_once()

        finally:
            if os.path.exists(voice_path):
                os.unlink(voice_path)

    @pytest.mark.asyncio
    async def test_chat_id_coercion_to_int(self):
        """Test that chat_id is correctly coerced to int."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "timestamp": time.time(),
        }
        chat_id = "987654"  # String
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=Mock(message_id=999))

        result = await handle_orchestration_complete(payload, chat_id, bot)

        assert result is True
        # Check that chat_id was converted to int
        call_args = bot.send_message.call_args
        assert call_args[0][0] == 987654


class TestSyncWrapper:
    """Test synchronous wrapper."""

    def test_sync_wrapper_with_running_loop(self):
        """Test sync wrapper behavior with running event loop."""
        payload = {
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "timestamp": time.time(),
        }
        chat_id = 123456
        bot = Mock()

        # Mock both asyncio.get_event_loop and asyncio.run
        with patch("asyncio.get_event_loop") as mock_get_loop:
            with patch("asyncio.run") as mock_run:
                # Simulate no running loop
                mock_loop = Mock()
                mock_loop.is_running.return_value = False
                mock_get_loop.return_value = mock_loop

                mock_run.return_value = True
                result = handle_orchestration_complete_sync(payload, chat_id, bot)

                # Should return the mocked result
                assert result is True

    def test_sync_wrapper_exception_handling(self):
        """Test that sync wrapper handles exceptions gracefully."""
        payload = {}
        chat_id = "invalid"
        bot = None

        result = handle_orchestration_complete_sync(payload, chat_id, bot)

        # Should return False on error
        assert result is False


class TestFormatDuration:
    """Test duration formatting helper."""

    def test_format_duration_seconds(self):
        """Test formatting durations under 60 seconds."""
        assert _format_duration(0) == "0s"
        assert _format_duration(1) == "1s"
        assert _format_duration(45) == "45s"

    def test_format_duration_minutes(self):
        """Test formatting durations in minutes."""
        assert _format_duration(60) == "1m"
        assert _format_duration(90) == "1m 30s"
        assert _format_duration(323) == "5m 23s"
        assert _format_duration(3599) == "59m 59s"

    def test_format_duration_hours(self):
        """Test formatting durations in hours."""
        assert _format_duration(3600) == "1h"
        assert _format_duration(3660) == "1h 1m"
        assert _format_duration(7322) == "2h 2m"


class TestIntegrationEnvelope:
    """Integration tests with real orchestration_router envelopes."""

    @pytest.mark.asyncio
    async def test_real_envelope_format(self):
        """Test formatting a real orchestration_router envelope."""
        # Simulate real envelope from orchestration_router.py
        payload = {
            "channel": "telegram",
            "message_type": "orchestration_complete",
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "batch_id": "batch_2026_09_22_001",
            "task_count": 3,
            "success_count": 3,
            "failed_tasks": [],
            "text": "✅ 3 background tasks completed successfully in 5m 23s",
            "voice_attachment_path": None,
            "timestamp": time.time(),
            "metadata": {},
        }

        chat_id = 123456789
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=Mock(message_id=999))

        result = await handle_orchestration_complete(payload, chat_id, bot)

        assert result is True
        bot.send_message.assert_called_once()
        sent_text = bot.send_message.call_args[0][1]
        assert "✅" in sent_text
        assert "3/3" in sent_text


if __name__ == "__main__":
    # Run tests: pytest test_orchestration_handler.py -v
    pytest.main([__file__, "-v"])
