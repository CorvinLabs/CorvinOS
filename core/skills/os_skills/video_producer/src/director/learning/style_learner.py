"""Learn and remember user creative preferences.

Maintains per-user style profiles that evolve based on feedback,
enabling personalized video generation that respects user preferences.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
import json

from core.skills.os_skills.video_producer.src.director.learning.feedback_schema import FeedbackSchema


@dataclass
class StyleProfile:
    """User's creative style preferences"""
    user_id: str

    # Animation preferences (0.0 to 1.0)
    animation_level: float = 0.5

    # Text preferences (0.0 = no text, 1.0 = heavy text)
    text_density: float = 0.5

    # Pacing preferences
    overall_speed: float = 1.0  # Multiplier
    average_scene_duration: float = 5.0  # Seconds

    # Color preferences
    color_brightness: float = 0.5  # 0 = dark, 1 = bright

    # Music preferences
    music_presence: float = 0.5  # 0 = none, 1 = heavy

    # Tone preferences
    dramatic_intensity: float = 0.5
    technical_depth: float = 0.5

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    feedback_count: int = 0
    videos_produced: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StyleProfile":
        """Create from dictionary"""
        return cls(**data)

    def get_all_parameters(self) -> Dict[str, float]:
        """Get all learnable parameters"""
        return {
            "animation_level": self.animation_level,
            "text_density": self.text_density,
            "overall_speed": self.overall_speed,
            "average_scene_duration": self.average_scene_duration,
            "color_brightness": self.color_brightness,
            "music_presence": self.music_presence,
            "dramatic_intensity": self.dramatic_intensity,
            "technical_depth": self.technical_depth,
        }


