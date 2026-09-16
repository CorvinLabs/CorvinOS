"""Feedback Schema for Style Learning (Phase 3)

Closed-set feedback types with parameter targets and deltas.
No free-form feedback; all types pre-defined + validated.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional
from datetime import datetime


class FeedbackType(str, Enum):
    """Closed-set feedback types for Style Learning."""

    MORE_ANIMATIONS = "more_animations"          # animation_level +0.2
    LESS_ANIMATIONS = "less_animations"          # animation_level -0.2
    MORE_TEXT = "more_text"                      # text_density +0.3
    LESS_TEXT = "less_text"                      # text_density -0.3
    FASTER_PACING = "faster_pacing"              # overall_speed +0.25
    SLOWER_PACING = "slower_pacing"              # overall_speed -0.25
    BRIGHTER_COLORS = "brighter_colors"          # color_brightness +0.2
    DARKER_COLORS = "darker_colors"              # color_brightness -0.2
    MORE_MUSIC = "more_music"                    # music_presence +0.2
    LESS_MUSIC = "less_music"                    # music_presence -0.2
    MORE_DRAMATIC = "more_dramatic"              # dramatic_intensity +0.2
    LESS_DRAMATIC = "less_dramatic"              # dramatic_intensity -0.2
    MORE_TECHNICAL = "more_technical"            # technical_depth +0.2
    LESS_TECHNICAL = "less_technical"            # technical_depth -0.2
    LONGER_SCENES = "longer_scenes"              # average_scene_duration +2.0s
    SHORTER_SCENES = "shorter_scenes"            # average_scene_duration -2.0s


@dataclass
class FeedbackEvent:
    """Immutable feedback event from user."""
    user_id: str
    feedback_type: FeedbackType
    timestamp: str  # ISO format
    video_id: Optional[str] = None  # Which video this feedback is for
    notes: Optional[str] = None  # Optional context (not used for learning, audit only)


# Feedback Type Configuration: target parameter + delta value
VALID_FEEDBACK_TYPES: Dict[FeedbackType, Dict[str, any]] = {
    FeedbackType.MORE_ANIMATIONS: {
        "target": "animation_level",
        "delta": +0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.LESS_ANIMATIONS: {
        "target": "animation_level",
        "delta": -0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.MORE_TEXT: {
        "target": "text_density",
        "delta": +0.3,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.LESS_TEXT: {
        "target": "text_density",
        "delta": -0.3,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.FASTER_PACING: {
        "target": "overall_speed",
        "delta": +0.25,
        "clamp_range": (0.5, 2.0),
    },
    FeedbackType.SLOWER_PACING: {
        "target": "overall_speed",
        "delta": -0.25,
        "clamp_range": (0.5, 2.0),
    },
    FeedbackType.BRIGHTER_COLORS: {
        "target": "color_brightness",
        "delta": +0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.DARKER_COLORS: {
        "target": "color_brightness",
        "delta": -0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.MORE_MUSIC: {
        "target": "music_presence",
        "delta": +0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.LESS_MUSIC: {
        "target": "music_presence",
        "delta": -0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.MORE_DRAMATIC: {
        "target": "dramatic_intensity",
        "delta": +0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.LESS_DRAMATIC: {
        "target": "dramatic_intensity",
        "delta": -0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.MORE_TECHNICAL: {
        "target": "technical_depth",
        "delta": +0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.LESS_TECHNICAL: {
        "target": "technical_depth",
        "delta": -0.2,
        "clamp_range": (0.0, 1.0),
    },
    FeedbackType.LONGER_SCENES: {
        "target": "average_scene_duration",
        "delta": +2.0,
        "clamp_range": (1.0, 30.0),
    },
    FeedbackType.SHORTER_SCENES: {
        "target": "average_scene_duration",
        "delta": -2.0,
        "clamp_range": (1.0, 30.0),
    },
}


def validate_feedback_type(feedback_type: FeedbackType) -> bool:
    """Validate that feedback type is in closed set."""
    return feedback_type in VALID_FEEDBACK_TYPES


def get_feedback_config(feedback_type: FeedbackType) -> Dict[str, any]:
    """Get configuration for a feedback type."""
    if not validate_feedback_type(feedback_type):
        raise ValueError(f"Invalid feedback type: {feedback_type}")
    return VALID_FEEDBACK_TYPES[feedback_type]
