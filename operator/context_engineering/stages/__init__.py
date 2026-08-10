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
from .registry import register_stage, get_stage, known_ids, all_specs
from .config import StageSpec, resolve_pipeline, DEFAULT_PIPELINE, topo_order

# Import the first-party stages so they self-register at package import.
from . import memory, graph, skill, approach, blocker  # noqa: F401,E402

__all__ = [
    "ContextBundle", "StageCtx", "StageTelemetry", "ContextStage", "SCRATCH_KEYS",
    "register_stage", "get_stage", "known_ids", "all_specs",
    "StageSpec", "resolve_pipeline", "DEFAULT_PIPELINE", "topo_order",
]
