"""Method-Discovery confidence scoring (ADR-0548, Phase 1).

Given a :class:`~core.skills.os_skills.method_discovery.WorkstylePattern`, compute
how much we believe the pattern is a *learned method* rather than a coincidence::

    confidence = base_rate * success_boost * sample_size_boost * recency_boost

The four sub-scores are deliberately separate, pure and side-effect free: EU AI
Act Art. 50 requires that an automated recommendation can be explained, so
:meth:`ConfidenceScorer.score` returns a :class:`ConfidenceBreakdown` carrying
every input and every intermediate value, not a bare float. The caller audits
that breakdown; the scorer itself never writes.

NOT the same thing as ``core.learning.confidence_scorer`` (ADR-0315), which
scores a single Skill *decision* on relevance/reliability. This module scores a
*pattern across many observations*. They intentionally do not share code.

Spec note — the base-rate table
-------------------------------
ADR-0548 states the base-rate table twice and the two statements disagree by one
index. The prose says "length=2 -> 0.4, length=3 -> 0.65, length=4 -> 0.75,
length=5+ -> 0.85"; the executable snippet says
``[0.4, 0.4, 0.65, 0.75, 0.85, 0.88, 0.90][len(skills)]``, i.e. length=2 -> 0.65,
length=3 -> 0.75, length=4 -> 0.85. This module follows the SNIPPET, because
(a) it is the executable form of the spec and (b) under the prose form no
sequence shorter than five skills can ever reach ``DISCOVERY_THRESHOLD``, which
would make the ADR's own Phase-1 gate unreachable. See ``_BASE_RATE_BY_LENGTH``.

The snippet also indexes a 7-element list with ``len(skills)`` unguarded, so any
sequence of 7+ skills raises ``IndexError``. Here the index is clamped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cycle
    from .method_discovery import WorkstylePattern

__all__ = [
    "ConfidenceBreakdown",
    "ConfidenceScorer",
    "DISCOVERY_THRESHOLD",
    "AUTONOMOUS_THRESHOLD",
    "MAX_CONFIDENCE",
]


# ── Thresholds (CONCEPT-0029 Constraint 2) ──────────────────────────────────

#: A pattern is surfaced to the user at or above this confidence.
DISCOVERY_THRESHOLD: float = 0.78

#: A pattern may be applied *autonomously* only at or above this confidence.
#: Deliberately higher than the discovery threshold: recommending is cheap,
#: acting without being asked is not.
AUTONOMOUS_THRESHOLD: float = 0.85

#: Hard ceiling. Confidence is a belief about a small, biased sample; it must
#: never present as certainty, so the product is clamped here regardless of how
#: the sub-scores multiply out.
MAX_CONFIDENCE: float = 0.95

#: Prior that a sequence of a given length is a real method rather than noise.
#: Index = number of skills in the sequence; the last entry is reused for any
#: longer sequence (the ADR snippet would raise IndexError there).
_BASE_RATE_BY_LENGTH: tuple[float, ...] = (0.40, 0.40, 0.65, 0.75, 0.85, 0.88, 0.90)

#: Boost applied when a sequence mixes in a rarely-used skill: a common
#: sequence repeating is weak evidence, an unusual one repeating is strong.
_EXOTIC_MULTIPLIER: float = 1.10

#: Skills that are rare enough that their presence in a repeated sequence is
#: itself evidence of intent. Kept as an explicit, reviewable allowlist rather
#: than a frequency heuristic so the score stays reproducible from the ADR
#: alone — a frequency-derived set would make the same pattern score differently
#: on two installs and break audit reproducibility.
_EXOTIC_SKILLS: frozenset[str] = frozenset(
    {
        "/security-review",
        "/dialectical-reasoning",
        "/root-cause-by-layer",
        "/drift-detection",
        "/method-evolution",
        "/adversarial-review",
        "/reproducibility-first",
    }
)

#: A sequence must be at least this long before the exotic boost can apply.
#: A single exotic skill on its own is a habit, not a method.
_EXOTIC_MIN_LENGTH: int = 3


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """Every input and intermediate of one confidence computation.

    Frozen and hashable so it can be embedded verbatim in an audit payload
    (ADR-0548 constraint 1). This is the EU AI Act Art. 50 explanation object:
    an operator reading it can re-derive ``confidence`` by hand.
    """

    confidence: float
    base_rate: float
    success_boost: float
    sample_size_boost: float
    recency_boost: float

    # Inputs, restated so the breakdown is self-contained in the audit trail.
    sequence_length: int
    observation_count: int
    success_rate: float
    days_since_last_observation: int
    exotic_combo: bool
    capped: bool

    def explain(self) -> str:
        """One-line human-readable derivation (used in dashboards + audit)."""
        line = (
            f"{self.base_rate:.4f} (base, len={self.sequence_length}"
            f"{', exotic' if self.exotic_combo else ''}) "
            f"x {self.success_boost:.4f} (success {self.success_rate:.0%}, N={self.observation_count}) "
            f"x {self.sample_size_boost:.4f} (sample N={self.observation_count}) "
            f"x {self.recency_boost:.4f} (recency {self.days_since_last_observation}d) "
            f"= {self.confidence:.4f}"
        )
        if self.capped:
            line += f" [capped at {MAX_CONFIDENCE}]"
        return line

    def to_payload(self) -> dict:
        """Audit-safe dict (all values are numbers/bools — never user content)."""
        return {
            "confidence": self.confidence,
            "base_rate": self.base_rate,
            "success_boost": self.success_boost,
            "sample_size_boost": self.sample_size_boost,
            "recency_boost": self.recency_boost,
            "sequence_length": self.sequence_length,
            "observation_count": self.observation_count,
            "success_rate": self.success_rate,
            "days_since_last_observation": self.days_since_last_observation,
            "exotic_combo": self.exotic_combo,
            "capped": self.capped,
        }


def _as_utc(moment: datetime) -> datetime:
    """Interpret a naive datetime as UTC; convert an aware one to UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class ConfidenceScorer:
    """Compute confidence for a discovered workstyle pattern (ADR-0548).

    Pure and deterministic: the same pattern and the same ``now`` always yield
    the same breakdown. Auditing is the caller's job (``observability.py``) —
    keeping the scorer write-free is what lets the unit tests run 50+ cases
    without touching the audit chain.
    """

    def __init__(self, *, now: Optional[datetime] = None):
        """Args:
        now: Fixed "current time" for recency decay. Injectable so tests are
            deterministic; defaults to wall-clock UTC at each call.
        """
        self._now = _as_utc(now) if now is not None else None

    def _current_time(self) -> datetime:
        return self._now if self._now is not None else datetime.now(timezone.utc)

    # ── public API ──────────────────────────────────────────────────────

    def score(self, pattern: "WorkstylePattern") -> ConfidenceBreakdown:
        """Score a pattern. Returns the full derivation, not just the number."""
        return self.score_parts(
            skill_sequence=pattern.skill_sequence,
            success_rate=pattern.success_rate,
            observation_count=pattern.observation_count,
            last_observed=pattern.last_observed,
        )

    def score_parts(
        self,
        *,
        skill_sequence: Sequence[str],
        success_rate: float,
        observation_count: int,
        last_observed: datetime,
    ) -> ConfidenceBreakdown:
        """Score from raw parts (no ``WorkstylePattern`` instance required).

        Raises:
            ValueError: if ``success_rate`` is outside [0, 1] or
                ``observation_count`` is negative. Both are fail-closed rather
                than clamped: a caller passing 1.5 has a bug upstream, and
                silently clamping it would launder that bug into a confident
                recommendation.
        """
        if not 0.0 <= success_rate <= 1.0:
            raise ValueError(f"success_rate must be in [0, 1], got {success_rate!r}")
        if observation_count < 0:
            raise ValueError(f"observation_count must be >= 0, got {observation_count!r}")
        if math.isnan(success_rate):
            raise ValueError("success_rate must not be NaN")

        skills = tuple(skill_sequence)
        exotic = self._is_exotic_combo(skills)
        base_rate = self._base_rate_of_sequence(skills)
        success_boost = self._success_boost(success_rate, observation_count)
        sample_size_boost = self._sample_size_boost(observation_count)
        days = self._days_since(last_observed)
        recency_boost = self._recency_boost(last_observed)

        raw = base_rate * success_boost * sample_size_boost * recency_boost
        capped = raw > MAX_CONFIDENCE
        confidence = min(raw, MAX_CONFIDENCE)

        return ConfidenceBreakdown(
            confidence=confidence,
            base_rate=base_rate,
            success_boost=success_boost,
            sample_size_boost=sample_size_boost,
            recency_boost=recency_boost,
            sequence_length=len(skills),
            observation_count=observation_count,
            success_rate=success_rate,
            days_since_last_observation=days,
            exotic_combo=exotic,
            capped=capped,
        )

    # ── sub-scores ──────────────────────────────────────────────────────

    def _base_rate_of_sequence(self, skills: Sequence[str]) -> float:
        """Prior probability that this sequence is a learned pattern.

        An empty sequence is not a pattern and scores 0.0 — the ADR table's
        index-0 entry (0.4) would otherwise give a "method" with no steps a
        non-zero prior. ``WorkstylePattern`` rejects empty sequences anyway;
        this is the second line of defence.
        """
        n = len(skills)
        if n == 0:
            return 0.0
        base = _BASE_RATE_BY_LENGTH[min(n, len(_BASE_RATE_BY_LENGTH) - 1)]
        if self._is_exotic_combo(skills):
            base *= _EXOTIC_MULTIPLIER
        return min(base, MAX_CONFIDENCE)

    def _is_exotic_combo(self, skills: Iterable[str]) -> bool:
        """True if a long-enough sequence contains a rarely-used skill."""
        seq = tuple(skills)
        if len(seq) < _EXOTIC_MIN_LENGTH:
            return False
        return any(s in _EXOTIC_SKILLS for s in seq)

    def _success_boost(self, success_rate: float, n: int) -> float:
        """How much the observed success rate raises confidence.

        100% over 2 runs is luck; 90% over 30 runs is a method. The cap rises
        with N (0.7 / 0.9 / 0.95) so a small sample can never look certain.
        """
        if n < 3:
            return 0.4 + (success_rate * 0.3)
        if n < 10:
            return 0.6 + (success_rate * 0.3)
        return 0.7 + (success_rate * 0.25)

    def _sample_size_boost(self, n: int) -> float:
        """Penalise small samples (overfitting risk)."""
        if n >= 30:
            return 0.99
        if n >= 20:
            return 0.95
        if n >= 10:
            return 0.90
        if n >= 5:
            return 0.80
        if n >= 3:
            return 0.60
        return 0.40

    def _days_since(self, last_observed: datetime) -> int:
        """Whole days between ``last_observed`` and now, floored at 0.

        A future timestamp (clock skew, a bad import) yields 0 rather than a
        negative age — it must not be able to *raise* confidence.
        """
        delta = self._current_time() - _as_utc(last_observed)
        return max(0, delta.days)

    def _recency_boost(self, last_observed: datetime) -> float:
        """Decay confidence for stale patterns — workstyles change.

        <=7d: 1.0 · 7-30d: linear to 0.95 · 30-60d: linear to 0.80 ·
        >60d: slow decay with a 0.60 floor (never zero: an old pattern is
        weak evidence, not counter-evidence).
        """
        days_ago = self._days_since(last_observed)
        if days_ago <= 7:
            return 1.0
        if days_ago <= 30:
            return 1.0 - (days_ago / 30) * 0.05
        if days_ago <= 60:
            return 0.95 - ((days_ago - 30) / 30) * 0.15
        return max(0.60, 0.80 - (days_ago / 365) * 0.1)

    # ── decisions ───────────────────────────────────────────────────────

    @staticmethod
    def is_discoverable(confidence: float, *, user_confirmed: bool = False) -> bool:
        """Whether a pattern may be surfaced to the user.

        An explicit user confirmation overrides the statistical gate: a human
        saying "yes, that is my method" is stronger evidence than N samples
        (CONCEPT-0029 Constraint 4). The reverse is never true — statistics
        alone never override a user's rejection.
        """
        return bool(user_confirmed) or confidence >= DISCOVERY_THRESHOLD

    @staticmethod
    def is_autonomously_applicable(confidence: float, *, user_confirmed: bool = False) -> bool:
        """Whether a pattern may be applied without asking first.

        Unlike :meth:`is_discoverable`, confirmation alone is not enough: the
        statistical bar must ALSO be met, because the cost of acting wrongly
        without asking is higher than the cost of a bad suggestion.
        """
        return confidence >= AUTONOMOUS_THRESHOLD and bool(user_confirmed)


#: Explicit alias — imports that also pull in ``core.learning.confidence_scorer``
#: (ADR-0315, a different scorer with the same class name) should use this to
#: keep the two visibly distinct at the call site.
MethodConfidenceScorer = ConfidenceScorer
