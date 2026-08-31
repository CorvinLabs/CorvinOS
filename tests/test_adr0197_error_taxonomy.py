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

    def test_error_detail_is_template_gated(self):
        """ADR-0197 §2 (2026-07-19 HIGH fix): error_detail comes from a FIXED
        TEMPLATE SET — never str(exc). The old denylist regex scrubbing leaked
        sk-ant tokens, Bearer JWTs, Discord UIDs, hostnames and hex keys."""
        from remote_trigger_sender import (
            _sanitize_error, _ERROR_DETAIL_TEMPLATES, _ALLOWED_EXC_TYPE_NAMES,
        )

        # Known templates pass through unchanged.
        assert _sanitize_error("HTTP error") == "HTTP error"
        assert _sanitize_error("Instance ID mismatch") == "Instance ID mismatch"
        # Allowlisted exception type names pass through.
        assert _sanitize_error("OSError") == "OSError"
        # None stays None.
        assert _sanitize_error(None) is None
        # ANY free-form input collapses to the generic template.
        for raw in (
            "Failed to connect to 192.168.1.1:8080",
            "Error at /root/.ssh/key.pem",
            "Admin email admin@example.com",
            "Exception: something exploded at C:\\Users\\admin",
        ):
            out = _sanitize_error(raw)
            assert out in _ERROR_DETAIL_TEMPLATES or out in _ALLOWED_EXC_TYPE_NAMES
            assert out == "error_detail_unavailable"

    # The concrete secrets the 2026-07-19 adversarial review pushed through
    # the old denylist scrubber — every one leaked verbatim back then.
    _SECRETS = [
        "sk-ant-api03-AbCdEfGh1234567890",                       # API token
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",      # JWT
        "123456789012345678",                                     # Discord UID
        "myhost.fritz.box:8443",                                  # LAN hostname
        "d" * 64,                                                 # 64-char hex key
    ]

    def test_secrets_never_reach_error_detail(self):
        """Feed tokens/JWTs/UIDs/hostnames/keys through every taxonomy entry
        point — none may surface in error_detail."""
        from remote_trigger_sender import _sanitize_error
        sender = RemoteTriggerSender(instance_id="test-sender")

        for secret in self._SECRETS:
            # 1) template gate itself
            out = _sanitize_error(f"connect to {secret} failed")
            assert out is not None and secret not in out

            # 2) transport catch-all (free-form reason from a lower layer)
            cat, detail = sender._categorize_transport_error(
                TransportError(f"transport_error:{secret}")
            )
            assert cat == ErrorCategory.INTERNAL_ERROR
            assert detail is not None and secret not in detail

            # 3) verification catch-all
            exc = ResponseVerificationError(f"weird:{secret}")
            cat, detail = sender._categorize_verification_error(exc)
            assert cat == ErrorCategory.INTERNAL_ERROR
            assert detail is not None and secret not in detail

            # 4) peer-controlled response status (audit-injection primitive)
            cat, detail, ok = sender._categorize_response_status(secret)
            assert ok is False
            assert detail == "unexpected_receiver_status"
            assert secret not in (detail or "")

    def test_catch_all_emits_allowlisted_type_name_only(self):
        """ADR-0197 §2: catch-alls emit type(exc).__name__, allowlist-validated;
        unknown types collapse to 'internal_error'."""
        from remote_trigger_sender import _safe_exc_type_name

        class EvilCustomError(Exception):
            pass

        assert _safe_exc_type_name(OSError()) == "OSError"
        assert _safe_exc_type_name(TransportError("x")) == "TransportError"
        assert _safe_exc_type_name(EvilCustomError()) == "internal_error"
        assert _safe_exc_type_name("NotARealExceptionName") == "internal_error"

    def test_audit_backstop_redacts_free_form_values(self):
        """Fix #4: _assert_audit_details_safe drops free-form values (fail-closed
        backstop analogous to telemetry's _assert_safe) without raising."""
        from remote_trigger_sender import _assert_audit_details_safe

        details = {
            "endpoint_id": "kid-1234",
            "task_id": "550e8400-e29b-41d4-a716-446655440000",
            "duration_ms": 42,
            "reachable": True,
            "error_category": "unreachable",
            "reason": "connect to myhost.fritz.box:8443 failed",   # free-form
            "error_detail": "sk-ant-api03-AbCdEf",                 # not a template
            "peer_hostname": "myhost.fritz.box",                   # unlisted key
        }
        safe = _assert_audit_details_safe(details)
        blob = json.dumps(safe)
        assert "fritz.box" not in blob
        assert "sk-ant" not in blob
        assert safe["endpoint_id"] == "kid-1234"
        assert safe["task_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert safe["duration_ms"] == 42
        assert safe["reachable"] is True
        assert safe["error_category"] == "unreachable"
        assert safe["reason"] == "redacted"
        assert safe["error_detail"] == "redacted"
        assert safe["peer_hostname"] == "redacted"

        # Legit closed-enum reasons survive untouched.
        ok = _assert_audit_details_safe({
            "reason": "missing_fields:hmac_key,recv_key",
            "status": "error",
            "http_status": 404,
        })
        assert ok["reason"] == "missing_fields:hmac_key,recv_key"
        assert ok["status"] == "error"
        assert ok["http_status"] == 404

        # Digit-only (Discord UID shape) and 64-hex reasons are redacted.
        assert _assert_audit_details_safe({"reason": "123456789012345678"})["reason"] == "redacted"
        assert _assert_audit_details_safe({"reason": "d" * 64})["reason"] == "redacted"

    def test_send_audit_details_carry_no_free_form_on_transport_error(self, tmp_path, monkeypatch):
        """E2E: a poisoned free-form TransportError reason (simulating an old
        or buggy lower layer) never reaches the audited details or the
        SendResult (fix #1/#3/#4)."""
        import os as _os
        from unittest.mock import MagicMock
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
        monkeypatch.delenv("REMOTE_ENDPOINTS_DIR", raising=False)

        endpoints = tmp_path / "endpoints"
        endpoints.mkdir()
        cfg_path = endpoints / "test-endpoint.json"
        cfg_path.write_text(json.dumps({
            "endpoint_id": "test-endpoint",
            "url": "http://peer.example:8443/v1/a2a/receive",
            "hmac_key": "aa" * 32,
            "recv_key": "bb" * 32,
            "instance_id": "",
            "enabled": True,
            "default_ttl_s": 60,
        }))
        _os.chmod(cfg_path, 0o600)

        se = MagicMock()
        se.write_event = MagicMock(return_value={"hash": "x"})
        sender = RemoteTriggerSender(
            endpoints, instance_id="test-sender", forge_se=se,
        )
        poison = "transport_error:sk-ant-secret at myhost.fritz.box:8443"

        def _poisoned_post(url, envelope, timeout_s):
            raise TransportError(poison)

        monkeypatch.setattr(sender, "_http_post", _poisoned_post)
        result = sender.send("test-endpoint", "hello")

        assert result.ok is False
        assert result.error_category == ErrorCategory.INTERNAL_ERROR
        assert "sk-ant" not in (result.error_detail or "")
        assert "fritz.box" not in (result.error_detail or "")

        for call in se.write_event.call_args_list:
            blob = json.dumps(call.kwargs.get("details", {}), default=str)
            assert "sk-ant" not in blob, f"audit leak in {call.args[1]}: {blob}"
            assert "fritz.box" not in blob, f"audit leak in {call.args[1]}: {blob}"

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
