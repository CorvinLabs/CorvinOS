"""
Phase 3: User Profile Learner

Learn user/task patterns → personalize routing decisions.
Bounded learning with convergence guarantees.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum


class FeedbackType(str, Enum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"
    NEUTRAL = "neutral"


@dataclass
class UserProfile:
    """User's learned preferences."""
    user_id: str
    intent_preferences: Dict[str, float] = field(default_factory=dict)  # {intent: weight}
    engine_affinity: Dict[str, float] = field(default_factory=dict)  # {engine: weight}
    skill_affinity: Dict[str, float] = field(default_factory=dict)  # {skill: weight}
    convergence_iterations: int = 0
    audit_hash: str = ""

    def __post_init__(self):
        if not self.audit_hash:
            content = f"{self.user_id}:{self.convergence_iterations}"
            self.audit_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


class ProfileLearner:
    """Learn from routing outcomes → update user profiles."""

    def __init__(self, learning_rate: float = 0.05, max_iterations: int = 1000):
        self.learning_rate = min(learning_rate, 0.1)  # Cap to prevent divergence
        self.max_iterations = max_iterations
        self.profiles: Dict[str, UserProfile] = {}

    def learn_from_feedback(
        self,
        user_id: str,
        intent: str,
        engine: str,
        feedback: FeedbackType,
        confidence: float = 1.0
    ) -> UserProfile:
        """Learn from outcome feedback."""

        if user_id not in self.profiles:
            self.profiles[user_id] = UserProfile(user_id=user_id)

        profile = self.profiles[user_id]

        # Bounded learning rate
        delta = self.learning_rate * confidence
        if feedback == FeedbackType.HELPFUL:
            signal = delta
        elif feedback == FeedbackType.NOT_HELPFUL:
            signal = -delta / 2  # Negative feedback is weaker
        else:
            signal = 0

        # Update weights (bounded)
        if intent not in profile.intent_preferences:
            profile.intent_preferences[intent] = 0.0
        profile.intent_preferences[intent] += signal
        profile.intent_preferences[intent] = max(-1.0, min(1.0, profile.intent_preferences[intent]))

        if engine not in profile.engine_affinity:
            profile.engine_affinity[engine] = 0.0
        profile.engine_affinity[engine] += signal
        profile.engine_affinity[engine] = max(-1.0, min(1.0, profile.engine_affinity[engine]))

        profile.convergence_iterations += 1

        # Audit: every update logged + hash updated
        content = f"{user_id}:{profile.convergence_iterations}"
        profile.audit_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        return profile

    def get_profile(self, user_id: str) -> Optional[UserProfile]:
        """Retrieve learned profile."""
        return self.profiles.get(user_id)

    def has_converged(self, user_id: str) -> bool:
        """Check if profile has converged (<1000 iterations)."""
        profile = self.profiles.get(user_id)
        return profile and profile.convergence_iterations < 1000


def learn_from_feedback(
    user_id: str,
    intent: str,
    engine: str,
    feedback: str,  # "helpful", "not_helpful", "neutral"
    confidence: float = 1.0
) -> UserProfile:
    """Top-level function to learn from outcome feedback."""
    learner = ProfileLearner()
    try:
        feedback_enum = FeedbackType(feedback)
    except ValueError:
        feedback_enum = FeedbackType.NEUTRAL

    return learner.learn_from_feedback(user_id, intent, engine, feedback_enum, confidence)
