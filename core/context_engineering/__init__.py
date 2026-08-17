"""Context Engineering — Persona, Role, Capability Model, and ExecutionContext v2."""

from core.context_engineering.capabilities import Capability, Persona, Role, Tier
from core.context_engineering.persona_model import (
    CapabilityDenied,
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
from core.context_engineering.transport_resolvers import (
    AuthError,
    InvalidPersona,
    TransportResolver,
    UnresolvablePersona,
)
from core.context_engineering.auth_decorators import (
    auth_required_cli,
    auth_required_flask,
    requires_auth_capability,
)
from core.context_engineering.execution_context import (
    ContextStack,
    ContextStackFrame,
    ExecutionContext,
)
from core.context_engineering.decision_record import DecisionRecord
from core.context_engineering.context_bus import ContextBus
from core.context_engineering.context_api import ContextAPI

__all__ = [
    "Persona",
    "Role",
    "Tier",
    "Capability",
    "CapabilityRegistry",
    "CapabilityDenied",
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
]
