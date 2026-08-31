"""Context Engineering stages (ADR-0280, CONCEPT-0006).

The CEL pipeline as a registry of typed `ContextStage`s, composed from a per-tenant
config, instead of five hard-coded calls in `build_brief`. P-A ships the contract,
the registry, the five first-party stages (re-expressing today's logic) and the
config-driven sync runner. Later phases add the dual channel (P-B), the LLM
synthesis stage (P-C), ToolForge/SkillForge stages (P-D), and grading (P-F).
"""
from .base import (
    ContextBundle, StageCtx, StageTelemetry, ContextStage, SCRATCH_KEYS,
)
from .registry import (register_stage, register_community_stage,
                       unregister_stage, builtin_ids, get_stage, known_ids,
                       all_specs)
from .sandbox import SandboxedStage, sandbox_available, run_stage_sandboxed
from .config import StageSpec, resolve_pipeline, DEFAULT_PIPELINE, topo_order
from .binding import (
    ToolRef, SkillRef, revalidate_tools, strip_for_remote, apply_tool_bindings,
    render_skill_bindings, MAX_BINDINGS,
)

# Import the first-party stages so they self-register at package import.
# The five default stages + the opt-in egress/forge stages (not in DEFAULT_PIPELINE).
from . import memory, graph, skill, approach, blocker  # noqa: F401,E402
from . import llm_synthesis  # noqa: F401,E402  (opt-in, ADR-0282)
from . import toolforge, skillforge  # noqa: F401,E402  (opt-in, ADR-0283)
from . import explicit_skill  # noqa: F401,E402  (honor user-named on-disk skills)
from .grades import (  # noqa: E402  (ADR-0285)
    grade_stage, get_grade, bootstrap_seed, is_default_eligible, record_turn_outcome,
)
# Phase 5: Advanced Context Optimizations (ADR-0394)
from . import selective_injection_stage, memory_pruning_stage, adr_reranking_stage  # noqa: F401,E402

__all__ = [
    "ContextBundle", "StageCtx", "StageTelemetry", "ContextStage", "SCRATCH_KEYS",
    "register_stage", "get_stage", "known_ids", "all_specs",
    "StageSpec", "resolve_pipeline", "DEFAULT_PIPELINE", "topo_order",
]
