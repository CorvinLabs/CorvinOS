"""Context Engineering Layer (CEL) — OS as thinking middleware.

Transforms sparse task input into rich context briefs for agents.
Phases: Memory Lookup, Graph Traversal, Skill Injection, Approach Synthesis, Blocker ID.

Phase 1 Lite (MVP): Memory Lookup only (ADR-0269).
Phase 2: Graph Traversal + Skill Injection (ADR-0269 Phase 2).
Phase 3: ADR-based decision discovery (Corvin-ADR integration).
"""

from .memory_lookup import MemoryLookup, MemoryMatch
from .rich_task_brief import RichTaskBrief, MemoryContext
from .graph_traversal import GraphTraversal, RelatedDecision, GraphTraversalResult
from .skill_injection import SkillInjection, RecommendedSkill, SkillInjectionResult
from .adr_loader import ADRLoader, ADRMetadata
from .adr_classifier import ADRClassifier
from .pipeline import build_brief, render_brief_to_text
from .license_gate import enforce_ce_quota
from .trace import persist_trace, read_recent_traces

__all__ = [
    # Phase 1
    "MemoryLookup",
    "MemoryMatch",
    "RichTaskBrief",
    "MemoryContext",
    # Phase 2
    "GraphTraversal",
    "RelatedDecision",
    "GraphTraversalResult",
    "SkillInjection",
    "RecommendedSkill",
    "SkillInjectionResult",
    # Phase 3 (ADR Integration)
    "ADRLoader",
    "ADRMetadata",
    "ADRClassifier",
]

__version__ = "0.3.0"  # Phase 3: ADR integration
