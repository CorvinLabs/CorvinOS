"""Context Engineering Layer (CEL) — OS as thinking middleware.

Transforms sparse task input into rich context briefs for agents.
Phases: Memory Lookup, Graph Traversal, Skill Injection, Approach Synthesis, Blocker ID.

Phase 1 Lite (MVP): Memory Lookup only (ADR-0269).
"""

from .memory_lookup import MemoryLookup, MemoryMatch
from .rich_task_brief import RichTaskBrief, MemoryContext

__all__ = [
    "MemoryLookup",
    "MemoryMatch",
    "RichTaskBrief",
    "MemoryContext",
]

__version__ = "0.1.0"
