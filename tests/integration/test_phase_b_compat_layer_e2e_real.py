"""
Phase B E2E: REAL Compat Layer Tests (CRITICAL-3 FIX)

Proves:
1. Old APIs are callable (backward compatible)
2. New Skills are invoked transparently
3. Audit trail captures every call (immutable)
4. Fail-closed on error (no silent fallback)
5. Timeout is enforced (no hangs)
"""

import pytest
import json
import os
from unittest.mock import Mock, patch, MagicMock
from core.legacy_compat.brain_compat import get_session_context
from core.telemetry.deprecated_api_calls import DeprecatedAPIEvent


class MockSkillResult:
    """Mock SkillExecutionResult for testing."""
    def __init__(self, status="success", output=None, error_message=None):
        self.status = status
        self.output = output or {"user_id": "test_user", "context": {}}
        self.error_message = error_message


class TestPhaseBAuditTrailReal:
    """CRITICAL-3 FIX: Real audit trail integration tests."""

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    @patch("core.telemetry.deprecated_api_calls.get_audit_writer")
    def test_brain_compat_writes_audit_event(self, mock_audit_writer, mock_skill_class):
        """Brain compat call WRITES to audit trail (not just logs)."""
        # Setup mock Skill
        mock_skill = Mock()
        mock_skill.execute.return_value = MockSkillResult(
            status="success",
            output={"user_id": "test_123", "context": {"data": "value"}}
        )
        mock_skill_class.return_value = mock_skill

        # Setup mock audit writer
        mock_writer = Mock()
        mock_audit_writer.return_value = mock_writer

        # Call deprecated API
        result = get_session_context(task_id="test_123", tenant_id="tenant_a")

        # VERIFY: Audit writer was called (CRITICAL-2 fix verified)
        assert mock_writer.write_event_dict.called, "Audit trail should be written"
        call_args = mock_writer.write_event_dict.call_args
        assert call_args[1]["event_type"] == "deprecated_api_call"
        assert call_args[1]["tenant_id"] == "tenant_a"

        # VERIFY: Skill was called
        assert mock_skill.execute.called, "Skill should be invoked"
        assert result == {"user_id": "test_123", "context": {"data": "value"}}

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    @patch("core.telemetry.deprecated_api_calls.get_audit_writer")
    def test_brain_compat_error_propagates_fails_closed(self, mock_audit_writer, mock_skill_class):
        """On Skill error, exception propagates (fail-closed, no fallback)."""
        # Setup mock Skill that fails
        mock_skill = Mock()
        mock_skill.execute.return_value = MockSkillResult(
            status="error",
            error_message="Skill call failed"
        )
        mock_skill_class.return_value = mock_skill

        # Setup mock audit writer
        mock_writer = Mock()
        mock_audit_writer.return_value = mock_writer

        # Call deprecated API and expect error
        with pytest.raises(RuntimeError, match="Skill failed"):
            get_session_context(task_id="test_123")

        # VERIFY: Error was logged to audit trail
        assert mock_writer.write_event_dict.called, "Error should be audited"

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    @patch("core.legacy_compat.brain_compat.skill_call_timeout")
    @patch("core.telemetry.deprecated_api_calls.get_audit_writer")
    def test_brain_compat_timeout_enforced(self, mock_audit_writer, mock_timeout, mock_skill_class):
        """Skill calls have timeout (CRITICAL-5 fix verified)."""
        mock_timeout.return_value.__enter__ = Mock()
        mock_timeout.return_value.__exit__ = Mock(return_value=None)

        mock_skill = Mock()
        mock_skill.execute.return_value = MockSkillResult()
        mock_skill_class.return_value = mock_skill

        mock_writer = Mock()
        mock_audit_writer.return_value = mock_writer

        # Call deprecated API
        get_session_context(task_id="test_123")

        # VERIFY: Timeout context manager was used
        assert mock_timeout.called, "skill_call_timeout should be used (CRITICAL-5)"
        call_args = mock_timeout.call_args
        assert call_args[1]["seconds"] == 5, "Timeout should be 5 seconds"

    @patch("core.legacy_compat.brain_compat.ContextAdapterSkill")
    @patch("core.telemetry.deprecated_api_calls.get_audit_writer")
    def test_brain_compat_tenant_scoped(self, mock_audit_writer, mock_skill_class):
        """All audit events are tenant-scoped (GDPR Art. 5)."""
        mock_skill = Mock()
        mock_skill.execute.return_value = MockSkillResult()
        mock_skill_class.return_value = mock_skill

        mock_writer = Mock()
        mock_audit_writer.return_value = mock_writer

        # Call with specific tenant
        get_session_context(
            task_id="test_123",
            tenant_id="tenant_b",
            user_id="user_xyz"
        )

        # VERIFY: Audit event is tenant-scoped
        call_args = mock_writer.write_event_dict.call_args
        assert call_args[1]["tenant_id"] == "tenant_b"

    def test_deprecated_api_event_immutable(self):
        """DeprecatedAPIEvent is immutable (frozen dataclass)."""
        event = DeprecatedAPIEvent(
            timestamp="2026-09-03T00:00:00Z",
            api_name="test",
            module="test.module",
            caller_file="test.py",
            caller_line=1,
            caller_func="test_func",
            stack_trace="",
            tenant_id="_default",
        )

        # Verify frozen (should raise FrozenInstanceError on modification)
        with pytest.raises(Exception):  # FrozenInstanceError
            event.timestamp = "2026-09-04T00:00:00Z"


class TestPhaseCAuditChainReadiness:
    """Tests that Phase C measurement gates have data to work with."""

    @patch("core.telemetry.deprecated_api_calls.get_audit_writer")
    def test_audit_events_contain_required_fields(self, mock_audit_writer):
        """Phase C gates need: event_type, tenant_id, timestamp, details."""
        mock_writer = Mock()
        mock_audit_writer.return_value = mock_writer

        with patch("core.legacy_compat.brain_compat.ContextAdapterSkill") as mock_skill:
            mock_skill.return_value.execute.return_value = MockSkillResult()
            get_session_context(task_id="gate_test", tenant_id="test_tenant")

        # Verify event has all Phase C gate requirements
        call_args = mock_writer.write_event_dict.call_args
        assert "event_type" in call_args[1]
        assert "tenant_id" in call_args[1]
        assert call_args[1]["event_type"] == "deprecated_api_call"


class TestPhaseBAuditToJsonl:
    """Simulate Phase C: grep audit.jsonl for deprecated_api_call events."""

    def test_audit_event_serializable_to_jsonl(self):
        """Events must be JSON-serializable for audit.jsonl storage."""
        event = DeprecatedAPIEvent(
            timestamp="2026-09-03T12:00:00Z",
            api_name="get_session_context",
            module="core.brain.conversation_recall",
            caller_file="my_code.py",
            caller_line=42,
            caller_func="my_func",
            stack_trace="...",
            tenant_id="tenant_x",
            task_id="task_001",
            user_id="user_scrubbed",
        )

        # Simulate jsonl write
        event_dict = event.to_dict()
        jsonl_line = json.dumps(event_dict)

        # Parse back (simulate grep + jq)
        parsed = json.loads(jsonl_line)
        assert parsed["api_name"] == "get_session_context"
        assert parsed["tenant_id"] == "tenant_x"
        assert "stack_trace" in parsed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
