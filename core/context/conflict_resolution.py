"""k=4: Conflict Resolution — Handle conflicts between Original and Pipeline Context."""

from enum import Enum
from dataclasses import dataclass
from core.context import OriginalContext, PipelineAddition, QualityTier


class ConflictType(Enum):
    """Types of conflicts between Original and Pipeline Context."""
    SAFETY_OVERRIDE = "safety"
    PREREQUISITE_GATE = "prereq"
    GOAL_PROTECTION = "goal"
    IMPLEMENTATION_BLEND = "impl"


@dataclass
class ConflictResolution:
    """Result of conflict resolution."""
    conflict_type: ConflictType
    winner: str  # "original" or "pipeline"
    reasoning: str
    action: str  # "include", "flag", "ask_user", "override"


class ConflictResolver:
    """Resolves conflicts between Original and Pipeline Context."""

    def resolve(
        self,
        original: OriginalContext,
        addition: PipelineAddition,
    ) -> ConflictResolution:
        """Resolve conflict if addition conflicts with original goal.

        Precedence:
        1. Safety/blocking → Pipeline wins
        2. Prerequisite → Ask user
        3. Goal-level change → Original wins
        4. Implementation → Combine both
        """
        # Check if safety/blocking
        if addition.tier == QualityTier.TIER_1_ALWAYS and "safety" in addition.source.lower():
            return ConflictResolution(
                conflict_type=ConflictType.SAFETY_OVERRIDE,
                winner="pipeline",
                reasoning="Safety concern overrides original goal",
                action="override",
            )

        # Check if prerequisite
        if "prerequisite" in addition.relevance.lower():
            return ConflictResolution(
                conflict_type=ConflictType.PREREQUISITE_GATE,
                winner="ask_user",
                reasoning="Blocking prerequisite requires user decision",
                action="ask_user",
            )

        # Check if goal-level change
        if any(word in addition.content.lower() for word in ["instead", "redirect", "abandon"]):
            return ConflictResolution(
                conflict_type=ConflictType.GOAL_PROTECTION,
                winner="original",
                reasoning="Original goal is protected against redirection",
                action="flag",
            )

        # Default: implementation-level blend
        return ConflictResolution(
            conflict_type=ConflictType.IMPLEMENTATION_BLEND,
            winner="both",
            reasoning="Implementation-level addition; combine both",
            action="include",
        )
