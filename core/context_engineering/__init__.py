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
