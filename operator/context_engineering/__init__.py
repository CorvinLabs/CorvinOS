"""Context Engineering Layer (CEL) — OS as thinking middleware.

Transforms sparse task input into rich context briefs for agents.
Phases: Memory Lookup, Graph Traversal, Skill Injection, Approach Synthesis, Blocker ID.

Phase 1 Lite (MVP): Memory Lookup only (ADR-0269).
Phase 2: Graph Traversal + Skill Injection (ADR-0269 Phase 2).
"""

from .memory_lookup import MemoryLookup, MemoryMatch
from .rich_task_brief import RichTaskBrief, MemoryContext
from .graph_traversal import GraphTraversal, RelatedDecision, GraphTraversalResult

__all__ = [
    "MemoryLookup",
    "MemoryMatch",
    "RichTaskBrief",
    "MemoryContext",
    "GraphTraversal",
    "RelatedDecision",
    "GraphTraversalResult",
]

__version__ = "0.2.0"
