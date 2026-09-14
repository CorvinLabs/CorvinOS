"""DoD_FeedbackEvent: User feedback → weight optimization (ADR-0722)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass(frozen=True)
class DoD_FeedbackEvent:
    """Immutable feedback event from operator → optimizer."""

    event_type: str = "dod_feedback_received"
    tenant_id: str = "_default"
    task_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # What happened
    dod_score_automatic: float = 0.0  # What the Skill computed
    dod_score_operator: float = 0.5   # What operator says is correct

    # Why
    reason: str = ""  # "too_harsh" | "too_lenient" | "wrong_type" | "check_broken"

    # Metadata
    task_type: str = "cli_command"  # "cli_command" | "api_endpoint" | "lib_function" | "plugin" | "skill"
    affected_checks: List[str] = field(default_factory=list)  # Which checks were wrong?

    # Audit
    hash: str = ""
    prev_hash: str = ""

    @property
    def delta(self) -> float:
        """Operator's correction (positive = skill was too harsh)."""
        return self.dod_score_operator - self.dod_score_automatic

    def to_dict(self) -> dict:
        """Convert to dict."""
        return {
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "dod_score_automatic": self.dod_score_automatic,
            "dod_score_operator": self.dod_score_operator,
            "delta": self.delta,
            "reason": self.reason,
            "task_type": self.task_type,
            "affected_checks": self.affected_checks,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
        }


# Unit Tests
class TestDoD_FeedbackEvent:
    """Test feedback event creation."""

    def test_feedback_too_harsh(self):
        """Operator says score was too harsh."""
        feedback = DoD_FeedbackEvent(
            task_id="t1",
            dod_score_automatic=0.65,
            dod_score_operator=0.85,
            reason="too_harsh",
            affected_checks=["audit_trail"],
        )
        assert feedback.delta == 0.20
        assert feedback.reason == "too_harsh"
        assert "audit_trail" in feedback.affected_checks

    def test_feedback_too_lenient(self):
        """Operator says score was too lenient."""
        feedback = DoD_FeedbackEvent(
            task_id="t1",
            dod_score_automatic=0.85,
            dod_score_operator=0.60,
            reason="too_lenient",
            affected_checks=["reachability"],
        )
        assert feedback.delta == -0.25
        assert feedback.reason == "too_lenient"

    def test_multiple_affected_checks(self):
        """Multiple checks identified as wrong."""
        feedback = DoD_FeedbackEvent(
            task_id="t1",
            dod_score_automatic=0.70,
            dod_score_operator=0.80,
            affected_checks=["audit_trail", "test_evidence"],
        )
        assert len(feedback.affected_checks) == 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
