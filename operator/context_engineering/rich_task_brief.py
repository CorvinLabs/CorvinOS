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
    """First 200 chars of memory file content."""

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
    """

    raw_input: str
    """Original user input."""

    enriched_task: object
    """EnrichedTask from Phase 4 (Enrich)."""

    memory_context: MemoryContext
    """Memory lookup results."""

    timestamp: datetime
    """When RichTaskBrief was created."""

    version: str = "0.1"
    """RichTaskBrief format version."""

    def __repr__(self) -> str:
        return (
            f"RichTaskBrief("
            f"input='{self.raw_input[:30]}...', "
            f"memory_matches={len(self.memory_context.matches)}, "
            f"confidence={self.memory_context.confidence:.2f}"
            f")"
        )
