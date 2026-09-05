"""User Feedback Collection & Interpretation (ADR-0549 Stages 1–2).

Phase 2 of CONCEPT-0029. User rates task completions, and feedback is converted
into deterministic hypotheses for the optimizer (Phase 2, Stage 3).

Stages:
  1. UserFeedback collection (user picks outcome_quality, optional reason)
  2. FeedbackInterpreter converts feedback → ConfigHypothesis list
  3. Optimizer tests hypotheses (Phase 2, Stage 3; see skill_adapter.py)

Audit trail:
  - user_feedback event (user gives feedback)
  - optimizer_hypothesis_generated (feedback → hypothesis)
  - optimizer_hypothesis_tested (test result)
  - optimizer_hypothesis_accepted/rejected (outcome)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from core.tenants.validation import validate_tenant_id

__all__ = [
    "UserFeedback",
    "ConfigHypothesis",
    "FeedbackInterpreter",
]


@dataclass(frozen=True)
class UserFeedback:
    """Explicit user signal about a completed task (ADR-0549 Stage 1).

    Frozen: audit-safe. Never infer feedback from behavior; only accept explicit
    user-given signals. This is CONCEPT-0029 Constraint 4.
    """

    task_id: str
    tenant_id: str
    timestamp: datetime

    # What did the user think?
    outcome_quality: Literal["excellent", "good", "okay", "poor", "bad"]

    # Would they do it again?
    would_repeat: Optional[bool] = None

    # Free-text reason (audit-only; not parsed for config tuning)
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)
        if not self.task_id:
            raise ValueError("task_id required")
        # Coerce naive timestamp to UTC
        if object.__getattribute__(self, "timestamp").tzinfo is None:
            object.__setattr__(
                self,
                "timestamp",
                object.__getattribute__(self, "timestamp").replace(tzinfo=timezone.utc)
            )


@dataclass(frozen=True)
class ConfigHypothesis:
    """Hypothesis: "change this Skill parameter by this delta" (ADR-0549 Stage 2).

    Generated deterministically from feedback, never from an LLM.
    Always has a reason (for audit), confidence (for gating), and is reversible.
    """

    hypothesis_id: str  # UUID or deterministic slug
    skill_id: str  # Which Skill to tune?
    param: str  # Which parameter? (e.g., "confidence_threshold")
    delta: float  # By how much? (e.g., +0.05)
    reason: str  # Why? (e.g., "User rated highly + would repeat")
    confidence: float  # [0.0–1.0] How confident are we in this hypothesis?

    def __post_init__(self) -> None:
        # Sanity checks
        if not -0.20 <= self.delta <= 0.20:
            raise ValueError(f"delta must be in [-0.20, 0.20], got {self.delta}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if not self.skill_id or not self.param:
            raise ValueError("skill_id and param required")


class FeedbackInterpreter:
    """Convert UserFeedback → ConfigHypothesis (deterministic, auditable).

    Rules are hardcoded, not ML-generated. Each rule has:
      - Trigger: feedback pattern (outcome_quality, would_repeat, reason keywords)
      - Action: what parameter to change, by how much
      - Confidence: how sure are we this is right?

    All rules are documented in this class as constants for reviewability.
    """

    # Rules: feedback pattern → hypothesis
    _RULES = [
        {
            "name": "excellent_would_repeat",
            "trigger": lambda fb: fb.outcome_quality in ("excellent", "good") and fb.would_repeat is True,
            "param": "confidence_threshold",
            "delta": +0.05,
            "reason": "User rated highly + would repeat",
            "confidence": 0.80,
        },
        {
            "name": "poor_would_not_repeat",
            "trigger": lambda fb: fb.outcome_quality in ("poor", "bad") and fb.would_repeat is False,
            "param": "confidence_threshold",
            "delta": -0.05,
            "reason": "User rated poorly + would not repeat",
            "confidence": 0.70,
        },
        {
            "name": "reason_contains_fast",
            "trigger": lambda fb: fb.reason and "fast" in fb.reason.lower(),
            "param": "speed_weight",
            "delta": +0.10,
            "reason": "User mentioned 'fast' in feedback",
            "confidence": 0.60,
        },
        {
            "name": "reason_contains_clear",
            "trigger": lambda fb: fb.reason and "clear" in fb.reason.lower(),
            "param": "clarity_weight",
            "delta": +0.10,
            "reason": "User mentioned 'clear' in feedback",
            "confidence": 0.60,
        },
        {
            "name": "okay_neutral",
            "trigger": lambda fb: fb.outcome_quality == "okay",
            "param": "exploration_rate",
            "delta": +0.02,
            "reason": "User was neutral; try exploring more",
            "confidence": 0.40,
        },
    ]

    def interpret(self, feedback: UserFeedback) -> list[ConfigHypothesis]:
        """Convert feedback into hypotheses.

        Each matching rule generates one hypothesis. Multiple rules can fire
        for one feedback (e.g., "excellent" + "reason contains 'fast'").
        """
        hypotheses = []

        for rule in self._RULES:
            if rule["trigger"](feedback):
                hypothesis = ConfigHypothesis(
                    hypothesis_id=f"{feedback.task_id}_{rule['name']}",
                    skill_id="os.delegation_router",  # Phase 2 only tunes the router
                    param=rule["param"],
                    delta=rule["delta"],
                    reason=rule["reason"],
                    confidence=rule["confidence"],
                )
                hypotheses.append(hypothesis)

        return hypotheses
