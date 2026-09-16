"""Director Mode Phase 2: Visual + Pacing Choreographer

Allocates time, emotion-to-visual mapping, cinematic pacing per ADR-0696.
"""

from dataclasses import dataclass, field
from typing import Dict, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class Scene:
    """Choreographed scene with visual language + timing."""
    act: str
    title: str
    duration_seconds: float
    emotional_tone: str
    visual_approach: str  # e.g., "dramatic", "contemplative", "dynamic"
    screenshot_indices: List[int] = field(default_factory=list)


class VisualLanguageMapper:
    """Map emotional tone to visual approach."""

    MAPPINGS = {
        "dramatic": ["high-contrast", "cinematic-lighting", "dynamic-camera"],
        "contemplative": ["soft-lighting", "static-camera", "muted-colors"],
        "dynamic": ["fast-cuts", "varied-angles", "bright-colors"],
    }

    def suggest_visual_approach(self, emotional_tone: str) -> str:
        """Suggest visual approach based on emotional tone."""
        return self.MAPPINGS.get(emotional_tone.lower(), ["cinematic"])[0]


class PacingIntelligence:
    """Allocate time per scene respecting narrative arc."""

    def allocate_timing(self, narrative: Dict, total_duration: float, num_facts: int) -> List[float]:
        """
        Allocate scene durations by importance.

        Load-bearing invariant: Sum of durations = total_duration (within 1.0s rounding).
        """
        num_scenes = len(narrative.get("scenes", []))
        if num_scenes == 0:
            return []

        # Cinematic pacing: hook < build < climax > resolution
        base_duration = total_duration / num_scenes
        durations = []

        for i, scene in enumerate(narrative.get("scenes", [])):
            if i == 0:  # Hook (shorter, grab attention)
                duration = base_duration * 0.8
            elif i == len(narrative.get("scenes", [])) - 1:  # Resolution (close strong)
                duration = base_duration * 1.0
            else:  # Middle scenes (build tension)
                duration = base_duration * (1.0 + (i * 0.1))

            durations.append(max(1.0, duration))  # Enforce minimum 1.0s per scene

        # Normalize to exact total
        total = sum(durations)
        if total > 0:
            scale = total_duration / total
            durations = [d * scale for d in durations]

        logger.info(f"Pacing allocated: {len(durations)} scenes, total={sum(durations):.1f}s")
        return durations


class VisualChoreographer:
    """Phase 2: Choreograph visual language + pacing."""

    def __init__(self):
        self.visual_mapper = VisualLanguageMatcher()
        self.pacing = PacingIntelligence()

    def choreograph(self, narrative: Dict, total_duration: float = 120.0) -> List[Scene]:
        """Create choreographed scenes with visual language + timing."""
        durations = self.pacing.allocate_timing(narrative, total_duration, num_facts=len(narrative.get("facts_preserved", [])))

        choreographed_scenes = []
        for i, (scene_data, duration) in enumerate(zip(narrative.get("scenes", []), durations)):
            emotional_tone = scene_data.get("emotional_tone", "dramatic")
            visual_approach = self.visual_mapper.suggest_visual_approach(emotional_tone)

            choreographed = Scene(
                act=scene_data.get("act", f"Act {i+1}"),
                title=scene_data.get("title", f"Scene {i+1}"),
                duration_seconds=duration,
                emotional_tone=emotional_tone,
                visual_approach=visual_approach,
                screenshot_indices=scene_data.get("screenshot_indices", [])
            )
            choreographed_scenes.append(choreographed)

        logger.info(f"Choreographed {len(choreographed_scenes)} scenes with visual language + pacing")
        return choreographed_scenes


class VisualLanguageMatcher:
    """Visual language matcher."""
    MAPPINGS = {
        "dramatic": "high-contrast-cinematic",
        "contemplative": "soft-lighting-static",
        "dynamic": "fast-cuts-bright",
    }

    def suggest_visual_approach(self, emotional_tone: str) -> str:
        return self.MAPPINGS.get(emotional_tone.lower(), "cinematic")


__all__ = ["VisualChoreographer", "Scene"]
