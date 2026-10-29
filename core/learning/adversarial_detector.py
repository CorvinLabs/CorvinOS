"""ADR-0534 Layer 3 — trust weight of a feedback SOURCE on a subject.

``trust_weight = max(FLOOR, 1 - rejected / total)`` over the last ``WINDOW``
judged gate decisions (throttle refusals excluded, ``NOT_A_VERDICT``) on the
subject BY THE SAME PRODUCER: the gate passes the
(subject, source) history (``DecisionLedger.source_history``), so one faulty or
hostile producer lowers only its own weight, never an honest producer's on the
same skill, and the alert names that producer (``source_key``). The floor is 0.1, not 0: a source that was
wrong for a while can recover. Below ``ALERT_BELOW`` (0.3) the gate emits
``learning.adversarial_feedback_detected``.

The weight is stamped into the accepted event's ``signal["trust_weight"]``;
every consumer that turns feedback into a number (operator-rating stats, the
workflow optimizer's Beta update, the stream4 loop closure, the
``/learning/feedback`` optimizer epoch) multiplies by it. A signal recorded before
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


#: Decisions that say nothing about whether the producer's signal was RIGHT: a
#: rate-limit refusal is the gate's own budget, not a verdict on the feedback
#: (review c120 r2_s1-8: an honest operator rating in bursts ended at weight 0.5).
NOT_A_VERDICT = frozenset({"rate_limit_exceeded"})


def assess(decisions: list[dict], *, subject: str, window: int = WINDOW) -> TrustAssessment:
    """Weight over the producer's last ``window`` JUDGED decisions on ``subject``
    (accepted, or rejected by Layer 2); throttle refusals are not counted."""
    mine = [d for d in decisions if d.get("subject") == subject
            and d.get("reason") not in NOT_A_VERDICT][-window:]
    accepted = sum(1 for d in mine if d.get("decision") == "accepted")
    rejected = len(mine) - accepted
    if not mine:
        return TrustAssessment(1.0, 0, 0)
    return TrustAssessment(max(FLOOR, 1.0 - rejected / len(mine)), rejected, accepted)


def weight_of(signal: dict | None) -> float:
    """The trust weight a consumer applies to one stored feedback signal."""
    w = (signal or {}).get("trust_weight")
    return float(w) if isinstance(w, (int, float)) and 0.0 < w <= 1.0 else 1.0
