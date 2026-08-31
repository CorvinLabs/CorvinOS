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

# Context helpers (thread/async context propagation). `get_current_context` is
# the public read side used by core/skills/learning_loop.py; it was reachable
# only via the submodule path before, so `from core.context import
# get_current_context` -- the form used by callers and tests -- raised
# ImportError.
from .async_context import (
    async_run_with_context,
    async_task_with_context,
    gather_with_context,
)

from .thread_context import (
    ContextPreservingExecutor,
    executor_submit_with_context,
    thread_with_context,
)

from .helpers import (
    ContextError,
    get_current_context,
    set_context,
    preserve_context,
)

# persona_aware_pipeline enforces the ADR-0302 deny-by-default capability gate.
# This import is deliberately unguarded: it used to sit behind
# `except (ImportError, AttributeError): pass`, which combined with a stub
# fallback inside persona_aware_pipeline itself to hide the fact that the module
# raised AttributeError at import time and the gate was never active. A security
# gate that fails silently is worse than one that fails loudly.
from .persona_aware_pipeline import (
    PersonaAwarePipeline,
    PersonaContextPolicy,
    ContextVisibility,
    create_persona_aware_pipeline,
)

__all__ += [
    "async_run_with_context",
    "async_task_with_context",
    "gather_with_context",
    "thread_with_context",
    "executor_submit_with_context",
    "ContextPreservingExecutor",
    "ContextError",
    "get_current_context",
    "set_context",
    "preserve_context",
    "PersonaAwarePipeline",
    "PersonaContextPolicy",
    "ContextVisibility",
    "create_persona_aware_pipeline",
]
