"""Pipeline Context Layer — Additive, argumentative context injections.

Memory/skill/ADR additions that supplement (never replace) Original Context.
Every addition must justify its relevance to the original goal.

ADR-0399: Context-Pipeline v2 — Preservation+Additive Model
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum
from uuid import uuid4

logger = logging.getLogger(__name__)


class QualityTier(Enum):
    """Classification of pipeline context addition quality/urgency."""
    TIER_1_ALWAYS = "blocking, safety, direct prerequisite"
    TIER_2_FLAG = "relevant precedent, optimization, order suggestion"
    TIER_3_ASK = "nice-to-know, tangential, alternatives"


@dataclass
class PipelineAddition:
    """A single supplementary context addition from the pipeline.

    Must include explicit relevance to original context.
    Scoped (session/project/user) and conflict-aware.
    """

    scope: str
    """Where this applies: 'session', 'project', or 'user'."""

    source: str
    """Where it came from: 'memory:skill-name', 'adr:0278', 'agent:explore'."""

    relevance: str
    """Why this applies NOW (to original context). Max 1-2 sentences."""

    content: str
    """The actual content/fact being added."""

    id: str = field(default_factory=lambda: str(uuid4())[:8])
    """Unique identifier for this addition."""

    tier: QualityTier = QualityTier.TIER_2_FLAG
    """Quality classification: Tier 1/2/3."""

    conflict_resolution: str = "original_wins"
    """What happens if this conflicts with original goal."""

    timestamp: datetime = field(default_factory=datetime.utcnow)
    """When this was added."""

    def is_valid(self) -> bool:
        """Validate that addition is properly formed."""
        return all([
            self.source,
            self.relevance,
            self.content,
            len(self.relevance) <= 200,  # Relevance must be concise
        ])

    def to_system_prompt_section(self) -> str:
        """Render as system prompt section (labeled as PIPELINE, not original)."""
        tier_label = self.tier.name.replace("_", " ")
        return f"""### [{tier_label}] {self.source}
Relevance: {self.relevance}
Content: {self.content}
Conflict Resolution: {self.conflict_resolution}
"""

    def summary(self) -> str:
        """Short summary of addition."""
        return f"[{self.tier.name}] {self.source}: {self.relevance[:100]}"


@dataclass
class PipelineContext:
    """Container for all pipeline context additions in a session.

    Tracks multiple supplementary additions, each justified.
    Never modifies or replaces original context.
    """

    session_id: str
    """Which session this belongs to."""

    additions: list[PipelineAddition] = field(default_factory=list)
    """All pipeline additions so far."""

    def add(self, addition: PipelineAddition) -> bool:
        """Add a new pipeline context addition.

        Args:
            addition: PipelineAddition with full details

        Returns:
            True if added successfully, False if validation failed
        """
        if not addition.is_valid():
            logger.warning(f"Invalid pipeline addition from {addition.source}: {addition}")
            return False

        self.additions.append(addition)
        logger.debug(f"Added pipeline context: {addition.summary()}")
        return True

    def get_by_tier(self, tier: QualityTier) -> list[PipelineAddition]:
        """Get all additions of a specific tier."""
        return [a for a in self.additions if a.tier == tier]

    def get_by_source(self, source: str) -> list[PipelineAddition]:
        """Get all additions from a specific source."""
        return [a for a in self.additions if a.source == source]

    def to_system_prompt_section(self) -> str:
        """Render all pipeline additions as system prompt section."""
        if not self.additions:
            return "## PIPELINE CONTEXT [Supplementary]\n(none)"

        sections = ["## PIPELINE CONTEXT [Supplementary — ADDS to Original]"]

        # Organize by tier
        for tier in [QualityTier.TIER_1_ALWAYS, QualityTier.TIER_2_FLAG, QualityTier.TIER_3_ASK]:
            tier_additions = self.get_by_tier(tier)
            if tier_additions:
                sections.append(f"\n### {tier.name.replace('_', ' ')}")
                for addition in tier_additions:
                    sections.append(addition.to_system_prompt_section())

        return "\n".join(sections) + "\n[Conflict Resolution: See above per-addition. Original context always protected.]"

    def count_by_tier(self) -> dict[str, int]:
        """Count additions by tier."""
        return {
            "tier_1": len(self.get_by_tier(QualityTier.TIER_1_ALWAYS)),
            "tier_2": len(self.get_by_tier(QualityTier.TIER_2_FLAG)),
            "tier_3": len(self.get_by_tier(QualityTier.TIER_3_ASK)),
            "total": len(self.additions),
        }

    def summary(self) -> str:
        """Human-readable summary of all pipeline context."""
        counts = self.count_by_tier()
        return f"Pipeline Context: {counts['total']} additions (Tier1: {counts['tier_1']}, Tier2: {counts['tier_2']}, Tier3: {counts['tier_3']})"


def create_pipeline_context(session_id: str) -> PipelineContext:
    """Create a new empty pipeline context for a session."""
    return PipelineContext(session_id=session_id)


def add_memory_context(
    pipeline: PipelineContext,
    memory_fact: str,
    original_goal: str,
    memory_source: str,
) -> bool:
    """Add memory-loaded fact to pipeline with argumentative framing.

    Args:
        pipeline: PipelineContext to add to
        memory_fact: The fact from memory
        original_goal: Original goal (for relevance check)
        memory_source: Skill/feature that loaded this memory

    Returns:
        True if added successfully
    """
    # Automatically determine tier and relevance
    tier = QualityTier.TIER_2_FLAG  # Default: flagged (requires review)

    # Simple relevance: just state the connection
    relevance = f"Applies to original goal '{original_goal}' because "
    if "prerequisite" in memory_fact.lower() or "requires" in memory_fact.lower():
        relevance += "it's a blocking prerequisite"
        tier = QualityTier.TIER_1_ALWAYS
    elif "safety" in memory_fact.lower() or "audit" in memory_fact.lower():
        relevance += "it's a safety/compliance constraint"
        tier = QualityTier.TIER_1_ALWAYS
    elif "related" in memory_fact.lower() or "similar" in memory_fact.lower():
        relevance += "it's related to the task domain"
        tier = QualityTier.TIER_2_FLAG
    else:
        relevance += "it's contextually relevant"
        tier = QualityTier.TIER_3_ASK

    addition = PipelineAddition(
        scope="session",
        source=f"memory:{memory_source}",
        relevance=relevance,
        tier=tier,
        content=memory_fact,
        conflict_resolution="original_wins",
    )

    return pipeline.add(addition)
