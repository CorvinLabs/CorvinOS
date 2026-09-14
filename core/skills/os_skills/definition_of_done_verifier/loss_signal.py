"""DoD_LossSignal: Outcome signal for learning infrastructure (ADR-0314)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass(frozen=True)
class DoD_LossSignal:
    """Immutable outcome signal from DoD Verifier → Learning Infrastructure."""

    event_type: str = "dod_loss_signal"
    tenant_id: str = "_default"
    task_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Raw outcomes (0.0–1.0)
    dod_score: float = 0.0  # Overall score
    dod_score_before_feedback: float = 0.0  # Score before operator correction

    # Per-check outcomes
    check_scores: Dict[str, float] = field(default_factory=dict)  # {check_name: 0.0 or 1.0}

    # Metadata
    task_type: str = "cli_command"  # "cli_command" | "api_endpoint" | "lib_function" | "plugin" | "skill"
    weights_applied: Dict[str, float] = field(default_factory=dict)  # {w_reach, w_audit, ...}

    # Feedback
    feedback_received: bool = False
    operator_score: Optional[float] = None  # Operator's judgment (0.0–1.0)
    feedback_reason: Optional[str] = None  # "too_harsh" | "too_lenient" | "wrong_type" | "check_broken"

    # Audit
    hash: str = ""
    prev_hash: str = ""

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "dod_score": self.dod_score,
            "dod_score_before_feedback": self.dod_score_before_feedback,
            "check_scores": self.check_scores,
            "task_type": self.task_type,
            "weights_applied": self.weights_applied,
            "feedback_received": self.feedback_received,
            "operator_score": self.operator_score,
            "feedback_reason": self.feedback_reason,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
        }


# Unit Tests for DoD_LossSignal
class TestDoD_LossSignal:
    """Test outcome signal creation."""

    def test_create_signal(self):
        """Create a basic loss signal."""
        signal = DoD_LossSignal(
            task_id="t1",
            dod_score=0.85,
            check_scores={"reachability": 1.0, "audit_trail": 0.0},
            task_type="api_endpoint",
        )
        assert signal.task_id == "t1"
        assert signal.dod_score == 0.85
        assert signal.check_scores["reachability"] == 1.0
        assert signal.feedback_received == False

    def test_immutable(self):
        """Ensure dataclass is frozen."""
        signal = DoD_LossSignal(task_id="t1", dod_score=0.5)
        try:
            signal.dod_score = 0.9  # Should fail
            assert False, "Should not be able to modify frozen dataclass"
        except AttributeError:
            pass  # Expected

    def test_to_dict(self):
        """Convert to dict for JSON."""
        signal = DoD_LossSignal(
            task_id="t1",
            dod_score=0.75,
            operator_score=0.80,
        )
        d = signal.to_dict()
        assert isinstance(d, dict)
        assert d["task_id"] == "t1"
        assert d["dod_score"] == 0.75


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
