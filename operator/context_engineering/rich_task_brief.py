"""RichTaskBrief and MemoryMatch structs."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class MemoryMatch:
    """Single memory file match from search."""

    filename: str
    """Name of memory file (e.g., 'voice-summary-context-loss.md')."""

    title: str
    """Title from memory file."""

    relevance_score: float
    """Relevance score [0.0, 1.0]. Higher = more relevant."""

    source_file: str
    """Absolute path to memory file."""

    timestamp: datetime
    """File creation/modification timestamp."""

    content_preview: str = ""
    """First 50 chars of memory file content (ADR-0389)."""

    def __post_init__(self):
        """Validate score is in [0.0, 1.0]."""
        if not 0.0 <= self.relevance_score <= 1.0:
            raise ValueError(f"Score must be [0.0, 1.0], got {self.relevance_score}")


@dataclass
class MemoryContext:
    """Memory enrichment for a task."""

    matches: List[MemoryMatch] = field(default_factory=list)
    """Ranked list of matching memory files."""

    search_queries: List[str] = field(default_factory=list)
    """Queries used to search memory."""

    confidence: float = 0.0
    """Overall confidence [0.0, 1.0] that memory is relevant."""

    cache_hit: bool = False
    """Whether this result was cached."""

    search_duration_ms: float = 0.0
    """Time to search memory (ms)."""

    def __post_init__(self):
        """Validate confidence is in [0.0, 1.0]."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be [0.0, 1.0], got {self.confidence}")


@dataclass
class RichTaskBrief:
    """Complete context for agent execution.

    Output of Context Engineering Layer (Phase 5.5).
    Transforms sparse EnrichedTask into rich context brief.
    Extended in Phase 2: adds related_decisions and recommended_skills.
    """

    raw_input: str
    """Original user input."""

    enriched_task: object
    """EnrichedTask from Phase 4 (Enrich)."""

    memory_context: MemoryContext
    """Memory lookup results (Phase 5.5a)."""

    timestamp: datetime
    """When RichTaskBrief was created."""

    related_decisions: List[object] = field(default_factory=list)
    """Related decisions from GraphTraversal (Phase 5.5b)."""

    recommended_skills: List[object] = field(default_factory=list)
    """Recommended skills from SkillInjection (Phase 5.5c)."""

    version: str = "0.2"
    """RichTaskBrief format version (0.2: Phase 2 with decisions + skills)."""

    anchor_facts: List[dict] = field(default_factory=list)
    """Session Load-Bearing-Fact Anchor facts (ADR-0407 amendment). Optional; only
    populated by ``build_brief`` when the ``cel_load_bearing_anchor`` flag is ON
    (ship-dark, default OFF). Each entry: ``{id, kind, text, added_at}``. Rendered
    UNCAPPED at the very top of the brief so a load-bearing fact survives the
    memory/blocker [:5] truncation every turn. Empty (default) ⇒ quiet path."""

    def __repr__(self) -> str:
        """Compact representation for logging."""
        return (
            f"RichTaskBrief("
            f"input='{self.raw_input[:30]}...', "
            f"memory_matches={len(self.memory_context.matches)}, "
            f"related_decisions={len(self.related_decisions)}, "
            f"recommended_skills={len(self.recommended_skills)}, "
            f"confidence={self.memory_context.confidence:.2f}"
            f")"
        )
