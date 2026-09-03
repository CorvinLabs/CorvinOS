"""Operator Feedback Loop Integration (Gap 7, ADR-0327).

Integrates operator ratings (tools and skills) into the learning system:
1. Records operator ratings as ``EventType.FEEDBACK`` learning events
2. Aggregates feedback (average rating, sample size, outlier detection)
3. Feeds aggregated feedback to auto-promotion mechanisms
4. Stores feedback metadata for audit trail

Storage contract (ONE pair, never mixed): events are
``core.learning.learning_events.LearningEvent`` persisted by
``core.learning.event_store.EventStore`` (the store ``EventEmitter`` writes
to). A rating is a FEEDBACK event whose ``skill_id`` is ``tool:<tool_id>`` /
``skill:<skill_id>`` and whose ``signal`` carries the rating.

Tenant-scoped: every query goes through ``EventStore.query_events(tenant_id)``
(GDPR Art. 5, 32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from statistics import mean, stdev

from .learning_events import LearningEvent, EventType
from .event_store import EventStore
from .event_emitter import EventEmitter

logger = logging.getLogger(__name__)

RATING_KIND_TOOL = "operator_rated_tool"
RATING_KIND_SKILL = "operator_rated_skill"


def tool_subject_id(tool_id: str) -> str:
    return f"tool:{tool_id}"


def skill_subject_id(skill_id: str) -> str:
    return f"skill:{skill_id}"


@dataclass(frozen=True)
class FeedbackStats:
    """Aggregated feedback statistics for an entity."""

    entity_id: str
    entity_type: str  # "tool" or "skill"
    entity_name: str
    sample_count: int
    average_rating: float  # 1.0-5.0
    median_rating: float
    std_dev: Optional[float]  # None if sample_count < 2
    min_rating: int
    max_rating: int
    confidence: float  # 0.0-1.0, higher with more samples
    window_days: int
    timestamp_utc: datetime
    feedback_sentiment: str  # "negative" | "neutral" | "positive" | "very_positive"


@dataclass
class OutlierStats:
    """Outlier detection result."""

    is_outlier: bool
    z_score: Optional[float]  # None if only 1 sample
    reason: Optional[str]  # Why it was flagged as outlier
    should_exclude: bool  # True if outlier should be excluded from aggregation


class OutlierDetector:
    """Detect and handle outliers in feedback ratings.

    Uses Z-score method with 2.5 sigma threshold:
    - Z-score > 2.5 → outlier
    - Z-score < -2.5 → outlier
    """

    Z_SCORE_THRESHOLD = 2.5

    @staticmethod
    def detect_outlier(
        rating: int,
        existing_ratings: List[int],
        minimum_sample_size: int = 5,
    ) -> OutlierStats:
        """Detect if a rating is an outlier."""
        if len(existing_ratings) < minimum_sample_size:
            return OutlierStats(
                is_outlier=False,
                z_score=None,
                reason="Insufficient historical data",
                should_exclude=False,
            )

        try:
            avg = mean(existing_ratings)
            sigma = stdev(existing_ratings)

            if sigma == 0:
                if abs(rating - avg) > 1:
                    return OutlierStats(
                        is_outlier=True,
                        z_score=None,
                        reason=f"New rating {rating} differs from uniform existing ratings (all {avg})",
                        should_exclude=False,
                    )
                return OutlierStats(
                    is_outlier=False,
                    z_score=None,
                    reason="Existing ratings have no variance",
                    should_exclude=False,
                )

            z_score = (rating - avg) / sigma

            if abs(z_score) > OutlierDetector.Z_SCORE_THRESHOLD:
                return OutlierStats(
                    is_outlier=True,
                    z_score=z_score,
                    reason=f"Z-score {z_score:.2f} exceeds threshold {OutlierDetector.Z_SCORE_THRESHOLD}",
                    should_exclude=False,
                )
            return OutlierStats(
                is_outlier=False,
                z_score=z_score,
                reason=None,
                should_exclude=False,
            )

        except Exception as e:
            logger.warning(f"Outlier detection failed: {e}")
            return OutlierStats(
                is_outlier=False,
                z_score=None,
                reason=f"Detection error: {e}",
                should_exclude=False,
            )


class FeedbackAggregator:
    """Aggregate operator feedback ratings by entity and time window."""

    # Confidence model: maps sample_count -> confidence_score (0.0-1.0)
    CONFIDENCE_THRESHOLDS = {
        1: 0.2,
        2: 0.3,
        3: 0.4,
        5: 0.6,
        10: 0.8,
        20: 0.95,
        30: 1.0,
    }

    @staticmethod
    def compute_confidence(sample_count: int) -> float:
        """Compute confidence score based on sample count."""
        for threshold, confidence in sorted(FeedbackAggregator.CONFIDENCE_THRESHOLDS.items()):
            if sample_count <= threshold:
                return confidence
        return 1.0

    @staticmethod
    def classify_sentiment(average_rating: float) -> str:
        """Classify sentiment based on average rating."""
        if average_rating < 2.0:
            return "negative"
        elif average_rating < 3.0:
            return "neutral"
        elif average_rating < 4.0:
            return "positive"
        else:
            return "very_positive"

    @staticmethod
    def aggregate_feedback(
        entity_id: str,
        entity_type: str,  # "tool" or "skill"
        entity_name: str,
        ratings: List[int],  # All 1-5 ratings
        window_days: int = 7,
    ) -> FeedbackStats:
        """Aggregate feedback ratings into statistics."""
        if not ratings:
            return FeedbackStats(
                entity_id=entity_id,
                entity_type=entity_type,
                entity_name=entity_name,
                sample_count=0,
                average_rating=3.0,  # Neutral default
                median_rating=3.0,
                std_dev=None,
                min_rating=0,
                max_rating=0,
                confidence=0.0,
                window_days=window_days,
                timestamp_utc=datetime.now(timezone.utc),
                feedback_sentiment="neutral",
            )

        sorted_ratings = sorted(ratings)
        sample_count = len(ratings)
        average = mean(ratings)
        median = sorted_ratings[sample_count // 2]

        if sample_count < 2:
            std_dev = None
        else:
            try:
                std_dev = stdev(ratings)
            except Exception:
                std_dev = None

        confidence = FeedbackAggregator.compute_confidence(sample_count)
        sentiment = FeedbackAggregator.classify_sentiment(average)

        return FeedbackStats(
            entity_id=entity_id,
            entity_type=entity_type,
            entity_name=entity_name,
            sample_count=sample_count,
            average_rating=average,
            median_rating=float(median),
            std_dev=std_dev,
            min_rating=sorted_ratings[0],
            max_rating=sorted_ratings[-1],
            confidence=confidence,
            window_days=window_days,
            timestamp_utc=datetime.now(timezone.utc),
            feedback_sentiment=sentiment,
        )


def build_rating_event(
    *,
    kind: str,
    entity_id: str,
    entity_name: str,
    rating: int,
    tenant_id: str,
    feedback_text: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    instance_id: str = "unknown",
    lom: Optional[str] = None,
) -> LearningEvent:
    """Build the FEEDBACK learning event for an operator rating."""
    if kind == RATING_KIND_TOOL:
        subject = tool_subject_id(entity_id)
        id_key, name_key = "tool_id", "tool_name"
    elif kind == RATING_KIND_SKILL:
        subject = skill_subject_id(entity_id)
        id_key, name_key = "skill_id", "skill_name"
    else:
        raise ValueError(f"unknown rating kind {kind!r}")
    return LearningEvent.create(
        event_type=EventType.FEEDBACK,
        skill_id=subject,
        tenant_id=tenant_id,
        signal={
            "kind": kind,
            id_key: entity_id,
            name_key: entity_name,
            "rating": rating,
            "feedback_text": feedback_text,
            "task_id": task_id,
            "session_id": session_id,
            "instance_id": instance_id,
        },
        lom=lom,
    )


class OperatorFeedbackHandler:
    """Subsystem for collecting and processing operator feedback."""

    def __init__(
        self,
        event_store: EventStore,
        min_sample_size: int = 3,
        event_emitter: Optional[EventEmitter] = None,
    ):
        """Initialize feedback handler.

        Args:
            event_store: ``event_store.EventStore`` (tenant_home-based) for queries
                and — when no emitter is given — for writes.
            min_sample_size: Minimum ratings needed before aggregation (default 3)
            event_emitter: EventEmitter (non-blocking queue in front of the SAME
                store, ADR-0314). ``emit()`` is synchronous and returns ``False``
                when the event was dropped; a drop is an error here — a rating
                the operator entered must never vanish silently.
        """
        self.event_store = event_store
        self.event_emitter = event_emitter
        self.min_sample_size = min_sample_size
        self._aggregate_cache: Dict[str, FeedbackStats] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 300  # 5-minute cache TTL

    # ── record ──────────────────────────────────────────────────────────

    def _persist(self, event: LearningEvent) -> None:
        if self.event_emitter is not None:
            if not self.event_emitter.emit(event):
                raise RuntimeError(
                    f"learning event {event.event_id} dropped by EventEmitter (queue full or stopped)"
                )
        else:
            self.event_store.write_event(event)

    def record_tool_rating(
        self,
        tool_id: str,
        tool_name: str,
        rating: int,
        tenant_id: str = "_default",
        feedback_text: Optional[str] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        instance_id: str = "unknown",
    ) -> LearningEvent:
        """Record an operator rating for a tool. Returns the persisted event."""
        if not 1 <= rating <= 5:
            raise ValueError(f"Rating must be 1-5, got {rating}")

        event = build_rating_event(
            kind=RATING_KIND_TOOL,
            entity_id=tool_id,
            entity_name=tool_name,
            rating=rating,
            tenant_id=tenant_id,
            feedback_text=feedback_text,
            task_id=task_id,
            session_id=session_id,
            instance_id=instance_id,
            lom=f"{__name__}:OperatorFeedbackHandler.record_tool_rating",
        )
        try:
            self._persist(event)
            logger.info(
                f"Recorded tool rating: tool_id={tool_id}, rating={rating}, tenant={tenant_id}"
            )
        except Exception as e:
            logger.error(f"Failed to record tool rating: {e}")
            raise

        self._invalidate_cache()
        return event

    def record_skill_rating(
        self,
        skill_id: str,
        skill_name: str,
        rating: int,
        tenant_id: str = "_default",
        feedback_text: Optional[str] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        instance_id: str = "unknown",
    ) -> LearningEvent:
        """Record an operator rating for a skill. Returns the persisted event."""
        if not 1 <= rating <= 5:
            raise ValueError(f"Rating must be 1-5, got {rating}")

        event = build_rating_event(
            kind=RATING_KIND_SKILL,
            entity_id=skill_id,
            entity_name=skill_name,
            rating=rating,
            tenant_id=tenant_id,
            feedback_text=feedback_text,
            task_id=task_id,
            session_id=session_id,
            instance_id=instance_id,
            lom=f"{__name__}:OperatorFeedbackHandler.record_skill_rating",
        )
        try:
            self._persist(event)
            logger.info(
                f"Recorded skill rating: skill_id={skill_id}, rating={rating}, tenant={tenant_id}"
            )
        except Exception as e:
            logger.error(f"Failed to record skill rating: {e}")
            raise

        self._invalidate_cache()
        return event

    # ── query ───────────────────────────────────────────────────────────

    def _ratings_in_window(
        self, *, tenant_id: str, subject: str, kind: str, id_key: str, name_key: str,
        entity_id: str, window_days: int,
    ) -> Tuple[List[int], str]:
        """Collect (ratings, entity_name) for one entity inside the window."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        events = self.event_store.query_events(
            tenant_id, event_type=EventType.FEEDBACK, skill_id=subject, limit=10000
        )
        ratings: List[int] = []
        entity_name = entity_id
        for event in events:
            signal = event.signal or {}
            if signal.get("kind") != kind or signal.get(id_key) != entity_id:
                continue
            ts = datetime.fromisoformat(event.timestamp.rstrip("Z"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue
            rating = signal.get("rating")
            if isinstance(rating, int) and 1 <= rating <= 5:
                ratings.append(rating)
                entity_name = signal.get(name_key) or entity_id
        return ratings, entity_name

    def get_tool_feedback_stats(
        self,
        tool_id: str,
        tenant_id: str = "_default",
        window_days: int = 7,
        use_cache: bool = True,
    ) -> FeedbackStats:
        """Get aggregated feedback statistics for a tool."""
        cache_key = f"tool:{tool_id}:{tenant_id}:{window_days}"
        if use_cache and self._is_cache_valid() and cache_key in self._aggregate_cache:
            return self._aggregate_cache[cache_key]

        ratings, tool_name = self._ratings_in_window(
            tenant_id=tenant_id, subject=tool_subject_id(tool_id), kind=RATING_KIND_TOOL,
            id_key="tool_id", name_key="tool_name", entity_id=tool_id, window_days=window_days,
        )
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id=tool_id,
            entity_type="tool",
            entity_name=tool_name,
            ratings=ratings,
            window_days=window_days,
        )
        if use_cache:
            self._aggregate_cache[cache_key] = stats
            self._cache_timestamp = datetime.now(timezone.utc)
        return stats

    def get_skill_feedback_stats(
        self,
        skill_id: str,
        tenant_id: str = "_default",
        window_days: int = 7,
        use_cache: bool = True,
    ) -> FeedbackStats:
        """Get aggregated feedback statistics for a skill."""
        cache_key = f"skill:{skill_id}:{tenant_id}:{window_days}"
        if use_cache and self._is_cache_valid() and cache_key in self._aggregate_cache:
            return self._aggregate_cache[cache_key]

        ratings, skill_name = self._ratings_in_window(
            tenant_id=tenant_id, subject=skill_subject_id(skill_id), kind=RATING_KIND_SKILL,
            id_key="skill_id", name_key="skill_name", entity_id=skill_id, window_days=window_days,
        )
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id=skill_id,
            entity_type="skill",
            entity_name=skill_name,
            ratings=ratings,
            window_days=window_days,
        )
        if use_cache:
            self._aggregate_cache[cache_key] = stats
            self._cache_timestamp = datetime.now(timezone.utc)
        return stats

    # ── promotion ───────────────────────────────────────────────────────

    def compute_promotion_adjustment(
        self,
        feedback_stats: FeedbackStats,
        base_threshold: float = 0.7,
    ) -> Tuple[float, str]:
        """Compute auto-promotion threshold adjustment based on feedback."""
        if feedback_stats.sample_count < self.min_sample_size:
            return (base_threshold, "Insufficient feedback samples")

        adjustment = 0.0
        reason_parts = []

        sentiment = feedback_stats.feedback_sentiment
        confidence = feedback_stats.confidence

        if sentiment == "very_positive" and confidence >= 0.5:
            adjustment = -0.15 * confidence
            reason_parts.append(f"very_positive_feedback_{int(confidence*100)}pct_confidence")
        elif sentiment == "positive" and confidence >= 0.7:
            adjustment = -0.05 * confidence
            reason_parts.append(f"positive_feedback_{int(confidence*100)}pct_confidence")
        elif sentiment == "negative" and confidence >= 0.5:
            adjustment = 0.15 * confidence
            reason_parts.append(f"negative_feedback_{int(confidence*100)}pct_confidence")

        adjusted_threshold = max(0.3, min(0.95, base_threshold + adjustment))
        reason = ", ".join(reason_parts) or "neutral_feedback"
        return (adjusted_threshold, reason)

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid (within TTL)."""
        if self._cache_timestamp is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self._cache_timestamp).total_seconds()
        return elapsed < self._cache_ttl_seconds

    def _invalidate_cache(self) -> None:
        """Invalidate the aggregate cache."""
        self._aggregate_cache.clear()
        self._cache_timestamp = None
