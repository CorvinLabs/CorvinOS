"""k=1 Endpoint Architecture (ADR-0515)."""

from core.endpoints.k1_context import (
    K1ConnectionContext,
    K1PipelineEnforcer,
    K1RequestContext,
    PerRequestTaskQueue,
    RequestSessionContext,
    get_k1_context,
)
from core.endpoints.k1_decorators import (
    k1_async,
    k1_cli,
    k1_flask,
    k1_ws,
)

__all__ = [
    # Contexts
    'K1ConnectionContext',
    'RequestSessionContext',
    'PerRequestTaskQueue',
    'K1PipelineEnforcer',
    'K1RequestContext',
    # Utilities
    'get_k1_context',
    # Decorators
    'k1_flask',
    'k1_cli',
    'k1_async',
    'k1_ws',
]
