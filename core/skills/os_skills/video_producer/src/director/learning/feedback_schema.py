"""Actionable feedback labels for style learning.

Defines a closed set of feedback types that map to learnable parameters,
preventing hallucination and ensuring all feedback is actionable.
"""

from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum


class FeedbackType(Enum):
    """Valid feedback types"""
    MORE_ANIMATIONS = "more_animations"
    LESS_ANIMATIONS = "less_animations"
    MORE_TEXT = "more_text"
    LESS_TEXT = "less_text"
    LONGER_SCENES = "longer_scenes"
    SHORTER_SCENES = "shorter_scenes"
    DARKER_COLORS = "darker_colors"
    BRIGHTER_COLORS = "brighter_colors"
    FASTER_PACING = "faster_pacing"
    SLOWER_PACING = "slower_pacing"
    ADD_MUSIC = "add_music"
    REMOVE_MUSIC = "remove_music"
    MORE_DRAMATIC = "more_dramatic"
    LESS_DRAMATIC = "less_dramatic"
    MORE_TECHNICAL = "more_technical"
    LESS_TECHNICAL = "less_technical"


@dataclass
class FeedbackMapping:
    """Maps a feedback type to learnable parameters"""
    feedback_type: str
    target_parameter: str
    delta: float
    applicable_to: List[str]  # Which scene types this applies to


class FeedbackSchema:
    """Closed set of actionable feedback labels"""

    VALID_FEEDBACK: Dict[str, Dict[str, Any]] = {
        "more_animations": {
            "target": "animation_level",
            "delta": 0.2,
            "description": "Add more animations and motion graphics",
            "applicable_to": ["all"]
        },
        "less_animations": {
            "target": "animation_level",
            "delta": -0.2,
            "description": "Reduce animations and motion graphics",
            "applicable_to": ["all"]
        },
        "more_text": {
            "target": "text_density",
            "delta": 0.2,
            "description": "Include more on-screen text and captions",
            "applicable_to": ["technical", "educational"]
        },
        "less_text": {
            "target": "text_density",
            "delta": -0.2,
            "description": "Reduce on-screen text and visual clutter",
            "applicable_to": ["all"]
        },
        "longer_scenes": {
            "target": "average_scene_duration",
            "delta": 1.2,
            "description": "Extend individual scene durations",
            "applicable_to": ["all"]
        },
        "shorter_scenes": {
            "target": "average_scene_duration",
            "delta": 0.8,
            "description": "Shorten individual scene durations",
            "applicable_to": ["all"]
        },
        "darker_colors": {
            "target": "color_brightness",
            "delta": -0.2,
            "description": "Use darker, more saturated colors",
            "applicable_to": ["all"]
        },
        "brighter_colors": {
            "target": "color_brightness",
            "delta": 0.2,
            "description": "Use lighter, more vibrant colors",
            "applicable_to": ["all"]
        },
        "faster_pacing": {
            "target": "overall_speed",
            "delta": 1.2,
            "description": "Increase overall pacing and tempo",
            "applicable_to": ["all"]
        },
        "slower_pacing": {
            "target": "overall_speed",
            "delta": 0.8,
            "description": "Decrease overall pacing for more contemplation",
            "applicable_to": ["all"]
        },
        "add_music": {
            "target": "music_presence",
            "delta": 0.3,
            "description": "Add or increase background music",
            "applicable_to": ["all"]
        },
        "remove_music": {
            "target": "music_presence",
            "delta": -0.3,
            "description": "Remove or reduce background music",
            "applicable_to": ["technical", "serious"]
        },
        "more_dramatic": {
            "target": "dramatic_intensity",
            "delta": 0.2,
            "description": "Increase dramatic elements and tension",
            "applicable_to": ["narrative", "storytelling"]
        },
        "less_dramatic": {
            "target": "dramatic_intensity",
            "delta": -0.2,
            "description": "Tone down dramatic elements",
            "applicable_to": ["all"]
        },
        "more_technical": {
            "target": "technical_depth",
            "delta": 0.2,
            "description": "Include more technical details and explanations",
            "applicable_to": ["technical"]
        },
        "less_technical": {
            "target": "technical_depth",
            "delta": -0.2,
            "description": "Simplify technical content for broader audience",
            "applicable_to": ["all"]
        },
    }

    @classmethod
    def validate_feedback(cls, feedback: str) -> bool:
        """Validate that feedback is in the allowed set.

        Args:
            feedback: Feedback string to validate

        Returns:
            True if valid, False otherwise
        """
        return feedback.lower() in cls.VALID_FEEDBACK

    @classmethod
    def get_feedback_mapping(cls, feedback: str) -> FeedbackMapping:
        """Get parameter mapping for a feedback type.

        Args:
            feedback: Feedback string

        Returns:
            FeedbackMapping with parameter details

        Raises:
            ValueError: If feedback type is not valid
        """
        if not cls.validate_feedback(feedback):
            raise ValueError(f"Invalid feedback type: {feedback}")

        mapping_def = cls.VALID_FEEDBACK[feedback]
        return FeedbackMapping(
            feedback_type=feedback,
            target_parameter=mapping_def["target"],
            delta=mapping_def["delta"],
            applicable_to=mapping_def.get("applicable_to", ["all"])
        )

    @classmethod
    def list_valid_feedback(cls) -> List[str]:
        """List all valid feedback types."""
        return list(cls.VALID_FEEDBACK.keys())

    @classmethod
    def get_feedback_description(cls, feedback: str) -> str:
        """Get human-readable description of a feedback type.

        Args:
            feedback: Feedback string

        Returns:
            Description string
        """
        if not cls.validate_feedback(feedback):
            return "Unknown feedback type"

        return cls.VALID_FEEDBACK[feedback]["description"]

    @classmethod
    def get_feedback_for_scene_type(cls, scene_type: str) -> List[str]:
        """Get applicable feedback types for a scene type.

        Args:
            scene_type: Type of scene (technical, narrative, etc.)

        Returns:
            List of applicable feedback types
        """
        applicable = []
        for feedback, mapping in cls.VALID_FEEDBACK.items():
            if "all" in mapping.get("applicable_to", []) or \
               scene_type in mapping.get("applicable_to", []):
                applicable.append(feedback)

        return applicable

    @classmethod
    def validate_feedback_list(cls, feedback_list: List[str]) -> tuple[bool, List[str]]:
        """Validate a list of feedback items.

        Args:
            feedback_list: List of feedback strings

        Returns:
            Tuple of (all_valid, invalid_items)
        """
        invalid = [f for f in feedback_list if not cls.validate_feedback(f)]
        return len(invalid) == 0, invalid