class StyleLearner:
    """Learn and remember user creative preferences"""

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize style learner.

        Args:
            storage_path: Path to store preference profiles (optional)
        """
        self.preference_profiles: Dict[str, StyleProfile] = {}
        self.storage_path = storage_path
        self.feedback_history: List[Dict[str, Any]] = []

        # Load existing profiles if storage_path provided
        if storage_path and storage_path.exists():
            self._load_profiles()

    def get_or_create_profile(self, user_id: str) -> StyleProfile:
        """Get existing profile or create new one.

        Args:
            user_id: ID of the user

        Returns:
            StyleProfile
        """
        if user_id not in self.preference_profiles:
            self.preference_profiles[user_id] = StyleProfile(user_id=user_id)

        return self.preference_profiles[user_id]

    def record_feedback(self, user_id: str, video_id: str,
                       feedback_items: List[str]) -> bool:
        """Record feedback and update user profile.

        Args:
            user_id: ID of the user
            video_id: ID of the video being rated
            feedback_items: List of feedback strings

        Returns:
            True if feedback was recorded, False if invalid
        """
        # Validate feedback
        valid, invalid = FeedbackSchema.validate_feedback_list(feedback_items)
        if not valid:
            print(f"Invalid feedback items: {invalid}")
            return False

        profile = self.get_or_create_profile(user_id)

        # Record feedback in history
        feedback_record = {
            "user_id": user_id,
            "video_id": video_id,
            "timestamp": datetime.utcnow().isoformat(),
            "feedback": feedback_items
        }
        self.feedback_history.append(feedback_record)

        # Apply feedback to profile
        for feedback_item in feedback_items:
            mapping = FeedbackSchema.get_feedback_mapping(feedback_item)
            parameter = mapping.target_parameter
            delta = mapping.delta

            # Get current value
            current_value = getattr(profile, parameter, 0.5)

            # Apply delta
            new_value = current_value + delta

            # Clamp to valid range
            if parameter == "overall_speed" or parameter == "average_scene_duration":
                new_value = max(0.5, min(2.0, new_value))  # Speed: 0.5x to 2x
            else:
                new_value = max(0.0, min(1.0, new_value))  # Others: 0 to 1

            # Update profile
            setattr(profile, parameter, new_value)

        # Update metadata
        profile.feedback_count += len(feedback_items)
        profile.last_updated = datetime.utcnow().isoformat()

        # Persist if storage_path provided
        if self.storage_path:
            self._save_profile(user_id, profile)

        return True

    def apply_learned_style(self, user_id: str,
                           narrative: Dict[str, Any]) -> Dict[str, Any]:
        """Apply learned preferences to narrative.

        Args:
            user_id: ID of the user
            narrative: Narrative structure to customize

        Returns:
            Modified narrative with learned style applied
        """
        profile = self.get_or_create_profile(user_id)

        # Create modified copy
        modified_narrative = json.loads(json.dumps(narrative))  # Deep copy

        # Apply animation level adjustments
        if profile.animation_level > 0.7:
            modified_narrative = self._increase_animations(modified_narrative)
        elif profile.animation_level < 0.3:
            modified_narrative = self._decrease_animations(modified_narrative)

        # Apply text density adjustments
        if profile.text_density < 0.3:
            modified_narrative = self._minimize_text(modified_narrative)
        elif profile.text_density > 0.7:
            modified_narrative = self._maximize_text(modified_narrative)

        # Apply pacing adjustments
        if profile.overall_speed > 1.1:
            modified_narrative = self._increase_pacing(modified_narrative, profile.overall_speed)
        elif profile.overall_speed < 0.9:
            modified_narrative = self._decrease_pacing(modified_narrative, profile.overall_speed)

        # Apply color adjustments
        if hasattr(modified_narrative, "get") and "color_scheme" in modified_narrative:
            modified_narrative["color_scheme"] = self._adjust_colors(
                modified_narrative["color_scheme"],
                profile.color_brightness
            )

        return modified_narrative

    def _increase_animations(self, narrative: Dict[str, Any]) -> Dict[str, Any]:
        """Add more animations to narrative"""
        for scene in narrative.get("scenes", []):
            if "animation" not in scene:
                scene["animation"] = "standard"
            # Could add additional animation layers
        return narrative

    def _decrease_animations(self, narrative: Dict[str, Any]) -> Dict[str, Any]:
        """Reduce animations in narrative"""
        for scene in narrative.get("scenes", []):
            scene["animation"] = "minimal"
        return narrative

    def _minimize_text(self, narrative: Dict[str, Any]) -> Dict[str, Any]:
        """Reduce text overlays in narrative"""
        for scene in narrative.get("scenes", []):
            if "text_overlays" in scene:
                # Reduce number of text overlays
                scene["text_overlays"] = scene["text_overlays"][:1] if scene["text_overlays"] else []
        return narrative

    def _maximize_text(self, narrative: Dict[str, Any]) -> Dict[str, Any]:
        """Increase text overlays in narrative"""
        for scene in narrative.get("scenes", []):
            if "narration" in scene:
                # Add text overlay of narration
                scene["text_overlays"] = scene.get("text_overlays", [])
                if scene["narration"] not in scene["text_overlays"]:
                    scene["text_overlays"].append(scene["narration"])
        return narrative

    def _increase_pacing(self, narrative: Dict[str, Any], speed_multiplier: float) -> Dict[str, Any]:
        """Increase overall pacing"""
        for scene in narrative.get("scenes", []):
            if "duration_seconds" in scene:
                scene["duration_seconds"] /= speed_multiplier
        return narrative

    def _decrease_pacing(self, narrative: Dict[str, Any], speed_multiplier: float) -> Dict[str, Any]:
        """Decrease overall pacing"""
        for scene in narrative.get("scenes", []):
            if "duration_seconds" in scene:
                scene["duration_seconds"] *= (1 / speed_multiplier)
        return narrative

    def _adjust_colors(self, color_scheme: str, brightness: float) -> str:
        """Adjust color scheme brightness"""
        brightness_levels = {
            0.0: "darkest",
            0.25: "dark",
            0.5: "standard",
            0.75: "bright",
            1.0: "brightest"
        }

        # Find closest brightness level
        closest = min(brightness_levels.keys(), key=lambda x: abs(x - brightness))
        return brightness_levels[closest]

    def get_profile(self, user_id: str) -> Optional[StyleProfile]:
        """Get a user's style profile."""
        return self.preference_profiles.get(user_id)

    def get_profile_summary(self, user_id: str) -> Dict[str, Any]:
        """Get summary of a user's style profile"""
        profile = self.get_or_create_profile(user_id)

        return {
            "user_id": user_id,
            "preferences": profile.get_all_parameters(),
            "feedback_count": profile.feedback_count,
            "videos_produced": profile.videos_produced,
            "created_at": profile.created_at,
            "last_updated": profile.last_updated
        }

    def _save_profile(self, user_id: str, profile: StyleProfile):
        """Persist profile to storage"""
        if not self.storage_path:
            return

        self.storage_path.mkdir(parents=True, exist_ok=True)
        profile_file = self.storage_path / f"{user_id}.json"

        with open(profile_file, "w") as f:
            json.dump(profile.to_dict(), f, indent=2)

    def _load_profiles(self):
        """Load profiles from storage"""
        if not self.storage_path or not self.storage_path.exists():
            return

        for profile_file in self.storage_path.glob("*.json"):
            try:
                with open(profile_file, "r") as f:
                    data = json.load(f)
                    profile = StyleProfile.from_dict(data)
                    self.preference_profiles[profile.user_id] = profile
            except Exception as e:
                print(f"Error loading profile {profile_file}: {e}")
