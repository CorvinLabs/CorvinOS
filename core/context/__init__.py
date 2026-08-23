"""Context Pipeline v2 — Two-layer architecture for context preservation.

ADR-0399: Preservation+Additive Model
Prevents context drift by separating original (immutable) from pipeline (additive) context.

Module exports:
  - OriginalContext: Immutable user goal + constraints
  - PipelineContext: Additive skill/memory/ADR injections
  - ContextLayerComposer: Combines both into system prompt
"""

from .original_context import (
    OriginalContext,
    ContextScope,
    capture_original_context,
)

from .pipeline_context import (
    PipelineContext,
    PipelineAddition,
    QualityTier,
    create_pipeline_context,
    add_memory_context,
)

__all__ = [
    "OriginalContext",
    "ContextScope",
    "capture_original_context",
    "PipelineContext",
    "PipelineAddition",
    "QualityTier",
    "create_pipeline_context",
    "add_memory_context",
]
