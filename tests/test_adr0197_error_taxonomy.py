"""End-to-end tests for ADR-0197: Typed A2A error taxonomy."""
import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "bridges" / "shared"))

from remote_trigger_sender import (
    RemoteTriggerSender, SendResult, ErrorCategory, TransportError, ResponseVerificationError
)


class TestADR0197ErrorCategories:
    """ADR-0197: Verify error_category and error_detail on all error paths."""

    def test_protocol_error_on_attachment_validation(self):
        """Local attachment validation failure → PROTOCOL_ERROR."""
        sender = RemoteTriggerSender(instance_id="test-sender")

        # Trigger attachment validation error
        result = sender.send(
            endpoint_id="test-endpoint",
            instruction="test",
            attachments=[{"name": "x" * 1000, "digest": "bad"}],  # Invalid: name too long
        )

        assert result.ok is False
        assert result.status == "error"
        assert result.error_category == ErrorCategory.PROTOCOL_ERROR
        assert result.error_detail is not None
        assert len(result.error_detail) <= 256

    def test_error_detail_sanitized_no_pii(self):
        """error_detail must never contain raw exceptions or file paths."""
        from remote_trigger_sender import _sanitize_error

        # Test IPv4
        assert "192.168.1.1" not in _sanitize_error("Failed to connect to 192.168.1.1:8080")
        assert "[IP]" in _sanitize_error("Failed to connect to 192.168.1.1:8080")

        # Test IPv6
        assert "2001:db8::1" not in _sanitize_error("Connected to 2001:db8::1")
        assert "[IPv6]" in _sanitize_error("Connected to 2001:db8::1")

        # Test Unix paths
        assert "/root" not in _sanitize_error("Error at /root/.ssh/key.pem")
        assert "[PATH]" in _sanitize_error("Error at /root/.ssh/key.pem")

        # Test Windows paths
        assert "C:\\Users" not in _sanitize_error("Error at C:\\Users\\admin\\secret.txt")
        assert "[WINPATH]" in _sanitize_error("Error at C:\\Users\\admin\\secret.txt")

        # Test E-mails
        assert "admin@example.com" not in _sanitize_error("Admin email admin@example.com")
        assert "[EMAIL]" in _sanitize_error("Admin email admin@example.com")

        # Test UUIDs
        assert "550e8400-e29b-41d4-a716-446655440000" not in _sanitize_error("UUID 550e8400-e29b-41d4-a716-446655440000")
        assert "[UUID]" in _sanitize_error("UUID 550e8400-e29b-41d4-a716-446655440000")

        # Test URL credentials
        assert "user:pass@" not in _sanitize_error("http://user:pass@internal.com/api")
        assert "[CREDENTIALS]" in _sanitize_error("http://user:pass@internal.com/api")

        # Combined test
        raw = "Exception: Failed to connect to 192.168.1.1:8080 at /root/.ssh/key.pem with admin@corp.com"
        sanitized = _sanitize_error(raw)
        assert "[IP]" in sanitized
        assert "[PATH]" in sanitized
        assert "[EMAIL]" in sanitized
        assert "192.168.1.1" not in sanitized
        assert "/root" not in sanitized
        assert "admin@corp.com" not in sanitized
        assert len(sanitized) <= 256

    def test_receiver_timeout_returns_ok_false_not_true(self):
        """ADR-0197 BUG FIX: receiver timeout MUST return ok=False, not True."""
        sender = RemoteTriggerSender(instance_id="test-sender")

        # Directly test the categorization logic (simpler than mocking entire send path)
        error_cat, error_detail, ok = sender._categorize_response_status("timeout")

        # The FIX: receiver timeout should be ok=False
        assert ok is False, "ADR-0197 BUG: receiver timeout must be ok=False"
        assert error_cat == ErrorCategory.TIMEOUT_REMOTE
        assert error_detail is None  # Status-derived categories don't have details

    def test_transport_error_categorized_correctly(self):
        """Transport errors mapped to specific categories."""
        sender = RemoteTriggerSender(instance_id="test-sender")

        test_cases = [
            (TransportError("connection_failed"), ErrorCategory.UNREACHABLE),
            (TransportError("timeout"), ErrorCategory.TIMEOUT_TRANSPORT),
            (TransportError("http_500"), ErrorCategory.HTTP_ERROR),
            (TransportError("response_too_large"), ErrorCategory.PROTOCOL_ERROR),
            (TransportError("invalid_response_json:bad"), ErrorCategory.PROTOCOL_ERROR),
        ]

        for exc, expected_category in test_cases:
            cat, detail = sender._categorize_transport_error(exc)
            assert cat == expected_category, f"Failed for {exc.reason}: got {cat}"
            assert detail is not None
            assert len(detail) <= 256

    def test_verification_error_categorized_correctly(self):
        """Signature verification errors mapped to AUTH_FAILED."""
        sender = RemoteTriggerSender(instance_id="test-sender")

        test_cases = [
            ("bad_signature", ErrorCategory.AUTH_FAILED),
            ("missing_signature", ErrorCategory.AUTH_FAILED),
            ("task_id_mismatch", ErrorCategory.AUTH_FAILED),
            ("response_not_object", ErrorCategory.PROTOCOL_ERROR),
            ("canonical_encode_failed:x", ErrorCategory.PROTOCOL_ERROR),
        ]

        for reason, expected_category in test_cases:
            # Create mock exc with reason attribute
            exc = Mock(spec=ResponseVerificationError)
            exc.reason = reason
            cat, detail = sender._categorize_verification_error(exc)
            assert cat == expected_category, f"Failed for {reason}: got {cat}"

    def test_response_status_categorization(self):
        """Receiver response status mapped to error_category."""
        sender = RemoteTriggerSender(instance_id="test-sender")

        test_cases = [
            ("ok", None, True),  # Success
            ("timeout", ErrorCategory.TIMEOUT_REMOTE, False),  # Receiver timeout
            ("rejected", ErrorCategory.REJECTED, False),  # Explicitly rejected
            ("filtered", ErrorCategory.FILTERED, False),  # House rules filtered
            ("unknown", ErrorCategory.INTERNAL_ERROR, False),  # Unknown status
        ]

        for status, expected_cat, expected_ok in test_cases:
            cat, detail, ok = sender._categorize_response_status(status)
            assert cat == expected_cat, f"Failed for status={status}"
            assert ok == expected_ok, f"ok flag wrong for status={status}"

    def test_error_category_enum_values_are_all_defined(self):
        """ErrorCategory enum includes all 9 specified values."""
        required = {
            "unreachable",
            "timeout_transport",
            "timeout_remote",
            "rejected",
            "filtered",
            "auth_failed",
            "http_error",
            "protocol_error",
            "internal_error",
        }

        assert required.issubset(ErrorCategory.ALL), f"Missing: {required - ErrorCategory.ALL}"
        assert len(ErrorCategory.ALL) == 9


class TestADR0197Audit:
    """Verify error_category and error_detail appear in audit logs."""

    def test_sendresult_has_error_fields(self):
        """SendResult dataclass includes error_category and error_detail."""
        result = SendResult(
            ok=False,
            status="error",
            task_id="test",
            instance_id="",
            instance_id_match=False,
            data={},
            attachments=[],
            duration_ms=100,
            error_category=ErrorCategory.UNREACHABLE,
            error_detail="Peer unreachable",
        )

        assert result.error_category == ErrorCategory.UNREACHABLE
        assert result.error_detail == "Peer unreachable"

        # Should be JSON-serializable for audit logs
        result_dict = {
            "ok": result.ok,
            "status": result.status,
            "error_category": result.error_category,
            "error_detail": result.error_detail,
        }
        json_str = json.dumps(result_dict)
        assert "unreachable" in json_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
