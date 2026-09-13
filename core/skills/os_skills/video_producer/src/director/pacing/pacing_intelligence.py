"""Intelligent time allocation based on narrative importance.

Distributes screen time across scenes based on their importance,
complexity, and key learning objectives.
"""

from typing import Dict, List, Any
from dataclasses import dataclass


@dataclass
class ImportanceScore:
    """Represents importance of a scene"""
    scene_id: str
    role_score: float  # 0.0-1.0
    complexity_score: float  # 0.0-1.0
    learning_score: float  # 0.0-1.0
    total_score: float  # Weighted average


class PacingIntelligence:
    """Intelligent time allocation based on narrative importance"""

    def __init__(self):
        self.importance_scores: Dict[str, ImportanceScore] = {}
        self.time_allocations: Dict[str, float] = {}

    def allocate_time(self, narrative: Dict[str, Any],
                      total_duration: int) -> Dict[str, float]:
        """Redistribute time based on narrative importance.

        Rules:
        - Hero's Journey: hook 10%, action 50% (most important)
        - Problem-Solution: solution 60% (most important)
        - Three-Act: confrontation 50% (climax)

        Args:
            narrative: Narrative structure with scenes
            total_duration: Total duration in seconds

        Returns:
            Dictionary mapping scene_id to allocated duration in seconds
        """
        self.importance_scores.clear()
        self.time_allocations.clear()

        # Score each scene for importance
        for scene in narrative.get("scenes", []):
            scene_id = scene.get("id", "unknown")
            score = self._compute_importance(scene)
            self.importance_scores[scene_id] = score

        # Normalize scores to total duration
        total_importance = sum(s.total_score for s in self.importance_scores.values())

        if total_importance == 0:
            # Equal distribution if no importance scores
            equal_time = total_duration / len(self.importance_scores)
            for scene_id in self.importance_scores.keys():
                self.time_allocations[scene_id] = equal_time
        else:
            for scene_id, score in self.importance_scores.items():
                allocated_time = (score.total_score / total_importance) * total_duration
                self.time_allocations[scene_id] = allocated_time

        # Validate: sum equals total_duration (within rounding)
        total_allocated = sum(self.time_allocations.values())
        assert abs(total_allocated - total_duration) < 1.0, \
            f"Time allocation error: {total_allocated} != {total_duration}"

        return self.time_allocations

    def _compute_importance(self, scene: Dict[str, Any]) -> ImportanceScore:
        """Compute importance score for a scene.

        Args:
            scene: The scene dictionary

        Returns:
            ImportanceScore with component scores
        """
        scene_id = scene.get("id", "unknown")

        # Role in narrative (hook vs action vs resolution)
        role_score = self._score_role_importance(scene)

        # Complexity/depth of content
        complexity_score = self._score_complexity(scene)

        # Key learning objectives
        learning_score = self._score_learning_value(scene)

        # Weighted average: role 50%, complexity 30%, learning 20%
        total_score = (
            role_score * 0.5 +
            complexity_score * 0.3 +
            learning_score * 0.2
        )

        return ImportanceScore(
            scene_id=scene_id,
            role_score=role_score,
            complexity_score=complexity_score,
            learning_score=learning_score,
            total_score=total_score
        )

    def _score_role_importance(self, scene: Dict[str, Any]) -> float:
        """Score importance based on role in narrative."""
        role = scene.get("role_in_narrative", "").lower()

        role_weights = {
            "hook": 0.9,
            "opening": 0.9,
            "climax": 1.0,
            "action": 0.8,
            "main": 0.8,
            "context": 0.6,
            "transition": 0.4,
            "resolution": 0.7,
            "closing": 0.5,
            "cta": 0.5
        }

        for role_key, weight in role_weights.items():
            if role_key in role:
                return weight

        return 0.5  # Default middle importance

    def _score_complexity(self, scene: Dict[str, Any]) -> float:
        """Score based on content complexity.

        Factors:
        - Number of assets
        - Number of concepts
        - Technical depth
        """
        score = 0.5  # Base

        # Assets complexity
        if "assets" in scene:
            num_assets = len(scene["assets"])
            score += min(num_assets * 0.1, 0.3)

        # Narration length/depth
        if "narration" in scene:
            narration_length = len(scene["narration"].split())
            score += min(narration_length / 100, 0.2)

        # Technical depth flag
        if scene.get("is_technical", False):
            score += 0.15

        return min(score, 1.0)

    def _score_learning_value(self, scene: Dict[str, Any]) -> float:
        """Score based on key learning objectives."""
        score = 0.5  # Base

        # Check for key messages
        metadata = scene.get("metadata", {})
        if "key_messages" in metadata:
            num_messages = len(metadata["key_messages"])
            score += min(num_messages * 0.15, 0.4)

        # Check for learning objectives
        if "learning_objectives" in metadata:
            num_objectives = len(metadata["learning_objectives"])
            score += min(num_objectives * 0.1, 0.3)

        return min(score, 1.0)

    def get_time_allocation(self, scene_id: str) -> float:
        """Get allocated time for a specific scene."""
        return self.time_allocations.get(scene_id, 0.0)

    def get_allocation_summary(self) -> Dict[str, Any]:
        """Get summary of time allocations"""
        if not self.time_allocations:
            return {
                "total_duration": 0,
                "scenes": 0,
                "allocations": {}
            }

        return {
            "total_duration": sum(self.time_allocations.values()),
            "scenes": len(self.time_allocations),
            "allocations": self.time_allocations,
            "importance_scores": {
                scene_id: {
                    "role": score.role_score,
                    "complexity": score.complexity_score,
                    "learning": score.learning_score,
                    "total": score.total_score
                }
                for scene_id, score in self.importance_scores.items()
            }
        }
