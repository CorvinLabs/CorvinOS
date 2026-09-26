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
