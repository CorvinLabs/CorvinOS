"""Context Engineering — Persona, Role, Capability Model, and ExecutionContext v2."""

from .capabilities import Capability, Persona, Role, Tier
from .persona_model import (
    CapabilityDenied,
    _REGISTRY as REGISTRY,
    CapabilityLockError,
    CapabilityRegistry,
    get_current_persona,
    get_current_role,
    get_current_tenant_id,
    get_registry,
    has_capability,
    requires_capability,
    set_current_persona,
    set_current_role,
    set_current_tenant_id,
)
from .transport_resolvers import (
    AuthError,
    InvalidPersona,
    TransportResolver,
    UnresolvablePersona,
)
from .auth_decorators import (
    auth_required_cli,
    auth_required_flask,
    requires_auth_capability,
)
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
    "CapabilityRegistry",
    "CapabilityDenied",
    # `CapabilityDeniedError` is the spelling used by callers and by
    # core/capabilities/registry.py's exception of the same role; both names
    # refer to one class so a caller cannot catch the wrong one.
    "CapabilityDeniedError",
    # The process-wide registry singleton. `get_registry()` returns this same
    # object; the module-level name is what call sites and tests reach for.
    "REGISTRY",
    "CapabilityLockError",
    "get_registry",
    "get_current_persona",
    "set_current_persona",
    "get_current_role",
    "set_current_role",
    "get_current_tenant_id",
    "set_current_tenant_id",
    "has_capability",
    "requires_capability",
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

# Alias: one exception class, two spellings in use across the codebase.
CapabilityDeniedError = CapabilityDenied
