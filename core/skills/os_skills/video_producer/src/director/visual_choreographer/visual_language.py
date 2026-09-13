"""Map narrative intent to visual approaches and emotional styles.

Determines the visual language, animation style, color palette, and
transitions for each scene based on narrative intent and emotional tone.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class EmotionalStyle(Enum):
    """Emotional/visual styles for content"""
    INSPIRING = "inspiring"
    TECHNICAL = "technical"
    PLAYFUL = "playful"
    SERIOUS = "serious"
    DRAMATIC = "dramatic"


@dataclass
class VisualStyle:
    """Visual style specification"""
    emotion: str
    assets: List[str]
    animation: str
    color_palette: str
    transitions: str
    pacing_multiplier: float = 1.0


class VisualLanguageMapper:
    """Map narrative intent to visual approach"""

    EMOTIONAL_STYLES = {
        "inspiring": {
            "assets": ["diagrams", "success_stories", "quotes", "testimonials"],
            "animation": "slow_reveal",
            "color_palette": "primary_accent",
            "transitions": "fade",
            "pacing_multiplier": 0.9
        },
        "technical": {
            "assets": ["screenshots", "code", "architecture_diagrams", "data_visualizations"],
            "animation": "highlight_and_annotate",
            "color_palette": "technical_blue",
            "transitions": "cut",
            "pacing_multiplier": 1.1
        },
        "playful": {
            "assets": ["animations", "icons", "demos", "illustrations"],
            "animation": "bounce_and_scale",
            "color_palette": "vibrant",
            "transitions": "slide",
            "pacing_multiplier": 0.8
        },
        "serious": {
            "assets": ["documentary_footage", "graphs", "expert_interviews"],
            "animation": "static_text_overlay",
            "color_palette": "neutral",
            "transitions": "dissolve",
            "pacing_multiplier": 1.0
        },
        "dramatic": {
            "assets": ["cinematic_footage", "dramatic_music", "text_effects"],
            "animation": "dynamic_zoom",
            "color_palette": "contrast_heavy",
            "transitions": "cut",
            "pacing_multiplier": 1.2
        }
    }

    def __init__(self):
        self.scene_visual_plans: List[Dict[str, Any]] = []

    def map_scenes_to_visuals(self, narrative: Dict[str, Any],
                              available_assets: Optional[Dict[str, List[str]]] = None) -> List[Dict[str, Any]]:
        """Map each narrative scene to visual approach.

        Args:
            narrative: Narrative structure with scenes
            available_assets: Available asset types and paths

        Returns:
            List of scenes with visual plans
        """
        self.scene_visual_plans.clear()
        available_assets = available_assets or {}

        for scene in narrative.get("scenes", []):
            emotion = self._detect_emotional_intent(scene)
            style = self.EMOTIONAL_STYLES[emotion]

            # Select appropriate assets
            assets_for_scene = self._select_assets(
                scene,
                style["assets"],
                available_assets
            )

            visual_plan = {
                "scene_id": scene.get("id", "unknown"),
                "emotion": emotion,
                "visual_style": style,
                "selected_assets": assets_for_scene,
                "animation_details": self._generate_animation_details(
                    style["animation"],
                    scene
                ),
                "color_scheme": style["color_palette"],
                "transitions": style["transitions"]
            }

            self.scene_visual_plans.append(visual_plan)

        return self.scene_visual_plans

    def _detect_emotional_intent(self, scene: Dict[str, Any]) -> str:
        """Detect emotional intent from scene content.

        Args:
            scene: Scene dictionary with narration and metadata

        Returns:
            Emotion string from EMOTIONAL_STYLES keys
        """
        narration = scene.get("narration", "").lower()
        metadata = scene.get("metadata", {})

        # Check for explicit emotion tag
        if "emotion" in metadata:
            emotion = metadata["emotion"].lower()
            if emotion in self.EMOTIONAL_STYLES:
                return emotion

        # Infer from content
        inspiration_keywords = ["inspire", "amazing", "transform", "success", "achieve", "amazing"]
        if any(kw in narration for kw in inspiration_keywords):
            return "inspiring"

        technical_keywords = ["code", "algorithm", "architecture", "data", "technical", "implement"]
        if any(kw in narration for kw in technical_keywords):
            return "technical"

        playful_keywords = ["fun", "play", "cool", "awesome", "exciting", "enjoy"]
        if any(kw in narration for kw in playful_keywords):
            return "playful"

        dramatic_keywords = ["critical", "crucial", "emergency", "danger", "breakthrough"]
        if any(kw in narration for kw in dramatic_keywords):
            return "dramatic"

        # Default to serious
        return "serious"

    def _select_assets(self, scene: Dict[str, Any],
                      preferred_asset_types: List[str],
                      available_assets: Dict[str, List[str]]) -> List[Dict[str, str]]:
        """Select optimal assets for a scene.

        Args:
            scene: The scene
            preferred_asset_types: Preferred asset types for this emotion
            available_assets: Available assets by type

        Returns:
            List of selected assets
        """
        selected = []

        # First, try to use scene-specific assets
        if "assets" in scene:
            for asset in scene["assets"]:
                selected.append({
                    "id": asset.get("id", "unknown"),
                    "type": asset.get("type", "generic"),
                    "path": asset.get("path", ""),
                    "duration": asset.get("duration", 5.0)
                })

        # Then, fill in with preferred asset types
        for asset_type in preferred_asset_types:
            if asset_type in available_assets:
                for asset_path in available_assets[asset_type][:1]:  # Take first available
                    selected.append({
                        "id": f"{asset_type}_{len(selected)}",
                        "type": asset_type,
                        "path": asset_path,
                        "duration": 5.0
                    })

        return selected

    def _generate_animation_details(self, animation_type: str,
                                   scene: Dict[str, Any]) -> Dict[str, Any]:
        """Generate animation details for a scene.

        Args:
            animation_type: Type of animation (e.g., "slow_reveal")
            scene: The scene

        Returns:
            Animation details dictionary
        """
        details = {
            "type": animation_type,
            "duration_ms": 0,
            "easing": "ease-in-out",
            "keyframes": []
        }

        # Generate animation specifics based on type
        if animation_type == "slow_reveal":
            details["duration_ms"] = 2000
            details["keyframes"] = [
                {"opacity": 0, "time": 0},
                {"opacity": 1, "time": 1.0}
            ]
        elif animation_type == "highlight_and_annotate":
            details["duration_ms"] = 1500
            details["keyframes"] = [
                {"scale": 1.0, "highlight": False, "time": 0},
                {"scale": 1.1, "highlight": True, "time": 0.5},
                {"scale": 1.0, "highlight": True, "time": 1.0}
            ]
        elif animation_type == "bounce_and_scale":
            details["duration_ms"] = 1000
            details["easing"] = "cubic-bezier(0.68, -0.55, 0.265, 1.55)"
            details["keyframes"] = [
                {"scale": 0.8, "time": 0},
                {"scale": 1.1, "time": 0.7},
                {"scale": 1.0, "time": 1.0}
            ]
        elif animation_type == "dynamic_zoom":
            details["duration_ms"] = 2000
            details["keyframes"] = [
                {"zoom": 1.0, "time": 0},
                {"zoom": 1.5, "time": 1.0}
            ]

        return details

    def get_color_palette(self, palette_name: str) -> Dict[str, str]:
        """Get color palette for a visual style.

        Args:
            palette_name: Name of the palette

        Returns:
            Dictionary of color definitions
        """
        palettes = {
            "primary_accent": {
                "primary": "#1f77b4",
                "accent": "#ff7f0e",
                "background": "#ffffff",
                "text": "#000000"
            },
            "technical_blue": {
                "primary": "#003f5c",
                "accent": "#58508d",
                "background": "#f5f5f5",
                "text": "#333333"
            },
            "vibrant": {
                "primary": "#e74c3c",
                "accent": "#3498db",
                "background": "#ecf0f1",
                "text": "#2c3e50"
            },
            "neutral": {
                "primary": "#555555",
                "accent": "#888888",
                "background": "#fafafa",
                "text": "#222222"
            },
            "contrast_heavy": {
                "primary": "#000000",
                "accent": "#ffffff",
                "background": "#1a1a1a",
                "text": "#ffffff"
            }
        }

        return palettes.get(palette_name, palettes["primary_accent"])

    def get_visual_plan_summary(self) -> Dict[str, Any]:
        """Get summary of visual plans created"""
        if not self.scene_visual_plans:
            return {"total_scenes": 0, "emotions": {}}

        emotions = {}
        for plan in self.scene_visual_plans:
            emotion = plan["emotion"]
            emotions[emotion] = emotions.get(emotion, 0) + 1

        return {
            "total_scenes": len(self.scene_visual_plans),
            "emotions": emotions,
            "average_assets_per_scene": sum(len(p["selected_assets"]) for p in self.scene_visual_plans) / len(self.scene_visual_plans)
        }
