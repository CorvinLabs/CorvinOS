"""Cinematic pacing optimization.

Applies professional pacing rules to ensure engaging narrative flow:
- Hook: fast cuts for attention
- Build: normal pacing
- Climax: extended for impact
- Resolution: wind down
"""

from typing import List, Dict, Any
from enum import Enum


class NarrativePhase(Enum):
    """Narrative phase in the story arc"""
    HOOK = "hook"
    BUILD = "build"
    CLIMAX = "climax"
    RESOLUTION = "resolution"


class PacingOptimizer:
    """Optimize pacing for narrative flow"""

    # Pacing multipliers for different narrative phases
    PHASE_MULTIPLIERS = {
        NarrativePhase.HOOK: 0.8,        # Faster (more cuts)
        NarrativePhase.BUILD: 1.0,        # Normal pacing
        NarrativePhase.CLIMAX: 1.5,       # Extended (more emphasis)
        NarrativePhase.RESOLUTION: 0.9    # Wind down slightly
    }

    def __init__(self):
        self.optimized_scenes: List[Dict[str, Any]] = []

    def optimize(self, scenes_with_time: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply cinematic pacing rules.

        Args:
            scenes_with_time: List of scenes with duration_seconds

        Returns:
            List of optimized scenes with adjusted durations
        """
        self.optimized_scenes.clear()

        for i, scene in enumerate(scenes_with_time):
            # Detect narrative phase
            phase = self._detect_phase(i, len(scenes_with_time))

            # Apply pacing multiplier
            multiplier = self.PHASE_MULTIPLIERS[phase]

            optimized_scene = scene.copy()

            # Apply multiplier to duration
            original_duration = scene.get("duration_seconds", 5.0)
            optimized_scene["duration_seconds"] = original_duration * multiplier
            optimized_scene["pacing_phase"] = phase.value
            optimized_scene["pacing_multiplier"] = multiplier

            # Add transition recommendations
            optimized_scene["transition_type"] = self._recommend_transition(phase)

            self.optimized_scenes.append(optimized_scene)

        return self.optimized_scenes

    def _detect_phase(self, scene_index: int, total_scenes: int) -> NarrativePhase:
        """Detect narrative phase based on position.

        Args:
            scene_index: Index of current scene (0-based)
            total_scenes: Total number of scenes

        Returns:
            NarrativePhase enum
        """
        if total_scenes < 3:
            return NarrativePhase.BUILD

        # Divide narrative into phases
        hook_end = max(1, int(total_scenes * 0.1))          # First 10%
        climax_start = max(hook_end + 1, int(total_scenes * 0.6))  # After 60%
        climax_end = max(climax_start + 1, int(total_scenes * 0.8))  # Up to 80%

        if scene_index < hook_end:
            return NarrativePhase.HOOK
        elif scene_index >= climax_start and scene_index < climax_end:
            return NarrativePhase.CLIMAX
        elif scene_index >= climax_end:
            return NarrativePhase.RESOLUTION
        else:
            return NarrativePhase.BUILD

    def _recommend_transition(self, phase: NarrativePhase) -> str:
        """Recommend transition type for a phase.

        Args:
            phase: Current narrative phase

        Returns:
            Transition type string
        """
        transitions = {
            NarrativePhase.HOOK: "cut",           # Fast, punchy
            NarrativePhase.BUILD: "fade",          # Smooth progression
            NarrativePhase.CLIMAX: "cut",          # Sharp, dynamic
            NarrativePhase.RESOLUTION: "dissolve"  # Gentle wind down
        }
        return transitions[phase]

    def optimize_with_visual_intensity(self,
                                      scenes_with_time: List[Dict[str, Any]],
                                      intensity_curve: str = "default") -> List[Dict[str, Any]]:
        """Optimize pacing while considering visual intensity.

        Args:
            scenes_with_time: Scenes with durations
            intensity_curve: "default", "exponential", "linear"

        Returns:
            Optimized scenes
        """
        optimized = self.optimize(scenes_with_time)

        # Apply intensity-based adjustments
        if intensity_curve == "exponential":
            # Climax scenes get even more emphasis
            for scene in optimized:
                if scene["pacing_phase"] == NarrativePhase.CLIMAX.value:
                    scene["duration_seconds"] *= 1.2
                    scene["intensity_multiplier"] = 1.2
                else:
                    scene["intensity_multiplier"] = 1.0

        elif intensity_curve == "linear":
            # Gradual build to climax
            for i, scene in enumerate(optimized):
                progress = i / len(optimized)
                if progress < 0.8:  # Build phase
                    multiplier = 1.0 + (progress * 0.3)
                    scene["duration_seconds"] *= multiplier
                else:  # Resolution phase
                    multiplier = 1.3 - ((progress - 0.8) * 1.3 / 0.2)
                    scene["duration_seconds"] *= multiplier

                scene["intensity_multiplier"] = multiplier

        return optimized

    def get_pacing_distribution(self) -> Dict[str, float]:
        """Get distribution of pacing across narrative."""
        if not self.optimized_scenes:
            return {}

        distribution = {}
        total_duration = sum(s.get("duration_seconds", 0) for s in self.optimized_scenes)

        for phase in NarrativePhase:
            phase_duration = sum(
                s.get("duration_seconds", 0)
                for s in self.optimized_scenes
                if s.get("pacing_phase") == phase.value
            )
            if total_duration > 0:
                distribution[phase.value] = phase_duration / total_duration

        return distribution

    def validate_pacing(self) -> Dict[str, Any]:
        """Validate pacing follows cinematic best practices.

        Returns:
            Validation report
        """
        if not self.optimized_scenes:
            return {"valid": False, "reason": "No scenes optimized"}

        # Check: no scene shorter than 1 second
        min_duration = min(s.get("duration_seconds", 0) for s in self.optimized_scenes)
        if min_duration < 1.0:
            return {
                "valid": False,
                "reason": f"Scene duration too short: {min_duration}s"
            }

        # Check: climax is longer than hook
        distribution = self.get_pacing_distribution()
        if distribution.get("climax", 0) <= distribution.get("hook", 0):
            return {
                "valid": False,
                "reason": "Climax not longer than hook"
            }

        # Check: total duration is reasonable
        total_duration = sum(s.get("duration_seconds", 0) for s in self.optimized_scenes)
        if total_duration < 30:
            return {
                "valid": False,
                "reason": f"Total duration too short: {total_duration}s"
            }

        return {
            "valid": True,
            "total_duration": total_duration,
            "distribution": distribution,
            "min_scene_duration": min_duration,
            "average_scene_duration": total_duration / len(self.optimized_scenes)
        }
