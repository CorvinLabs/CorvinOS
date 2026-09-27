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

#: Consent scope checked before feedback is turned into config hypotheses.
#: Resolved against the ONE console consent store
#: (``core.compliance.consent_store`` — the same store the console's
#: ``core.compliance.consent.consent_required`` gate reads). Until 2026-09-27
#: this module imported ``core.consent.manager``, which does not exist, so
#: every check raised and ``POST /v1/console/learning/feedback`` answered 500.
LEARNING_FEEDBACK_CONSENT_SCOPE = "learning_feedback"

__all__ = [
    "LEARNING_FEEDBACK_CONSENT_SCOPE",
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

    Consent check: User must have given consent before feedback is processed.
    Fail-closed: If consent is not given, feedback is rejected with 403.
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

    def check_consent(self, feedback: UserFeedback, *, user_id: Optional[str] = None) -> bool:
        """Require an active ``learning_feedback`` consent for ``user_id``.

        Deny-by-default: no ``user_id``, no active (granted, unexpired,
        unrevoked) record in the tenant's consent store, or ANY error while
        reading it → ``PermissionError``. Callers map that to HTTP 403.

        Returns:
            True when consent is active.

        Raises:
            PermissionError: consent missing, expired, revoked or unverifiable.
        """
        if not user_id:
            raise PermissionError("Feedback rejected: no user identity for the consent check")
        try:
            from core.compliance.consent_store import get_consent_store

            store = get_consent_store(tenant_id=feedback.tenant_id)
            granted = store.get_consent(user_id=user_id, scope=LEARNING_FEEDBACK_CONSENT_SCOPE)
        except Exception as exc:  # noqa: BLE001 — fail-closed
            raise PermissionError(
                f"Feedback rejected: consent check failed ({type(exc).__name__})"
            ) from None
        if not granted:
            raise PermissionError(
                "Feedback rejected: no active consent for learning/feedback processing"
            )
        return True

    def interpret(self, feedback: UserFeedback, *, user_id: Optional[str] = None) -> list[ConfigHypothesis]:
        """Convert feedback into hypotheses.

        Each matching rule generates one hypothesis. Multiple rules can fire
        for one feedback (e.g., "excellent" + "reason contains 'fast'").

        Raises:
            PermissionError: If user has not given consent (403)
        """
        # Check consent first (fail-closed)
        self.check_consent(feedback, user_id=user_id)

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
