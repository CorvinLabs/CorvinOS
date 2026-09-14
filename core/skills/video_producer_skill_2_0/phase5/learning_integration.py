"""Learning Loop Integration — Feedback-based tier optimization

Records rendering metrics (quality, speed) and learns which tier
works best for which concepts.
"""

from dataclasses import dataclass
from typing import Dict, List
from datetime import datetime


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


class LearningOptimizer:
    """Learn which tier works best for each concept"""

    def __init__(self):
        self.feedback_log: List[RenderFeedback] = []
        self.tier_preferences: Dict[str, str] = {}  # concept → best_tier
        self.tier_weights: Dict[str, Dict[str, float]] = {}  # concept → {tier → weight}

    def record_feedback(self, feedback: RenderFeedback):
        """Record user feedback on rendering"""
        feedback.timestamp = datetime.now().isoformat()
        self.feedback_log.append(feedback)

        # Update preference if this is better
        self._update_preferences(feedback)

        # Audit: Log to audit trail
        print(f"[LEARNING] Feedback recorded: {feedback.animation_id} (quality={feedback.quality_score})")

    def get_recommended_tier(self, animation_id: str) -> str:
        """Get recommended tier based on learning"""

        # Default to Tier 2 (balanced)
        return self.tier_preferences.get(animation_id, "TIER_2_RICH")

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
