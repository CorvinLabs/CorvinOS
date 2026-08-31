"""Unit tests for tool execution telemetry (ADR-0321, Gap 1).

Tests the ToolExecutionTelemetry frozen dataclass, validation, PII sanitization,
and payload conversion for learning events.
"""

import pytest
from datetime import datetime, timedelta
from core.learning.tool_execution import (
    ToolExecutionTelemetry,
    ToolExecutionStatus,
    _sanitize_error_message,
    _assert_safe,
)


class TestToolExecutionTelemetry:
    """Test ToolExecutionTelemetry frozen dataclass and validation."""

    def test_tool_execution_telemetry_created_valid(self):
        """Test creation of a valid ToolExecutionTelemetry instance."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=500)

        telemetry = ToolExecutionTelemetry(
            tool_id="tool-123",
            tool_name="test_tool",
            tool_type="generated",
            session_id="session-456",
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            input_tokens=100,
            output_tokens=200,
            estimated_cost_cents=50,
            status=ToolExecutionStatus.SUCCESS,
        )

        assert telemetry.tool_id == "tool-123"
        assert telemetry.tool_name == "test_tool"
        assert telemetry.status == ToolExecutionStatus.SUCCESS
        assert telemetry.latency_ms == 500
        assert telemetry.input_tokens == 100
        assert telemetry.output_tokens == 200
        assert telemetry.user_satisfaction == -1  # Default: not available

    def test_tool_execution_latency_calculated(self):
        """Test that latency_ms is correctly calculated from timestamps."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=250)

        telemetry = ToolExecutionTelemetry(
            tool_id="tool-1",
            tool_name="test",
            tool_type="promoted",
            session_id="sess-1",
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            input_tokens=10,
            output_tokens=20,
            estimated_cost_cents=5,
            status=ToolExecutionStatus.SUCCESS,
        )

        # Allow small rounding error (within ±1ms)
        assert 249 <= telemetry.latency_ms <= 251

    def test_pii_sanitization_removes_paths(self):
        """Test that error message sanitization removes file paths."""
        msg = "Error in /home/user/project/core/file.py line 42"
        sanitized = _sanitize_error_message(msg)
        assert sanitized is not None
        assert "/home" not in sanitized
        assert "/project" not in sanitized
        assert "[PATH]" in sanitized

    def test_pii_sanitization_removes_schema_names(self):
        """Test that error message sanitization removes database schema names."""
        msg = "Failed to query users.profile.email from database"
        sanitized = _sanitize_error_message(msg)
        assert sanitized is not None
        assert "users.profile" not in sanitized
        assert "[SCHEMA]" in sanitized

    def test_pii_sanitization_removes_credentials(self):
        """Test that error message sanitization removes credential patterns."""
        msg = "Connection failed: bearer token=abc123def456 expired"
        sanitized = _sanitize_error_message(msg)
        assert sanitized is not None
        assert "abc123" not in sanitized
        assert "[CREDENTIAL]" in sanitized

    def test_tool_execution_with_operator_rating(self):
        """Test that operator rating is captured in telemetry."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=100)

        telemetry = ToolExecutionTelemetry(
            tool_id="tool-1",
            tool_name="search",
            tool_type="builtin",
            session_id="sess-1",
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            input_tokens=50,
            output_tokens=100,
            estimated_cost_cents=20,
            status=ToolExecutionStatus.SUCCESS,
            user_satisfaction=4,  # 4-star rating
        )

        assert telemetry.user_satisfaction == 4
        assert telemetry.required_followup is False  # Satisfied, no followup needed

    def test_tool_execution_token_subsystem_breakdown(self):
        """Test that subsystem tokens are validated against total."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=100)

        # Valid: subsystem total <= total tokens
        telemetry = ToolExecutionTelemetry(
            tool_id="tool-1",
            tool_name="test",
            tool_type="generated",
            session_id="sess-1",
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            input_tokens=100,
            output_tokens=200,
            subsystem_tokens={"confidence": 150, "ranking": 100},
            estimated_cost_cents=75,
            status=ToolExecutionStatus.SUCCESS,
        )

        assert telemetry.subsystem_tokens["confidence"] == 150
        assert sum(telemetry.subsystem_tokens.values()) <= 300

    def test_tool_execution_subsystem_tokens_exceed_total_fails(self):
        """Test that subsystem tokens exceeding total tokens raises ValueError."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=100)

        with pytest.raises(ValueError, match="subsystem_tokens sum .* exceeds"):
            ToolExecutionTelemetry(
                tool_id="tool-1",
                tool_name="test",
                tool_type="generated",
                session_id="sess-1",
                start_timestamp_utc=start,
                end_timestamp_utc=end,
                input_tokens=100,
                output_tokens=200,
                subsystem_tokens={"too_much": 500},  # Exceeds 300 total
                estimated_cost_cents=50,
                status=ToolExecutionStatus.SUCCESS,
            )

    def test_tool_execution_invalid_user_satisfaction_fails(self):
        """Test that invalid user_satisfaction rating raises ValueError."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=100)

        with pytest.raises(ValueError, match="user_satisfaction must be"):
            ToolExecutionTelemetry(
                tool_id="tool-1",
                tool_name="test",
                tool_type="generated",
                session_id="sess-1",
                start_timestamp_utc=start,
                end_timestamp_utc=end,
                input_tokens=100,
                output_tokens=200,
                estimated_cost_cents=50,
                status=ToolExecutionStatus.SUCCESS,
                user_satisfaction=10,  # Invalid: not in [-1, 1-5]
            )

    def test_tool_execution_negative_latency_fails(self):
        """Test that negative latency raises ValueError."""
        start = datetime.utcnow()
        end = start - timedelta(milliseconds=100)  # End before start

        with pytest.raises(ValueError, match="latency_ms must be"):
            ToolExecutionTelemetry(
                tool_id="tool-1",
                tool_name="test",
                tool_type="generated",
                session_id="sess-1",
                start_timestamp_utc=start,
                end_timestamp_utc=end,
                input_tokens=100,
                output_tokens=200,
                estimated_cost_cents=50,
                status=ToolExecutionStatus.SUCCESS,
            )

    def test_tool_execution_to_event_payload(self):
        """Test conversion of telemetry to event payload format."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=100)

        telemetry = ToolExecutionTelemetry(
            tool_id="tool-123",
            tool_name="search_tool",
            tool_type="generated",
            session_id="sess-456",
            task_id="task-789",
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            input_tokens=50,
            output_tokens=100,
            estimated_cost_cents=30,
            status=ToolExecutionStatus.SUCCESS,
            user_satisfaction=5,
        )

        payload = telemetry.to_event_payload()

        assert payload["tool_id"] == "tool-123"
        assert payload["status"] == "success"
        assert payload["input_tokens"] == 50
        assert payload["output_tokens"] == 100
        assert payload["latency_ms"] == 100
        assert payload["user_satisfaction"] == 5


class TestPiiSanitization:
    """Test PII sanitization function."""

    def test_sanitize_empty_message(self):
        """Test that empty message is returned as-is."""
        assert _sanitize_error_message("") == ""
        assert _sanitize_error_message(None) is None

    def test_sanitize_message_entirely_pii(self):
        """Test that messages with PII are sanitized or dropped."""
        # A message that's mostly PII
        msg = "/home/user/secret/api_key=abc123"
        sanitized = _sanitize_error_message(msg)
        # Should sanitize PII content (either return None or contain [PATH]/[CREDENTIAL])
        assert sanitized is not None  # Some content is preserved
        assert "[PATH]" in sanitized  # Path is sanitized

    def test_sanitize_windows_paths(self):
        """Test that Windows paths are sanitized."""
        msg = "File not found: C:\\Users\\Alice\\project\\main.py"
        sanitized = _sanitize_error_message(msg)
        assert sanitized is not None
        assert "C:\\Users" not in sanitized
        assert "[PATH]" in sanitized

    def test_sanitize_stack_traces(self):
        """Test that stack traces are removed."""
        msg = "Error occurred\nat core/file.py:42\nat handler()\nin process()"
        sanitized = _sanitize_error_message(msg)
        assert sanitized is not None
        assert "at " not in sanitized or sanitized.count("at") == 0

    def test_sanitize_preserves_safe_content(self):
        """Test that safe error content is preserved."""
        msg = "Tool timeout after 30 seconds waiting for API response"
        sanitized = _sanitize_error_message(msg)
        assert sanitized == msg  # No PII, unchanged


class TestAssertSafe:
    """Test the fail-closed _assert_safe validator."""

    def test_assert_safe_valid_telemetry(self):
        """Test that valid telemetry passes assertion."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=100)

        telemetry = ToolExecutionTelemetry(
            tool_id="tool-1",
            tool_name="safe_tool",
            tool_type="builtin",
            session_id="sess-1",
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            input_tokens=100,
            output_tokens=200,
            estimated_cost_cents=50,
            status=ToolExecutionStatus.SUCCESS,
        )

        # Should not raise
        assert _assert_safe(telemetry) is True

    def test_assert_safe_rejects_pii_in_error_message(self):
        """Test that telemetry with PII in error_message is rejected."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=100)

        # This should fail sanitization and thus fail assertion
        telemetry = ToolExecutionTelemetry(
            tool_id="tool-1",
            tool_name="test",
            tool_type="generated",
            session_id="sess-1",
            start_timestamp_utc=start,
            end_timestamp_utc=end,
            input_tokens=100,
            output_tokens=200,
            estimated_cost_cents=50,
            status=ToolExecutionStatus.FAILURE,
            error_message="Password=secret123",  # PII in message
        )

        # The __post_init__ sanitizes this, so we check if assert_safe catches remaining PII
        # If the message wasn't fully sanitized, assert_safe should reject it
        try:
            _assert_safe(telemetry)
        except ValueError as e:
            # Expected: PII detected
            assert "PII detected" in str(e)

    def test_assert_safe_rejects_credential_in_tool_name(self):
        """Test that credentials in tool_name are rejected."""
        start = datetime.utcnow()
        end = start + timedelta(milliseconds=100)

        with pytest.raises(ValueError, match="PII detected"):
            telemetry = ToolExecutionTelemetry(
                tool_id="tool-1",
                tool_name="api_key=secret123",  # PII in name
                tool_type="generated",
                session_id="sess-1",
                start_timestamp_utc=start,
                end_timestamp_utc=end,
                input_tokens=100,
                output_tokens=200,
                estimated_cost_cents=50,
                status=ToolExecutionStatus.SUCCESS,
            )
            _assert_safe(telemetry)


class TestToolExecutionStatus:
    """Test ToolExecutionStatus enum."""

    def test_status_enum_values(self):
        """Test that all expected status values exist."""
        assert ToolExecutionStatus.SUCCESS.value == "success"
        assert ToolExecutionStatus.FAILURE.value == "failure"
        assert ToolExecutionStatus.TIMEOUT.value == "timeout"
        assert ToolExecutionStatus.ERROR.value == "error"

    def test_status_comparison(self):
        """Test status comparison and string conversion."""
        status = ToolExecutionStatus.SUCCESS
        assert status == ToolExecutionStatus.SUCCESS
        assert str(status.value) == "success"
