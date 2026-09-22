"""test_email_orchestration.py — Tests for orchestration_handler.py.

Tests cover:
- HTML body generation with task summaries
- SMTP send with mock server
- Voice attachment handling (OGG, MP3)
- Error handling (missing recipient, SMTP failure)
- Graceful fallback on attachment failure
- Outbox polling
"""
import json
import logging
import smtplib
import tempfile
import time
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from orchestration_handler import (
    OrchestrationPayload,
    _format_duration,
    _generate_html_body,
    handle_orchestration_complete,
    process_orchestration_outbox,
)

logger = logging.getLogger(__name__)


class TestFormatDuration:
    """Test duration formatting."""

    def test_seconds(self):
        assert _format_duration(45) == "45s"

    def test_minutes(self):
        assert _format_duration(150) == "2m 30s"
        assert _format_duration(120) == "2m"

    def test_hours(self):
        assert _format_duration(3661) == "1h 1m"
        assert _format_duration(3600) == "1h"


class TestGenerateHtmlBody:
    """Test HTML email body generation."""

    def test_success_event(self):
        payload = OrchestrationPayload(
            channel="email",
            message_type="orchestration_complete",
            event_type="ORCHESTRATION_COMPLETE_SUCCESS",
            batch_id="batch_123",
            task_count=5,
            success_count=5,
            failed_tasks=[],
            text="✅ 5 tasks completed in 2m 35s",
            voice_attachment_path=None,
            timestamp=time.time(),
            metadata={},
        )

        html = _generate_html_body(payload)

        assert "✅ All Tasks Completed Successfully" in html
        assert "batch_123" in html
        assert "5" in html  # task count
        assert "View Full Details in Console" in html
        assert "#d4edda" in html  # Light green background

    def test_mixed_event_with_failures(self):
        payload = OrchestrationPayload(
            channel="email",
            message_type="orchestration_complete",
            event_type="ORCHESTRATION_COMPLETE_MIXED",
            batch_id="batch_456",
            task_count=10,
            success_count=8,
            failed_tasks=[
                {"task_id": "task_1", "error": "Timeout"},
                {"task_id": "task_2", "error": "Network error"},
            ],
            text="⚠️ 8 of 10 tasks completed",
            voice_attachment_path=None,
            timestamp=time.time(),
            metadata={},
        )

        html = _generate_html_body(payload)

        assert "⚠️ Some Tasks Failed" in html
        assert "batch_456" in html
        assert "8" in html  # success count
        assert "2" in html  # failed count
        assert "task_1" in html
        assert "Timeout" in html
        assert "#fff3cd" in html  # Light yellow background

    def test_many_failures_truncated(self):
        """Test that only first 5 failures are shown inline."""
        failures = [{"task_id": f"task_{i}", "error": f"Error {i}"} for i in range(10)]
        payload = OrchestrationPayload(
            channel="email",
            message_type="orchestration_complete",
            event_type="ORCHESTRATION_COMPLETE_MIXED",
            batch_id="batch_many",
            task_count=10,
            success_count=0,
            failed_tasks=failures,
            text="All tasks failed",
            voice_attachment_path=None,
            timestamp=time.time(),
            metadata={},
        )

        html = _generate_html_body(payload)

        assert "task_0" in html
        assert "task_4" in html  # First 5
        assert "and 5 more failure(s)" in html  # Remaining count


