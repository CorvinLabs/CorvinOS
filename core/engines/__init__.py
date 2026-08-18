"""Engine subsystem (Phase 0+).

Unified interface for all compute engines:
- Claude, Opus, Sonnet, Haiku
- Hermes (local)
- Fallback chains and load balancing
"""

from core.engines.engine_interface import (
    EngineType,
    EngineStatus,
    EngineCapability,
    EngineRequest,
    EngineResponse,
    EngineInterface,
    EnginePool,
)
from core.engines.execution_context import (
    ExecutionState,
    ExecutionContext,
    ExecutionContextUpdate,
    ExecutionContextStore,
)

__all__ = [
    "EngineType",
    "EngineStatus",
    "EngineCapability",
    "EngineRequest",
    "EngineResponse",
    "EngineInterface",
    "EnginePool",
    "ExecutionState",
    "ExecutionContext",
    "ExecutionContextUpdate",
    "ExecutionContextStore",
]
