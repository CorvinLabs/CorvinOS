"""Context Engineering — Persona Capability Axis (ADR-0302, ADR-0294)

Centralized identity and capability management for CorvinOS.
- Persona: identity + environment (console_operator, voice_user, bridge_adapter, mcp_tool)
- Role: capability partition (admin, operator, user)
- Capability: fine-grained permission (read_audit_log, write_feature_flag, etc.)
"""

from core.context_engineering.persona_model import (
    # Enums
    Persona,
    Role,
    Tier,
    # Data structures
    Capability,
    PersonaRoleCapabilities,
    # Registry
    CapabilityRegistry,
    REGISTRY,
    # Context variables
    current_persona,
    current_role,
    get_current_persona,
    set_current_persona,
    get_current_role,
    set_current_role,
    # Decorators
    requires_capability,
    # Exceptions
    CapabilityLockError,
    CapabilityDeniedError,
    PersonaResolutionError,
)

from core.context_engineering.transport_resolvers import TransportResolver

from core.context_engineering.auth_decorators import (
    # Flask decorators
    auth_required,
    requires_auth_capability,
    audit_request,
    # CLI decorators
    cli_auth_required,
    cli_requires_capability,
    # Async decorators
    async_auth_required,
    async_requires_capability,
    # Exceptions
    AuthError,
    UnresolvablePersona,
    MissingCapability,
)

__all__ = [
    # Enums
    "Persona",
    "Role",
    "Tier",
    # Data structures
    "Capability",
    "PersonaRoleCapabilities",
    # Registry
    "CapabilityRegistry",
    "REGISTRY",
    # Context variables
    "current_persona",
    "current_role",
    "get_current_persona",
    "set_current_persona",
    "get_current_role",
    "set_current_role",
    # Transport resolver
    "TransportResolver",
    # Decorators (Flask)
    "auth_required",
    "requires_auth_capability",
    "audit_request",
    # Decorators (CLI)
    "cli_auth_required",
    "cli_requires_capability",
    # Decorators (Async)
    "async_auth_required",
    "async_requires_capability",
    # Decorators (general)
    "requires_capability",
    # Exceptions
    "CapabilityLockError",
    "CapabilityDeniedError",
    "PersonaResolutionError",
    "AuthError",
    "UnresolvablePersona",
    "MissingCapability",
]
