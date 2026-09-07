"""Phase 2-9: Feedback Consistency Checker — Mitigation for Finding #9

Validates feedback consistency by comparing user feedback against recent loss trends.
Prevents divergence caused by contradictory feedback signals.

Compliance: GDPR Art. 6 (feedback consent), Art. 30 (audit trail), Art. 32 (data security)
Mitigation for: Finding #9 "Feedback Contradiction: Conflicting signals → divergence"
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
import logging
import numpy as np

logger = logging.getLogger(__name__)


class FeedbackSignal(str, Enum):
    """Feedback signal type."""
    GOOD = "good"        # User satisfied with outcome
    BAD = "bad"          # User dissatisfied with outcome
    OTHER = "other"      # Neutral / inconclusive


@dataclass(frozen=True)
class ConsistencyCheckResult:
    """Immutable result of consistency check (audit trail)."""
    feedback_id: str
    skill_id: str
    task_id: str
    feedback_signal: FeedbackSignal
    consistency_score: float  # [0, 1]: 1.0 = fully consistent, 0.0 = fully contradictory
    is_consistent: bool  # consistency_score >= 0.5
    loss_trend: str  # "increasing", "decreasing", "stable"
    recent_loss_delta: float  # change in loss over recent window
    contradiction_reason: Optional[str] = None
    tenant_id: str = "_default"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to audit event format."""
        return {
            "event_type": "feedback_consistency_checked",
            "feedback_id": self.feedback_id,
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "feedback_signal": self.feedback_signal.value,
            "consistency_score": self.consistency_score,
            "is_consistent": self.is_consistent,
            "loss_trend": self.loss_trend,
            "recent_loss_delta": self.recent_loss_delta,
            "contradiction_reason": self.contradiction_reason,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class FeedbackContradictionEvent:
    """Immutable feedback contradiction audit event."""
    feedback_id: str
    skill_id: str
    task_id: str
    consistency_score: float
    feedback_signal: FeedbackSignal
    expected_signal: FeedbackSignal
    loss_trend: str
    downweight_factor: float  # (1 - consistency_score): how much to reduce feedback weight
    tenant_id: str = "_default"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to audit event format."""
        return {
            "event_type": "feedback_contradiction",
            "feedback_id": self.feedback_id,
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "consistency_score": self.consistency_score,
            "feedback_signal": self.feedback_signal.value,
            "expected_signal": self.expected_signal.value,
            "loss_trend": self.loss_trend,
            "downweight_factor": self.downweight_factor,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
        }


def _parse_event_timestamp(raw: Any) -> Optional[datetime]:
    """Parse an ISO-8601 learning-event timestamp to an aware UTC datetime.

    Accepts the ``…Z`` form ``LearningEvent.create`` writes, an explicit offset
    (``+02:00``) and a naive stamp (assumed UTC). Returns ``None`` when the
    value is missing or unparseable — the caller must then NOT claim to have
    ordered anything chronologically.
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class FeedbackConsistencyValidator:
    """Validates feedback consistency against recent loss trends (GDPR Art. 32).

    **Failure direction, stated once (round-4 review, F8).** This class'
    docstring used to say "fail-closed" while
    :meth:`validate_consistency`'s exception handler returned
    ``is_consistent=True`` under the comment "Fail-open: assume consistent if
    check fails". Both cannot be true. The deliberate behaviour is:

    * a CHECKER error (store unreachable, malformed history) yields a NEUTRAL
      result (score 0.5, ``is_consistent=True``) — i.e. fail-OPEN, because a
      crashing checker must never downweight legitimate user feedback. The
      reason string names the error and the audit event still fires, so the
      degradation is visible rather than silent;
    * a detected CONTRADICTION is acted on (contradiction event emitted,
      feedback downweighted) — that is the only decision this class makes;
    * the fail-CLOSED guarantees around it belong to the audit-first
      ``EventStore`` and the tenant binding, not to this validator.
    """

    # Configuration
    LOSS_WINDOW_SAMPLES = 20  # Recent samples to evaluate for trend
    CONSISTENCY_THRESHOLD = 0.5  # score >= 0.5 is considered consistent
    LOSS_TREND_THRESHOLD = 0.05  # % change to detect trend (5% = +/- 0.05)
    MAX_FEEDBACK_AGE_MINUTES = 60  # Only recent feedback can be checked

    def __init__(self, audit_backend=None, event_store=None):
        """Initialize consistency validator with dependencies.

        Args:
            audit_backend: Audit trail writer (for logging consistency checks)
            event_store: EventStore instance (to fetch recent loss samples)
        """
        self.audit_backend = audit_backend
        self.event_store = event_store
        # Cache for recent loss history (skill_id → [loss_values])
        self._loss_history: Dict[str, List[float]] = {}

    def validate_consistency(
        self,
        feedback_id: str,
        skill_id: str,
        task_id: str,
        feedback_signal: FeedbackSignal,
        tenant_id: str = "_default",
    ) -> ConsistencyCheckResult:
        """Validate feedback consistency against recent loss trends.

        Args:
            feedback_id: Unique feedback identifier
            skill_id: Skill being evaluated
            task_id: Task context
            feedback_signal: User's feedback (good/bad/other)
            tenant_id: Tenant context (GDPR isolation)

        Returns:
            ConsistencyCheckResult with consistency_score and is_consistent flag
        """
        try:
            # Step 1: Fetch recent loss history for this skill
            recent_losses = self._fetch_recent_losses(skill_id, tenant_id)

            # Step 2: Detect loss trend (increasing, decreasing, stable)
            loss_trend, loss_delta = self._detect_loss_trend(recent_losses)

            # Step 3: Infer expected feedback signal from loss trend
            expected_signal = self._infer_expected_signal(loss_trend)

            # Step 4: Compute consistency score (0-1)
            consistency_score = self._compute_consistency_score(
                feedback_signal=feedback_signal,
                expected_signal=expected_signal,
                loss_delta=loss_delta,
                recent_losses=recent_losses,
            )

            is_consistent = consistency_score >= self.CONSISTENCY_THRESHOLD

            # Determine contradiction reason if applicable
            contradiction_reason = None
            if not is_consistent:
                contradiction_reason = (
                    f"Feedback '{feedback_signal.value}' contradicts loss trend "
                    f"'{loss_trend}' (expected '{expected_signal.value}'). "
                    f"Consistency score: {consistency_score:.2f}"
                )

            result = ConsistencyCheckResult(
                feedback_id=feedback_id,
                skill_id=skill_id,
                task_id=task_id,
                feedback_signal=feedback_signal,
                consistency_score=consistency_score,
                is_consistent=is_consistent,
                loss_trend=loss_trend,
                recent_loss_delta=loss_delta,
                contradiction_reason=contradiction_reason,
                tenant_id=tenant_id,
            )

            # Step 5: Emit audit event (always, whether consistent or not)
            self._emit_consistency_audit_event(result)

            # Step 6: If contradictory, emit contradiction event for backprop downweighting
            if not is_consistent:
                self._emit_contradiction_event(
                    feedback_id=feedback_id,
                    skill_id=skill_id,
                    task_id=task_id,
                    consistency_score=consistency_score,
                    feedback_signal=feedback_signal,
                    expected_signal=expected_signal,
                    loss_trend=loss_trend,
                    tenant_id=tenant_id,
                )

            return result

        except Exception as e:
            logger.error(f"Consistency check failed for feedback {feedback_id}: {e}")
            # Fail-OPEN by design (see the class docstring): a checker error
            # must not downweight legitimate feedback. Neutral score, reason
            # names the error, audit event already emitted above.
            return ConsistencyCheckResult(
                feedback_id=feedback_id,
                skill_id=skill_id,
                task_id=task_id,
                feedback_signal=feedback_signal,
                consistency_score=0.5,
                is_consistent=True,  # neutral, not a verdict — see class docstring
                loss_trend="unknown",
                recent_loss_delta=0.0,
                contradiction_reason=f"Consistency check error: {e}",
                tenant_id=tenant_id,
            )

    def _fetch_recent_losses(self, skill_id: str, tenant_id: str) -> List[float]:
        """Fetch recent loss samples for a skill.

        Args:
            skill_id: Skill identifier
            tenant_id: Tenant context

        Returns:
            List of recent loss values (newest last)
        """
        # If event_store available, fetch from real data.
        #
        # Two defects fixed on 2026-09-07 (round-2 adversarial review, vector 9):
        #   * the call named ``get_events``, which no store defines — every call
        #     raised AttributeError into the bare ``except`` below, so the
        #     validator always saw an empty history and inferred "neutral";
        #   * the result was ``sorted(losses)`` (a NUMERIC sort) behind a comment
        #     claiming chronological order, which INVERTS a decreasing trend and
        #     would rate contradictory feedback as consistent.
        # Order is now taken from the event timestamps, never from the values.
        if self.event_store:
            try:
                # ``newest_first=True`` is load-bearing: the store's default
                # selects the OLDEST ``limit`` events, so a "recent" window
                # taken from it is ancient history that never advances
                # (round-3 review, R3-B2). Stores that predate the flag still
                # work — the fallback below re-queries without it.
                try:
                    events = self.event_store.query_events(
                        tenant_id=tenant_id,
                        skill_id=skill_id,
                        limit=self.LOSS_WINDOW_SAMPLES,
                        newest_first=True,
                    )
                except TypeError:
                    events = self.event_store.query_events(
                        tenant_id=tenant_id,
                        skill_id=skill_id,
                        limit=self.LOSS_WINDOW_SAMPLES,
                    )
                # ``events`` arrives NEWEST FIRST (see the query above), so its
                # reverse is already chronological. That is the fallback, and it
                # is why a missing/unparseable timestamp can no longer invert a
                # trend (round-4 review, F8: the previous code did a plain
                # LEXICAL sort, and Python's sort is stable, so equal or empty
                # keys preserved the reverse-chronological input order and a
                # falling loss was reported as rising).
                samples: list[tuple[Optional[datetime], int, float]] = []
                for index, event in enumerate(events):
                    payload = getattr(event, "signal", None)
                    if payload is None and isinstance(event, dict):
                        payload = event.get("signal") or event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    value = payload.get("total_loss")
                    if not isinstance(value, (int, float)) or isinstance(value, bool):
                        continue
                    raw_ts = getattr(event, "timestamp", None)
                    if raw_ts is None and isinstance(event, dict):
                        raw_ts = event.get("timestamp")
                    samples.append((_parse_event_timestamp(raw_ts), index, float(value)))

                if samples:
                    if all(ts is not None for ts, _, _ in samples):
                        # Sort by real instants. Tie-break on the store's own
                        # order: ``index`` counts newest→oldest, so ``-index``
                        # ascending puts the OLDER of two equal stamps first.
                        samples.sort(key=lambda item: (item[0], -item[1]))
                    else:
                        missing = sum(1 for ts, _, _ in samples if ts is None)
                        logger.warning(
                            "%d/%d loss samples for %s carry no parseable timestamp — "
                            "falling back to store order (newest-first reversed); the "
                            "trend is NOT timestamp-ordered",
                            missing, len(samples), skill_id,
                        )
                        samples.sort(key=lambda item: -item[1])
                    return [value for _, _, value in samples][-self.LOSS_WINDOW_SAMPLES:]
            except Exception as e:
                logger.warning(f"Failed to fetch loss history: {e}")

        # Fallback: return empty list (will infer neutral trend)
        return []

    def _detect_loss_trend(self, recent_losses: List[float]) -> Tuple[str, float]:
        """Detect loss trend from recent samples.

        Args:
            recent_losses: List of recent loss values (chronological order)

        Returns:
            (trend, loss_delta): trend in ["increasing", "decreasing", "stable", "unknown"]
            loss_delta: % change (positive = increasing, negative = decreasing)
        """
        if not recent_losses or len(recent_losses) < 2:
            return "unknown", 0.0

        # Compare first half vs second half
        mid = len(recent_losses) // 2
        first_half_mean = np.mean(recent_losses[:mid]) if mid > 0 else recent_losses[0]
        second_half_mean = np.mean(recent_losses[mid:])

        # Compute % change
        if first_half_mean != 0:
            loss_delta = (second_half_mean - first_half_mean) / abs(first_half_mean)
        else:
            loss_delta = 0.0

        # Classify trend
        if abs(loss_delta) < self.LOSS_TREND_THRESHOLD:
            trend = "stable"
        elif loss_delta > 0:
            trend = "increasing"
        else:
            trend = "decreasing"

        return trend, loss_delta

    def _infer_expected_signal(self, loss_trend: str) -> FeedbackSignal:
        """Infer expected feedback signal from loss trend.

        Args:
            loss_trend: Detected trend

        Returns:
            Expected feedback signal based on trend
        """
        if loss_trend == "decreasing":
            # Loss is improving → expect "good" feedback
            return FeedbackSignal.GOOD
        elif loss_trend == "increasing":
            # Loss is worsening → expect "bad" feedback
            return FeedbackSignal.BAD
        else:
            # Stable or unknown → could go either way
            return FeedbackSignal.OTHER

    def _compute_consistency_score(
        self,
        feedback_signal: FeedbackSignal,
        expected_signal: FeedbackSignal,
        loss_delta: float,
        recent_losses: List[float],
    ) -> float:
        """Compute consistency score [0, 1].

        Args:
            feedback_signal: User's feedback
            expected_signal: Expected signal based on trend
            loss_delta: % change in loss
            recent_losses: Recent loss samples (for volatility)

        Returns:
            Consistency score [0, 1]:
            1.0 = perfectly consistent
            0.5 = neutral (unknown trend or GOOD/OTHER/BAD match)
            0.0 = completely contradictory
        """
        # Base case: if signals match exactly
        if feedback_signal == expected_signal:
            return 1.0

        # If either is "OTHER", it's neutral (score = 0.5)
        if feedback_signal == FeedbackSignal.OTHER or expected_signal == FeedbackSignal.OTHER:
            return 0.5

        # Direct contradiction (GOOD vs BAD)
        if (feedback_signal == FeedbackSignal.GOOD and expected_signal == FeedbackSignal.BAD) or \
           (feedback_signal == FeedbackSignal.BAD and expected_signal == FeedbackSignal.GOOD):
            # Compute severity: how strong is the loss trend?
            # Strong trend (|loss_delta| > threshold) → more contradictory
            # Weak trend → less contradictory
            severity = min(1.0, abs(loss_delta) / (2 * self.LOSS_TREND_THRESHOLD))
            return max(0.0, 0.5 - severity)

        # Fallback: neutral
        return 0.5

    def _emit_consistency_audit_event(self, result: ConsistencyCheckResult) -> None:
        """Emit audit event for consistency check (GDPR Art. 30)."""
        if not self.audit_backend:
            return

        audit_event = {
            **result.to_dict(),
            "lom": "consistency_checker.validate_consistency",  # Line of Moral Responsibility
        }
        try:
            self.audit_backend.write_event(audit_event)
        except Exception as e:
            logger.error(f"Failed to write consistency check audit event: {e}")

    def _emit_contradiction_event(
        self,
        feedback_id: str,
        skill_id: str,
        task_id: str,
        consistency_score: float,
        feedback_signal: FeedbackSignal,
        expected_signal: FeedbackSignal,
        loss_trend: str,
        tenant_id: str,
    ) -> None:
        """Emit contradiction event for downweighting in backprop (fail-closed)."""
        if not self.audit_backend:
            return

        downweight_factor = 1.0 - consistency_score

        contradiction = FeedbackContradictionEvent(
            feedback_id=feedback_id,
            skill_id=skill_id,
            task_id=task_id,
            consistency_score=consistency_score,
            feedback_signal=feedback_signal,
            expected_signal=expected_signal,
            loss_trend=loss_trend,
            downweight_factor=downweight_factor,
            tenant_id=tenant_id,
        )

        audit_event = {
            **contradiction.to_dict(),
            "lom": "consistency_checker.emit_contradiction_event",  # LoM
        }
        try:
            self.audit_backend.write_event(audit_event)
        except Exception as e:
            logger.error(f"Failed to write contradiction event: {e}")


# ============================================================================
# Integration: Backprop Downweighting
# ============================================================================


def apply_consistency_downweighting(
    feedback_weight: float,
    consistency_score: float,
) -> float:
    """Apply consistency-based downweighting to feedback weight in backprop.

    Args:
        feedback_weight: Original weight (e.g., 1.0)
        consistency_score: Score [0, 1] from consistency check

    Returns:
        Downweighted feedback weight: feedback_weight * consistency_score
    """
    downweighted = feedback_weight * consistency_score
    logger.info(
        f"Applied consistency downweighting: "
        f"{feedback_weight:.3f} → {downweighted:.3f} (score={consistency_score:.2f})"
    )
    return downweighted


# The inline ``test_*`` demo block that used to live here was deleted on
# 2026-09-07 (round-3 review): every one of its mock stores defined
# ``get_events``, an API this module stopped calling in round 2, so the block
# exercised nothing but its own mocks and drifted further on every change. The
# real coverage is ``core/learning/tests/test_consistency_checker.py``, which
# drives the validator through the real ``EventStore`` on disk.
