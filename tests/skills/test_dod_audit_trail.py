"""Unit tests for AuditTrailCheck (Definition-of-Done verifier).

Tests moved from production code (audit_trail.py) per PEP 8 compliance.
Tests verify that audit events are recorded in audit.jsonl.
"""

import pytest
from pathlib import Path
from unittest.mock import mock_open, patch

from core.skills.os_skills.definition_of_done_verifier.checks.audit_trail import (
    AuditTrailCheck,
    CheckResult,
)


class TestAuditTrailCheck:
    """Test suite for AuditTrailCheck (fail-closed behavior)."""

    def test_audit_events_found(self):
        """Test: Audit events found for task_id."""
        check = AuditTrailCheck()
        audit_content = (
            '{"task_id": "task_123", "event_type": "skill_executed"}\n'
            '{"task_id": "task_123", "event_type": "feedback_received"}\n'
        )

        with patch("builtins.open", mock_open(read_data=audit_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_123", Path("/audit.jsonl"))
                assert result.passed == True
                assert result.event_count == 2
                assert result.check_name == "audit_trail"

    def test_no_audit_events(self):
        """Test: No events found for task_id (fail-closed)."""
        check = AuditTrailCheck()
        audit_content = (
            '{"task_id": "other_task", "event_type": "skill_executed"}\n'
        )

        with patch("builtins.open", mock_open(read_data=audit_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_123", Path("/audit.jsonl"))
                assert result.passed == False
                assert "no audit events" in result.evidence.lower()
                assert result.event_count == 0

    def test_audit_file_missing(self):
        """Test: Audit file doesn't exist → fail-closed."""
        check = AuditTrailCheck()
        with patch("pathlib.Path.exists", return_value=False):
            result = check.run("task_123", Path("/missing.jsonl"))
            assert result.passed == False
            assert "not found" in result.evidence.lower()

    def test_malformed_json_skipped(self):
        """Test: Malformed JSON lines are skipped, valid ones counted."""
        check = AuditTrailCheck()
        audit_content = (
            '{"task_id": "task_123", "event_type": "skill_executed"}\n'
            'this is not json\n'
            '{"task_id": "task_123", "event_type": "feedback"}\n'
        )

        with patch("builtins.open", mock_open(read_data=audit_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_123", Path("/audit.jsonl"))
                assert result.passed == True
                assert result.event_count == 2  # Malformed line skipped

    def test_empty_task_id(self):
        """Test: Empty task_id → immediate fail."""
        check = AuditTrailCheck()
        result = check.run("", Path("/audit.jsonl"))
        assert result.passed == False
        assert "empty" in result.evidence.lower()

    def test_read_error_fail_closed(self):
        """Test: File read error → fail-closed."""
        check = AuditTrailCheck()
        with patch("builtins.open") as mock_file:
            mock_file.side_effect = IOError("Permission denied")
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_123", Path("/audit.jsonl"))
                assert result.passed == False
                assert "error" in result.evidence.lower()

    def test_last_three_events_shown(self):
        """Test: Evidence shows last 3 event types (truncation)."""
        check = AuditTrailCheck()
        audit_content = (
            '{"task_id": "task_123", "event_type": "event_A"}\n'
            '{"task_id": "task_123", "event_type": "event_B"}\n'
            '{"task_id": "task_123", "event_type": "event_C"}\n'
            '{"task_id": "task_123", "event_type": "event_D"}\n'
        )

        with patch("builtins.open", mock_open(read_data=audit_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_123", Path("/audit.jsonl"))
                assert result.passed == True
                # Should show last 3: B, C, D (rolling buffer)
                assert "event_B" in result.evidence
                assert "event_C" in result.evidence
                assert "event_D" in result.evidence

    def test_check_result_is_frozen(self):
        """Test: CheckResult dataclass is immutable (frozen=True)."""
        result = CheckResult(passed=True, evidence="test", check_name="audit_trail")
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            result.passed = False

    def test_check_result_default_event_count(self):
        """Test: CheckResult has default event_count=0."""
        result = CheckResult(passed=False, evidence="not found")
        assert result.event_count == 0
        assert result.check_name == "audit_trail"

    def test_whitespace_only_lines_ignored(self):
        """Test: Empty and whitespace-only lines are ignored."""
        check = AuditTrailCheck()
        audit_content = (
            '{"task_id": "task_123", "event_type": "event_1"}\n'
            '  \n'
            '\n'
            '{"task_id": "task_123", "event_type": "event_2"}\n'
        )

        with patch("builtins.open", mock_open(read_data=audit_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_123", Path("/audit.jsonl"))
                assert result.passed == True
                assert result.event_count == 2  # Blank lines ignored
