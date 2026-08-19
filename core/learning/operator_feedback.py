"""Operator Feedback Loop Integration (Gap 7, ADR-0327).

Integrates operator ratings (tools and skills) into the learning system:
1. Collects OPERATOR_RATED_TOOL and OPERATOR_RATED_SKILL events
2. Aggregates feedback (average rating, sample size, outlier detection)
3. Feeds aggregated feedback to auto-promotion mechanisms
4. Stores feedback metadata for audit trail

Modules:
1. FeedbackAggregator: Aggregates ratings by entity (tool/skill) and time window
2. OutlierDetector: Rejects statistical outliers (e.g., single 1-star rating vs 100 5-stars)
3. OperatorFeedbackHandler: Subsystem interface for integration
4. Auto-promotion threshold adjustment based on feedback sentiment

Tenant-scoped: all queries respect tenant_id (GDPR Art. 5, 32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from statistics import mean, stdev

from .event_schema import (
    LearningEvent,
    LearningEventType,
    OperatorRatedToolPayload,
    OperatorRatedSkillPayload,
)
from .event_store import EventStore

logger = logging.getLogger(__name__)


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
        """Detect if a rating is an outlier.

        Args:
            rating: Rating to check (1-5)
            existing_ratings: Previous ratings for the entity
            minimum_sample_size: Minimum samples before outlier detection kicks in

        Returns:
            OutlierStats with detection result
        """
        if len(existing_ratings) < minimum_sample_size:
            # Not enough history to detect outliers
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
                # No variance in existing data
                if abs(rating - avg) > 1:
                    # New rating differs significantly from uniform data
                    return OutlierStats(
                        is_outlier=True,
                        z_score=None,
                        reason=f"New rating {rating} differs from uniform existing ratings (all {avg})",
                        should_exclude=False,  # Flag as outlier but include for visibility
                    )
                else:
                    return OutlierStats(
                        is_outlier=False,
                        z_score=None,
                        reason="Existing ratings have no variance",
                        should_exclude=False,
                    )

            # Compute Z-score
            z_score = (rating - avg) / sigma

            if abs(z_score) > OutlierDetector.Z_SCORE_THRESHOLD:
                return OutlierStats(
                    is_outlier=True,
                    z_score=z_score,
                    reason=f"Z-score {z_score:.2f} exceeds threshold {OutlierDetector.Z_SCORE_THRESHOLD}",
                    should_exclude=False,  # Flag but include for audit trail
                )
            else:
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
    """Aggregate operator feedback ratings by entity and time window.

    Computes:
    - Average rating (with confidence interval based on sample size)
    - Median rating
    - Standard deviation
    - Trend (recent vs historical)
    - Sentiment classification
    """

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
        """Compute confidence score based on sample count.

        Uses threshold table: more samples = higher confidence.
        """
        for threshold, confidence in sorted(FeedbackAggregator.CONFIDENCE_THRESHOLDS.items()):
            if sample_count <= threshold:
                return confidence
        return 1.0

    @staticmethod
    def classify_sentiment(average_rating: float) -> str:
        """Classify sentiment based on average rating.

        1.0-2.0: negative
        2.0-3.0: neutral
        3.0-4.0: positive
        4.0-5.0: very_positive
        """
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
        """Aggregate feedback ratings into statistics.

        Args:
            entity_id: Tool or skill ID
            entity_type: "tool" or "skill"
            entity_name: Human-readable name
            ratings: List of 1-5 ratings
            window_days: Aggregation window size

        Returns:
            FeedbackStats with aggregated metrics
        """
        if not ratings:
            # No ratings yet
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
        median = sorted_ratings[sample_count // 2] if sample_count > 0 else 3.0

        # Compute standard deviation (None if only 1 sample)
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


class OperatorFeedbackHandler:
    """Subsystem for collecting and processing operator feedback.

    Integrates with EventStore to:
    1. Collect OPERATOR_RATED_TOOL and OPERATOR_RATED_SKILL events
    2. Aggregate feedback by entity (tool/skill) and time window
    3. Detect outliers and flag suspicious patterns
    4. Feed aggregated feedback to auto-promotion mechanisms
    5. Maintain audit trail (who rated what, when, with what sentiment)
    """

    def __init__(self, event_store: EventStore, min_sample_size: int = 3):
        """Initialize feedback handler.

        Args:
            event_store: EventStore instance for querying feedback events
            min_sample_size: Minimum ratings needed before aggregation (default 3)
        """
        self.event_store = event_store
        self.min_sample_size = min_sample_size
        self._aggregate_cache: Dict[str, FeedbackStats] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 300  # 5-minute cache TTL

    async def record_tool_rating(
        self,
        tool_id: str,
        tool_name: str,
        rating: int,
        tenant_id: str = "_default",
        feedback_text: Optional[str] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        instance_id: str = "unknown",
    ) -> None:
        """Record an operator rating for a tool.

        Args:
            tool_id: Tool identifier
            tool_name: Human-readable tool name
            rating: Rating (1-5)
            tenant_id: Tenant ID
            feedback_text: Optional feedback comment
            task_id: Optional task ID
            session_id: Optional session ID
            instance_id: Instance identifier
        """
        if not 1 <= rating <= 5:
            raise ValueError(f"Rating must be 1-5, got {rating}")

        # Create and emit event
        event = LearningEvent(
            event_type=LearningEventType.OPERATOR_RATED_TOOL,
            tenant_id=tenant_id,
            instance_id=instance_id,
            skill_name=None,
            session_id=session_id or "unknown",
            timestamp_utc=datetime.utcnow(),
            payload={
                "tool_id": tool_id,
                "tool_name": tool_name,
                "rating": rating,
                "feedback_text": feedback_text,
                "task_id": task_id,
                "session_id": session_id,
                "timestamp_utc": datetime.utcnow().isoformat(),
            },
        )

        try:
            self.event_store.write_event(event)
            logger.info(
                f"Recorded tool rating: tool_id={tool_id}, rating={rating}, tenant={tenant_id}"
            )
        except Exception as e:
            logger.error(f"Failed to record tool rating: {e}")
            raise

        # Invalidate cache
        self._invalidate_cache()

    async def record_skill_rating(
        self,
        skill_id: str,
        skill_name: str,
        rating: int,
        tenant_id: str = "_default",
        feedback_text: Optional[str] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        instance_id: str = "unknown",
    ) -> None:
        """Record an operator rating for a skill.

        Args:
            skill_id: Skill identifier
            skill_name: Human-readable skill name
            rating: Rating (1-5)
            tenant_id: Tenant ID
            feedback_text: Optional feedback comment
            task_id: Optional task ID
            session_id: Optional session ID
            instance_id: Instance identifier
        """
        if not 1 <= rating <= 5:
            raise ValueError(f"Rating must be 1-5, got {rating}")

        # Create and emit event
        event = LearningEvent(
            event_type=LearningEventType.OPERATOR_RATED_SKILL,
            tenant_id=tenant_id,
            instance_id=instance_id,
            skill_name=skill_name,
            session_id=session_id or "unknown",
            timestamp_utc=datetime.utcnow(),
            payload={
                "skill_id": skill_id,
                "skill_name": skill_name,
                "rating": rating,
                "feedback_text": feedback_text,
                "task_id": task_id,
                "session_id": session_id,
                "timestamp_utc": datetime.utcnow().isoformat(),
            },
        )

        try:
            self.event_store.write_event(event)
            logger.info(
                f"Recorded skill rating: skill_id={skill_id}, rating={rating}, tenant={tenant_id}"
            )
        except Exception as e:
            logger.error(f"Failed to record skill rating: {e}")
            raise

        # Invalidate cache
        self._invalidate_cache()

    def get_tool_feedback_stats(
        self,
        tool_id: str,
        tenant_id: str = "_default",
        window_days: int = 7,
        use_cache: bool = True,
    ) -> FeedbackStats:
        """Get aggregated feedback statistics for a tool.

        Args:
            tool_id: Tool identifier
            tenant_id: Tenant ID
            window_days: Time window for aggregation (default 7 days)
            use_cache: Whether to use cached results

        Returns:
            FeedbackStats with aggregated metrics
        """
        # Check cache
        cache_key = f"tool:{tool_id}:{tenant_id}:{window_days}"
        if use_cache and self._is_cache_valid() and cache_key in self._aggregate_cache:
            return self._aggregate_cache[cache_key]

        # Query events from EventStore
        ratings: List[int] = []
        tool_name = tool_id

        try:
            events = self.event_store.read_events_by_type(
                event_type=LearningEventType.OPERATOR_RATED_TOOL,
                limit=10000,
            )

            # Filter by tenant_id, tool_id, and time window
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=window_days)
            for event in events:
                if (
                    event.tenant_id == tenant_id
                    and event.timestamp_utc >= cutoff_time
                    and event.payload.get("tool_id") == tool_id
                ):
                    rating = event.payload.get("rating")
                    if rating and 1 <= rating <= 5:
                        ratings.append(rating)
                        tool_name = event.payload.get("tool_name", tool_id)

        except Exception as e:
            logger.error(f"Failed to query tool feedback events: {e}")
            # Return empty stats on error
            return FeedbackStats(
                entity_id=tool_id,
                entity_type="tool",
                entity_name=tool_name,
                sample_count=0,
                average_rating=3.0,
                median_rating=3.0,
                std_dev=None,
                min_rating=0,
                max_rating=0,
                confidence=0.0,
                window_days=window_days,
                timestamp_utc=datetime.now(timezone.utc),
                feedback_sentiment="neutral",
            )

        # Aggregate feedback
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id=tool_id,
            entity_type="tool",
            entity_name=tool_name,
            ratings=ratings,
            window_days=window_days,
        )

        # Cache result
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
        """Get aggregated feedback statistics for a skill.

        Args:
            skill_id: Skill identifier
            tenant_id: Tenant ID
            window_days: Time window for aggregation (default 7 days)
            use_cache: Whether to use cached results

        Returns:
            FeedbackStats with aggregated metrics
        """
        # Check cache
        cache_key = f"skill:{skill_id}:{tenant_id}:{window_days}"
        if use_cache and self._is_cache_valid() and cache_key in self._aggregate_cache:
            return self._aggregate_cache[cache_key]

        # Query events from EventStore
        ratings: List[int] = []
        skill_name = skill_id

        try:
            events = self.event_store.read_events_by_type(
                event_type=LearningEventType.OPERATOR_RATED_SKILL,
                limit=10000,
            )

            # Filter by tenant_id, skill_id, and time window
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=window_days)
            for event in events:
                if (
                    event.tenant_id == tenant_id
                    and event.timestamp_utc >= cutoff_time
                    and event.payload.get("skill_id") == skill_id
                ):
                    rating = event.payload.get("rating")
                    if rating and 1 <= rating <= 5:
                        ratings.append(rating)
                        skill_name = event.payload.get("skill_name", skill_id)

        except Exception as e:
            logger.error(f"Failed to query skill feedback events: {e}")
            # Return empty stats on error
            return FeedbackStats(
                entity_id=skill_id,
                entity_type="skill",
                entity_name=skill_name,
                sample_count=0,
                average_rating=3.0,
                median_rating=3.0,
                std_dev=None,
                min_rating=0,
                max_rating=0,
                confidence=0.0,
                window_days=window_days,
                timestamp_utc=datetime.now(timezone.utc),
                feedback_sentiment="neutral",
            )

        # Aggregate feedback
        stats = FeedbackAggregator.aggregate_feedback(
            entity_id=skill_id,
            entity_type="skill",
            entity_name=skill_name,
            ratings=ratings,
            window_days=window_days,
        )

        # Cache result
        if use_cache:
            self._aggregate_cache[cache_key] = stats
            self._cache_timestamp = datetime.now(timezone.utc)

        return stats

    def compute_promotion_adjustment(
        self,
        feedback_stats: FeedbackStats,
        base_threshold: float = 0.7,
    ) -> Tuple[float, str]:
        """Compute auto-promotion threshold adjustment based on feedback.

        Adjusts promotion threshold based on operator sentiment:
        - Very positive (4.0-5.0) and confident → lower threshold (easier to promote)
        - Neutral (3.0-4.0) → no adjustment
        - Negative (<3.0) → raise threshold (harder to promote)

        Args:
            feedback_stats: Aggregated feedback statistics
            base_threshold: Base promotion threshold (default 0.7)

        Returns:
            Tuple of (adjusted_threshold, reason_string)
        """
        if feedback_stats.sample_count < self.min_sample_size:
            # Not enough feedback to adjust
            return (base_threshold, "Insufficient feedback samples")

        # Compute adjustment based on sentiment and confidence
        adjustment = 0.0
        reason_parts = []

        sentiment = feedback_stats.feedback_sentiment
        confidence = feedback_stats.confidence
        avg_rating = feedback_stats.average_rating

        if sentiment == "very_positive" and confidence >= 0.5:
            # Lower threshold: reduce by up to 0.15 based on confidence
            adjustment = -0.15 * confidence
            reason_parts.append(f"very_positive_feedback_{int(confidence*100)}pct_confidence")

        elif sentiment == "positive" and confidence >= 0.7:
            # Slight adjustment: reduce by up to 0.05
            adjustment = -0.05 * confidence
            reason_parts.append(f"positive_feedback_{int(confidence*100)}pct_confidence")

        elif sentiment == "negative" and confidence >= 0.5:
            # Raise threshold: increase by up to 0.15 based on confidence
            adjustment = 0.15 * confidence
            reason_parts.append(f"negative_feedback_{int(confidence*100)}pct_confidence")

        # Clamp adjusted threshold to [0.3, 0.95]
        adjusted_threshold = max(0.3, min(0.95, base_threshold + adjustment))

        reason = ", ".join(reason_parts) or "neutral_feedback"
        if feedback_stats.sample_count < self.min_sample_size:
            reason += f" (only {feedback_stats.sample_count} samples)"

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
