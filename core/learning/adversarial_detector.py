"""ADR-0534 Layer 3 — trust weight of a feedback subject.

``trust_weight = max(FLOOR, 1 - rejected / total)`` over the last ``WINDOW``
gate decisions on the subject. The floor is 0.1, not 0: a source that was
wrong for a while can recover. Below ``ALERT_BELOW`` (0.3) the gate emits
``learning.adversarial_feedback_detected``.

The weight is stamped into the accepted event's ``signal["trust_weight"]``;
every consumer that turns feedback into a number (operator-rating stats, the
workflow optimizer's Beta update) multiplies by it. A signal recorded before
this gate existed carries no weight and counts as 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass

WINDOW = 20
FLOOR = 0.1
ALERT_BELOW = 0.3


@dataclass(frozen=True)
class TrustAssessment:
    trust_weight: float
    rejection_count: int
    accepted_count: int

    @property
    def alert(self) -> bool:
        return self.trust_weight < ALERT_BELOW


def assess(decisions: list[dict], *, subject: str, window: int = WINDOW) -> TrustAssessment:
    mine = [d for d in decisions if d.get("subject") == subject][-window:]
    accepted = sum(1 for d in mine if d.get("decision") == "accepted")
    rejected = len(mine) - accepted
    if not mine:
        return TrustAssessment(1.0, 0, 0)
    return TrustAssessment(max(FLOOR, 1.0 - rejected / len(mine)), rejected, accepted)


def weight_of(signal: dict | None) -> float:
    """The trust weight a consumer applies to one stored feedback signal."""
    w = (signal or {}).get("trust_weight")
    return float(w) if isinstance(w, (int, float)) and 0.0 < w <= 1.0 else 1.0
