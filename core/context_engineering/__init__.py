"""Context Engineering — capability enums and ExecutionContext v2.

The persona model (capability registry, ContextVar persona/role state, transport
resolvers, auth decorators) was removed with the Personas Elimination
(commit e7e3560e); capability checks are served by the ``os.capabilities`` Skill.
"""

from .capabilities import Capability, Persona, Role, Tier
from .execution_context import (
    ContextStack,
    ContextStackFrame,
    ExecutionContext,
)
from .decision_record import DecisionRecord
from .context_bus import ContextBus
from .context_api import ContextAPI
from .memory_coordinator import (
    MemoryCoordinator,
    MemoryCoordinatorError,
    MemoryLayerNotFound,
    EventPersistenceError,
)

__all__ = [
    "Persona",
    "Role",
    "Tier",
    "Capability",
    # ADR-0358: ExecutionContext v2
    "ExecutionContext",
    "ContextStack",
    "ContextStackFrame",
    "DecisionRecord",
    "ContextBus",
    "ContextAPI",
    "MemoryCoordinator",
    "MemoryCoordinatorError",
    "MemoryLayerNotFound",
    "EventPersistenceError",
]