class TestHandleOrchestrationComplete:
    """Test email send functionality."""

    @patch("orchestration_handler.smtplib.SMTP")
    def test_send_email_success(self, mock_smtp_class):
        """Test successful email send."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        payload = {
            "channel": "email",
            "message_type": "orchestration_complete",
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "batch_id": "batch_001",
            "task_count": 3,
            "success_count": 3,
            "failed_tasks": [],
            "text": "✅ 3 tasks completed",
            "voice_attachment_path": None,
            "timestamp": time.time(),
            "metadata": {},
        }

        result = handle_orchestration_complete(
            payload=payload,
            user_email="user@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="password",
            smtp_use_tls=True,
            from_address="bot@example.com",
        )

        assert result is True
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("bot@example.com", "password")
        mock_smtp.sendmail.assert_called_once()
        mock_smtp.quit.assert_called_once()

    @patch("orchestration_handler.smtplib.SMTP")
    def test_send_email_with_attachment(self, mock_smtp_class):
        """Test email with voice attachment."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        # Create temporary audio file
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(b"fake ogg audio data")
            audio_path = tmp.name

        try:
            payload = {
                "channel": "email",
                "message_type": "orchestration_complete",
                "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
                "batch_id": "batch_audio",
                "task_count": 2,
                "success_count": 2,
                "failed_tasks": [],
                "text": "Tasks completed with audio summary",
                "voice_attachment_path": audio_path,
                "timestamp": time.time(),
                "metadata": {},
            }

            result = handle_orchestration_complete(
                payload=payload,
                user_email="user@example.com",
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_user="bot@example.com",
                smtp_password="password",
            )

            assert result is True

            # Verify sendmail was called
            call_args = mock_smtp.sendmail.call_args
            assert call_args is not None
            email_body = call_args[0][2]  # Third argument is the message

            # Check that attachment is in message
            assert "Content-Type: audio/ogg" in email_body or "audio/ogg" in email_body.lower()

        finally:
            Path(audio_path).unlink(missing_ok=True)

    def test_invalid_recipient(self):
        """Test rejection of invalid email address."""
        payload = {
            "channel": "email",
            "message_type": "orchestration_complete",
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "batch_id": "batch_bad",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "text": "Task completed",
            "voice_attachment_path": None,
            "timestamp": time.time(),
            "metadata": {},
        }

        result = handle_orchestration_complete(
            payload=payload,
            user_email="not-an-email",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="password",
        )

        assert result is False

    def test_missing_smtp_credentials(self):
        """Test rejection when SMTP credentials are missing."""
        payload = {
            "channel": "email",
            "message_type": "orchestration_complete",
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "batch_id": "batch_cred",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "text": "Task completed",
            "voice_attachment_path": None,
            "timestamp": time.time(),
            "metadata": {},
        }

        result = handle_orchestration_complete(
            payload=payload,
            user_email="user@example.com",
            smtp_host="",  # Missing
            smtp_port=587,
            smtp_user="",  # Missing
            smtp_password="",
        )

        assert result is False

    @patch("orchestration_handler.smtplib.SMTP")
    def test_smtp_auth_failure(self, mock_smtp_class):
        """Test handling of SMTP authentication failure."""
        mock_smtp = MagicMock()
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(
            535, "Authentication failed"
        )
        mock_smtp_class.return_value = mock_smtp

        payload = {
            "channel": "email",
            "message_type": "orchestration_complete",
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "batch_id": "batch_auth",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "text": "Task completed",
            "voice_attachment_path": None,
            "timestamp": time.time(),
            "metadata": {},
        }

        result = handle_orchestration_complete(
            payload=payload,
            user_email="user@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="wrong_password",
        )

        assert result is False

    @patch("orchestration_handler.smtplib.SMTP")
    def test_attachment_missing_graceful_fallback(self, mock_smtp_class):
        """Test graceful fallback when voice attachment file is missing."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        payload = {
            "channel": "email",
            "message_type": "orchestration_complete",
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "batch_id": "batch_missing",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "text": "Task completed",
            "voice_attachment_path": "/nonexistent/file.ogg",  # File doesn't exist
            "timestamp": time.time(),
            "metadata": {},
        }

        result = handle_orchestration_complete(
            payload=payload,
            user_email="user@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="password",
        )

        # Should still succeed (text-only email)
        assert result is True
        mock_smtp.sendmail.assert_called_once()

    @patch("orchestration_handler.smtplib.SMTP")
    def test_network_error_handling(self, mock_smtp_class):
        """Test handling of network errors."""
        mock_smtp_class.side_effect = OSError("Connection refused")

        payload = {
            "channel": "email",
            "message_type": "orchestration_complete",
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "batch_id": "batch_net",
            "task_count": 1,
            "success_count": 1,
            "failed_tasks": [],
            "text": "Task completed",
            "voice_attachment_path": None,
            "timestamp": time.time(),
            "metadata": {},
        }

        result = handle_orchestration_complete(
            payload=payload,
            user_email="user@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="bot@example.com",
            smtp_password="password",
        )

        assert result is False


class TestProcessOrchestrationOutbox:
    """Test outbox polling functionality."""

    @patch("orchestration_handler.smtplib.SMTP")
    def test_process_outbox(self, mock_smtp_class):
        """Test processing of orchestration_*.json files from outbox."""
        mock_smtp = MagicMock()
        mock_smtp_class.return_value = mock_smtp

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test outbox file
            outbox_file = Path(tmpdir) / "orchestration_email_1234_test.json"
            payload = {
                "channel": "email",
                "message_type": "orchestration_complete",
                "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
                "batch_id": "batch_outbox",
                "task_count": 2,
                "success_count": 2,
                "failed_tasks": [],
                "text": "Outbox test",
                "voice_attachment_path": None,
                "timestamp": time.time(),
                "metadata": {},
                "chat_id": "user@example.com",
            }
            outbox_file.write_text(json.dumps(payload))

            smtp_config = {
                "host": "smtp.example.com",
                "port": 587,
                "user": "bot@example.com",
                "password": "password",
                "use_tls": True,
                "from_address": "bot@example.com",
            }

            results = process_orchestration_outbox(str(tmpdir), smtp_config)

            assert len(results) == 1
            assert list(results.values())[0] is True
            # File should be deleted after successful send
            assert not outbox_file.exists()

    def test_process_outbox_no_directory(self):
        """Test graceful handling when outbox directory doesn't exist."""
        results = process_orchestration_outbox("/nonexistent/path", {})

        assert results == {}

    def test_process_outbox_wrong_channel(self):
        """Test that non-email files are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a Discord orchestration file (should be skipped)
            outbox_file = Path(tmpdir) / "orchestration_discord_1234_test.json"
            payload = {
                "channel": "discord",
                "message_type": "orchestration_complete",
                "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
                "batch_id": "batch_discord",
                "task_count": 1,
                "success_count": 1,
                "failed_tasks": [],
                "text": "Discord test",
                "voice_attachment_path": None,
                "timestamp": time.time(),
                "metadata": {},
                "chat_id": "123456789",
            }
            outbox_file.write_text(json.dumps(payload))

            results = process_orchestration_outbox(str(tmpdir), {})

            # Should not process Discord files
            assert len(results) == 0
            # File should still exist
            assert outbox_file.exists()

    def test_process_outbox_malformed_json(self):
        """Test handling of malformed JSON files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a malformed JSON file
            outbox_file = Path(tmpdir) / "orchestration_email_1234_bad.json"
            outbox_file.write_text("{invalid json}")

            results = process_orchestration_outbox(str(tmpdir), {})

            assert len(results) == 1
            assert list(results.values())[0] is False
            # File should still exist (not deleted on error)
            assert outbox_file.exists()

    def test_process_outbox_missing_recipient(self):
        """Test handling when recipient email is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create file without chat_id or to field
            outbox_file = Path(tmpdir) / "orchestration_email_1234_norcp.json"
            payload = {
                "channel": "email",
                "message_type": "orchestration_complete",
                "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
                "batch_id": "batch_norcp",
                "task_count": 1,
                "success_count": 1,
                "failed_tasks": [],
                "text": "No recipient test",
                "voice_attachment_path": None,
                "timestamp": time.time(),
                "metadata": {},
                # Missing chat_id and to fields
            }
            outbox_file.write_text(json.dumps(payload))

            results = process_orchestration_outbox(str(tmpdir), {})

            assert len(results) == 1
            assert list(results.values())[0] is False
            # File should still exist
            assert outbox_file.exists()


class TestIntegrationWithOrchestrationAggregator:
    """Integration tests with orchestration_router.py payload format."""

    def test_handle_router_payload_format(self):
        """Test that handler accepts payload from orchestration_router.py."""
        # This simulates a payload from orchestration_router.route_event()
        router_payload = {
            "channel": "email",
            "message_type": "orchestration_complete",
            "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
            "batch_id": "batch_from_router",
            "task_count": 5,
            "success_count": 5,
            "failed_tasks": [],
            "text": "✅ 5 background tasks completed successfully in 2m 23s",
            "voice_attachment_path": None,
            "timestamp": time.time(),
            "metadata": {"console_url": "http://localhost:8765/console"},
        }

        # Should parse without error
        orch = OrchestrationPayload(
            channel=router_payload.get("channel", "email"),
            message_type=router_payload.get("message_type", "orchestration_complete"),
            event_type=router_payload.get("event_type", "ORCHESTRATION_COMPLETE_SUCCESS"),
            batch_id=router_payload.get("batch_id", "unknown"),
            task_count=router_payload.get("task_count", 0),
            success_count=router_payload.get("success_count", 0),
            failed_tasks=router_payload.get("failed_tasks", []),
            text=router_payload.get("text", ""),
            voice_attachment_path=router_payload.get("voice_attachment_path"),
            timestamp=router_payload.get("timestamp", time.time()),
            metadata=router_payload.get("metadata", {}),
        )

        assert orch.batch_id == "batch_from_router"
        assert orch.event_type == "ORCHESTRATION_COMPLETE_SUCCESS"
        assert orch.success_count == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
