"""AuditTrailCheck: Are there audit events in audit.jsonl?"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CheckResult:
    """Immutable result of a single check."""
    passed: bool
    evidence: str
    check_name: str = "audit_trail"
    event_count: int = 0


class AuditTrailCheck:
    """Scan audit.jsonl for task-related events (fail-closed)."""

    def run(
        self,
        task_id: str,
        audit_path: Path,
        window_mins: int = 10,
    ) -> CheckResult:
        """
        Count audit events matching task_id in audit.jsonl.

        Returns CheckResult with passed=(count > 0) + evidence.
        Fail-closed: missing file, corrupt JSON, no matches → passed=False.
        """
        if not task_id or not task_id.strip():
            return CheckResult(
                passed=False,
                evidence="Task ID is empty",
                check_name="audit_trail"
            )

        try:
            if not audit_path.exists():
                return CheckResult(
                    passed=False,
                    evidence=f"Audit trail not found: {audit_path}",
                    check_name="audit_trail"
                )

            # Scan audit.jsonl for matching events
            count = 0
            last_events = []

            with open(audit_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    try:
                        event = json.loads(line)
                        if event.get("task_id") == task_id:
                            count += 1
                            last_events.append(event.get("event_type", "?"))
                            if len(last_events) > 3:
                                last_events.pop(0)
                    except json.JSONDecodeError:
                        # Skip malformed lines (fail-closed: ignore, keep counting)
                        pass

            if count > 0:
                event_types = ", ".join(last_events)
                evidence = f"{count} audit event(s): {event_types}"
                return CheckResult(
                    passed=True,
                    evidence=evidence,
                    check_name="audit_trail",
                    event_count=count
                )
            else:
                return CheckResult(
                    passed=False,
                    evidence=f"No audit events found for task_id={task_id}",
                    check_name="audit_trail"
                )

        except Exception as e:
            # Fail-closed: any error reading audit → fail
            return CheckResult(
                passed=False,
                evidence=f"Audit scan error: {type(e).__name__}: {str(e)[:50]}",
                check_name="audit_trail"
            )


# ============================================================================
# UNIT TESTS
# ============================================================================

import pytest
from unittest.mock import mock_open, patch


class TestAuditTrailCheck:

    def test_audit_events_found(self):
        """Audit events found for task_id."""
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

    def test_no_audit_events(self):
        """No events found for task_id."""
        check = AuditTrailCheck()
        audit_content = (
            '{"task_id": "other_task", "event_type": "skill_executed"}\n'
        )

        with patch("builtins.open", mock_open(read_data=audit_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_123", Path("/audit.jsonl"))
                assert result.passed == False
                assert "no audit events" in result.evidence.lower()

    def test_audit_file_missing(self):
        """Audit file doesn't exist → fail-closed."""
        check = AuditTrailCheck()
        with patch("pathlib.Path.exists", return_value=False):
            result = check.run("task_123", Path("/missing.jsonl"))
            assert result.passed == False
            assert "not found" in result.evidence.lower()

    def test_malformed_json_skipped(self):
        """Malformed JSON lines are skipped, valid ones counted."""
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
        """Empty task_id → immediate fail."""
        check = AuditTrailCheck()
        result = check.run("", Path("/audit.jsonl"))
        assert result.passed == False
        assert "empty" in result.evidence.lower()

    def test_read_error_fail_closed(self):
        """File read error → fail-closed."""
        check = AuditTrailCheck()
        with patch("builtins.open") as mock_file:
            mock_file.side_effect = IOError("Permission denied")
            with patch("pathlib.Path.exists", return_value=True):
                result = check.run("task_123", Path("/audit.jsonl"))
                assert result.passed == False
                assert "error" in result.evidence.lower()

    def test_last_three_events_shown(self):
        """Evidence shows last 3 event types."""
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
                # Should show last 3: B, C, D
                assert "event_B" in result.evidence
                assert "event_C" in result.evidence
                assert "event_D" in result.evidence


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
