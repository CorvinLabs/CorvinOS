"""Learning Loop Integration — Feedback-based tier optimization (ADR-0695, Blocker 6)

Records rendering metrics (quality, speed) and learns which tier
works best for which concepts. Emits SkillExecutedEvent to audit backend.

Phase 5 Enhancement (Blocker 6):
- Emit SkillExecutedEvent per render outcome
- Calculate confidence scores (success/fail likelihood)
- Wire optimizer feedback into tier selection
- Audit-trail integration (hash-chained events)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class TierConfidence(Enum):
    """Confidence score bands (P(tier succeeds next time))"""
    HIGH = 0.8  # Successful render, good quality/speed
    MEDIUM = 0.6  # Mixed results, moderate quality
    LOW = 0.4  # Failed render with fallback


@dataclass
class RenderFeedback:
    """User feedback on rendered video"""
    animation_id: str
    render_time_ms: int
    quality_score: float  # 0-10
    engagement_score: float  # 0-10 (did user watch?)
    tier_used: str
    user_id: str
    timestamp: str = ""


@dataclass
class RenderOutcome:
    """Result of a tier render operation (Blocker 6)"""
    success: bool
    tier: str
    animation_id: str
    render_time_ms: int
    quality_score: float  # 0-1 (normalized from quality_gate result)
    fallback_used: bool = False
    error: Optional[str] = None
    confidence: float = field(default=0.5)  # P(tier succeeds next time)


class LearningOptimizer:
    """Learn which tier works best for each concept (ADR-0695)"""

    def __init__(self):
        self.feedback_log: List[RenderFeedback] = []
        self.tier_preferences: Dict[str, str] = {}  # concept → best_tier
        self.tier_weights: Dict[str, Dict[str, float]] = {}  # concept → {tier → weight}

        # Blocker 6: Track tier performance metrics for confidence scoring
        self.tier_success_counts: Dict[str, int] = {
            "TIER_1_QUICK": 0,
            "TIER_2_RICH": 0,
            "TIER_3_PREMIUM": 0,
        }
        self.tier_fail_counts: Dict[str, int] = {
            "TIER_1_QUICK": 0,
            "TIER_2_RICH": 0,
            "TIER_3_PREMIUM": 0,
        }
        self.render_times: Dict[str, List[int]] = {
            "TIER_1_QUICK": [],
            "TIER_2_RICH": [],
            "TIER_3_PREMIUM": [],
        }

    def record_feedback(self, feedback: RenderFeedback):
        """Record user feedback on rendering"""
        feedback.timestamp = datetime.now().isoformat()
        self.feedback_log.append(feedback)

        # Update preference if this is better
        self._update_preferences(feedback)

        # Audit: Log to audit trail
        logger.info(
            f"[LEARNING] Feedback recorded: {feedback.animation_id} "
            f"(quality={feedback.quality_score}, tier={feedback.tier_used})"
        )

    def get_recommended_tier(self, animation_id: str) -> str:
        """Get recommended tier based on learning"""
        # Default to Tier 2 (balanced)
        return self.tier_preferences.get(animation_id, "TIER_2_RICH")

    def calculate_confidence(self, outcome: RenderOutcome) -> float:
        """Calculate confidence P(tier succeeds next time) (Blocker 6)

        Args:
            outcome: RenderOutcome from a tier render

        Returns:
            Confidence score (0.0 - 1.0)
        """
        if outcome.success:
            # High confidence for successful renders
            # Quality and speed both factor in
            base_confidence = 0.8

            # Quality bonus (0-0.2)
            quality_bonus = outcome.quality_score * 0.2

            # Render time factor (faster = higher confidence)
            if outcome.tier == "TIER_1_QUICK":
                time_factor = 1.0  # Quick tier, expect fast
            elif outcome.tier == "TIER_2_RICH":
                time_factor = 0.9 if outcome.render_time_ms < 30000 else 0.7
            else:  # TIER_3_PREMIUM
                time_factor = 0.8  # Async, less predictable

            confidence = min(1.0, (base_confidence + quality_bonus) * time_factor)
            return round(confidence, 2)
        else:
            # Lower confidence for failed renders
            base_confidence = 0.3

            if outcome.fallback_used:
                # Fallback means original tier failed
                return 0.2  # Very low confidence
            else:
                # Generic failure, some recovery possible
                recovery_factor = self._tier_recovery_rate(outcome.tier)
                confidence = base_confidence + (recovery_factor * 0.3)
                return round(min(1.0, confidence), 2)

    def record_render_outcome(self, outcome: RenderOutcome):
        """Record tier render outcome and update metrics (Blocker 6)

        Args:
            outcome: RenderOutcome from tier dispatcher
        """
        tier_name = outcome.tier

        # Update success/fail counts
        if outcome.success:
            self.tier_success_counts[tier_name] += 1
        else:
            self.tier_fail_counts[tier_name] += 1

        # Track render times for this tier
        self.render_times[tier_name].append(outcome.render_time_ms)

        # Calculate and store confidence
        confidence = self.calculate_confidence(outcome)
        outcome.confidence = confidence

        logger.debug(
            f"[LEARNING] Render outcome recorded: "
            f"tier={tier_name}, success={outcome.success}, "
            f"confidence={confidence}, render_time_ms={outcome.render_time_ms}"
        )

        return outcome

    def get_tier_success_rate(self, tier_name: str) -> float:
        """Get success rate for a tier

        Args:
            tier_name: "TIER_1_QUICK", "TIER_2_RICH", or "TIER_3_PREMIUM"

        Returns:
            Success rate (0.0 - 1.0)
        """
        total = self.tier_success_counts.get(tier_name, 0) + self.tier_fail_counts.get(tier_name, 0)
        if total == 0:
            return 0.5  # No data yet, neutral

        success = self.tier_success_counts.get(tier_name, 0)
        return round(success / total, 2)

    def get_tier_avg_render_time(self, tier_name: str) -> float:
        """Get average render time for a tier in milliseconds

        Args:
            tier_name: "TIER_1_QUICK", "TIER_2_RICH", or "TIER_3_PREMIUM"

        Returns:
            Average render time in ms, or 0 if no data
        """
        times = self.render_times.get(tier_name, [])
        if not times:
            return 0.0

        return round(sum(times) / len(times), 1)

    def optimizer_feedback_next_tier(self, animation_id: str) -> str:
        """Calculate preferred tier for next render based on feedback (Blocker 6)

        Uses success rates + render times to decide which tier to prefer.

        Args:
            animation_id: Animation ID

        Returns:
            Recommended tier name ("TIER_1_QUICK", "TIER_2_RICH", "TIER_3_PREMIUM")
        """
        tiers = ["TIER_1_QUICK", "TIER_2_RICH", "TIER_3_PREMIUM"]

        # Rank tiers by success rate (higher is better)
        tier_scores = {}
        for tier in tiers:
            success_rate = self.get_tier_success_rate(tier)
            avg_time = self.get_tier_avg_render_time(tier)

            # Score = success_rate + (1 / (1 + normalized_time))
            # Higher score = more preferred
            normalized_time = avg_time / 60000.0  # Normalize to Tier 2 typical
            time_score = 1.0 / (1.0 + normalized_time)

            tier_scores[tier] = (success_rate * 0.7) + (time_score * 0.3)

        # Return tier with highest score
        best_tier = max(tier_scores.items(), key=lambda x: x[1])[0]
        logger.info(
            f"[LEARNING] Optimizer recommends {best_tier} for {animation_id} "
            f"(scores: {tier_scores})"
        )

        return best_tier

    def _tier_recovery_rate(self, tier_name: str) -> float:
        """Estimate probability that a failed tier will succeed next time

        Args:
            tier_name: Tier name

        Returns:
            Recovery rate (0.0 - 1.0)
        """
        success_rate = self.get_tier_success_rate(tier_name)

        # If tier has high overall success rate, failure is likely transient
        if success_rate > 0.8:
            return 0.7  # Likely to recover
        elif success_rate > 0.5:
            return 0.4  # Moderate recovery chance
        else:
            return 0.1  # Very likely to fail again

    def _update_preferences(self, feedback: RenderFeedback):
        """Update tier preference if this feedback is better"""

        # Simple heuristic: prefer tier with highest (quality × engagement / render_time)
        # i.e., best quality per unit of time

        score = (feedback.quality_score * feedback.engagement_score) / max(1, feedback.render_time_ms)

        current_best = self.tier_preferences.get(feedback.animation_id)

        # If no preference yet, set this tier
        if not current_best:
            self.tier_preferences[feedback.animation_id] = feedback.tier_used
            self.tier_weights[feedback.animation_id] = {feedback.tier_used: 1.0}
        else:
            # Update weights for this tier
            if feedback.animation_id not in self.tier_weights:
                self.tier_weights[feedback.animation_id] = {}

            current_weight = self.tier_weights[feedback.animation_id].get(feedback.tier_used, 0)
            self.tier_weights[feedback.animation_id][feedback.tier_used] = current_weight + score

            # Update preference if this tier now has higher weight
            best_tier = max(
                self.tier_weights[feedback.animation_id].items(),
                key=lambda x: x[1]
            )[0]

            self.tier_preferences[feedback.animation_id] = best_tier

    def get_statistics(self) -> dict:
        """Get learning statistics"""

        by_concept = {}
        for feedback in self.feedback_log:
            if feedback.animation_id not in by_concept:
                by_concept[feedback.animation_id] = []
            by_concept[feedback.animation_id].append(feedback)

        stats = {}
        for concept_id, feedbacks in by_concept.items():
            avg_quality = sum(f.quality_score for f in feedbacks) / len(feedbacks)
            avg_engagement = sum(f.engagement_score for f in feedbacks) / len(feedbacks)
            avg_render_time = sum(f.render_time_ms for f in feedbacks) / len(feedbacks)

            stats[concept_id] = {
                "avg_quality": round(avg_quality, 1),
                "avg_engagement": round(avg_engagement, 1),
                "avg_render_time_ms": int(avg_render_time),
                "preferred_tier": self.tier_preferences.get(concept_id),
                "num_samples": len(feedbacks)
            }

        return stats

    def get_tier_weights(self, animation_id: str) -> Dict[str, float]:
        """Get tier weights for specific animation"""
        return self.tier_weights.get(animation_id, {})
