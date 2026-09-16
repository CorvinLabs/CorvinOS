"""Style Learner for Director Mode Phase 3

Per-user preference profiles (JSON-persistent).
Stateless API; all state stored in profile files.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import datetime
import json
import logging

from .feedback_schema import FeedbackEvent, FeedbackType, get_feedback_config

logger = logging.getLogger(__name__)


@dataclass
class StyleProfile:
    """Per-user style preferences (JSON-persistent)."""
    user_id: str

    # Style parameters [0.0, 1.0] (except overall_speed and average_scene_duration)
    animation_level: float = 0.5
    text_density: float = 0.5
    color_brightness: float = 0.5
    music_presence: float = 0.5
    dramatic_intensity: float = 0.5
    technical_depth: float = 0.5

    # Speed multiplier [0.5, 2.0]
    overall_speed: float = 1.0

    # Duration [1.0, 30.0] seconds
    average_scene_duration: float = 5.0

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    feedback_count: int = 0
    videos_produced: int = 0

    # Feedback history (immutable audit trail)
    feedback_history: List[FeedbackEvent] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Convert to dict (for JSON serialization)."""
        data = asdict(self)
        # Convert FeedbackEvent objects to dicts
        data['feedback_history'] = [
            {
                'user_id': fe.user_id,
                'feedback_type': fe.feedback_type.value,
                'timestamp': fe.timestamp,
                'video_id': fe.video_id,
                'notes': fe.notes,
            }
            for fe in self.feedback_history
        ]
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "StyleProfile":
        """Reconstruct from dict (after JSON deserialization)."""
        # Convert feedback history back to FeedbackEvent objects
        feedback_history = []
        for fe_dict in data.pop('feedback_history', []):
            feedback_history.append(
                FeedbackEvent(
                    user_id=fe_dict['user_id'],
                    feedback_type=FeedbackType(fe_dict['feedback_type']),
                    timestamp=fe_dict['timestamp'],
                    video_id=fe_dict.get('video_id'),
                    notes=fe_dict.get('notes'),
                )
            )
        data['feedback_history'] = feedback_history
        return cls(**data)


class StyleLearner:
    """Style Learning Skill (Phase 3)

    Stateless API for recording feedback and updating profiles.
    All state stored in JSON files (per-user).
    """

    def __init__(self, profile_store_path: str = "~/.corvin/profiles/"):
        """Initialize StyleLearner.

        Args:
            profile_store_path: Directory to store user profiles (JSON)
        """
        self.profile_store_path = profile_store_path
        self._profiles_cache: Dict[str, StyleProfile] = {}  # In-memory cache for testing

    def get_profile(self, user_id: str) -> StyleProfile:
        """Get or create user profile."""
        if user_id in self._profiles_cache:
            return self._profiles_cache[user_id]
        # For now, always return default profile
        # In production, would load from profile_store_path
        profile = StyleProfile(user_id=user_id)
        self._profiles_cache[user_id] = profile
        return profile

    def record_feedback(self, feedback: FeedbackEvent) -> StyleProfile:
        """Record user feedback and update profile.

        Returns updated StyleProfile (not persisted in this implementation).
        """
        profile = self.get_profile(feedback.user_id)

        # Validate feedback type
        if feedback.feedback_type not in FeedbackType.__members__.values():
            raise ValueError(f"Invalid feedback type: {feedback.feedback_type}")

        config = get_feedback_config(feedback.feedback_type)
        target_param = config['target']
        delta = config['delta']
        min_val, max_val = config['clamp_range']

        # Update parameter with clamping
        current_value = getattr(profile, target_param, 0.5)
        new_value = current_value + delta
        new_value = max(min_val, min(max_val, new_value))  # Clamp
        setattr(profile, target_param, new_value)

        # Update metadata
        profile.feedback_count += 1
        profile.last_updated = datetime.utcnow().isoformat()
        profile.feedback_history.append(feedback)

        logger.info(
            f"Feedback recorded for user {feedback.user_id}: "
            f"{target_param} → {new_value:.2f} (delta={delta})"
        )

        # Update cache
        self._profiles_cache[feedback.user_id] = profile

        return profile

    def get_applied_style(self, profile: StyleProfile) -> Dict[str, float]:
        """Convert profile to style directives for rendering.

        Returns dict of parameters that should be applied to video production.
        """
        return {
            'animation_level': profile.animation_level,
            'text_density': profile.text_density,
            'color_brightness': profile.color_brightness,
            'music_presence': profile.music_presence,
            'dramatic_intensity': profile.dramatic_intensity,
            'technical_depth': profile.technical_depth,
            'overall_speed': profile.overall_speed,
            'average_scene_duration': profile.average_scene_duration,
        }
