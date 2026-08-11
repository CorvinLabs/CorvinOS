"""Context Engineering — Persona, Role, and Capability Model."""

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
]
