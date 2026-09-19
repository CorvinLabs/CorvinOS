"""
ADR-0302: Persona Capability Axis — Centralized, deny-by-default capability model.

A Persona is an identity + environment bundle. Capabilities are fine-grained permissions.
This module provides the single source of truth for all capability checks across CorvinOS.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Set, Dict, Optional
import threading
from datetime import datetime


class Persona(Enum):
    """Identity + environment bundles for CorvinOS users."""
    CONSOLE_OPERATOR = auto()
    VOICE_USER = auto()
    BRIDGE_ADAPTER = auto()
    PLUGIN_SYSTEM = auto()


class Role(Enum):
    """Permission tiers within a Persona."""
    ADMIN = auto()
    OPERATOR = auto()
    USER = auto()
    VIEWER = auto()
    GUEST = auto()


class Tier(Enum):
    """Capability tiers."""
    COMPLIANCE = auto()
    STANDARD = auto()
    PERFORMANCE = auto()
    USER = auto()


@dataclass(frozen=True)
class Capability:
    """Atomic permission (immutable)."""
    id: str
    description: str
    tier: Tier
    requires_mfa: bool = False


class CapabilityRegistry:
    """
    Central registry for persona capabilities.
    Deny-by-default: missing capabilities always return False.
    Per-tenant: all operations scoped to tenant_id.
    Thread-safe: protected by RWLock.
    """
    
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self._lock = threading.RLock()
        self._capabilities: Dict[str, Capability] = {}
        self._grants: Dict[tuple, Set[str]] = {}
        self._audit_trail: list = []
    
    def register_capability(self, capability: Capability) -> None:
        """Register a capability definition."""
        with self._lock:
            if capability.id in self._capabilities:
                raise ValueError(f"Capability {capability.id} already registered")
            self._capabilities[capability.id] = capability
    
    def grant(self, persona: Persona, role: Role, capability_id: str) -> None:
        """Explicitly grant a capability to a (persona, role) pair."""
        with self._lock:
            if capability_id not in self._capabilities:
                raise ValueError(f"Capability {capability_id} not registered")
            
            key = (persona, role)
            if key not in self._grants:
                self._grants[key] = set()
            self._grants[key].add(capability_id)
    
    def has_capability(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
        mfa_verified: bool = False
    ) -> bool:
        """
        Check if (persona, role) has a capability.
        Deny-by-default: returns False if capability not explicitly granted.
        """
        with self._lock:
            if capability_id not in self._capabilities:
                return False
            
            key = (persona, role)
            has_grant = key in self._grants and capability_id in self._grants[key]
            
            if not has_grant:
                return False
            
            cap = self._capabilities[capability_id]
            if cap.requires_mfa and not mfa_verified:
                return False
            
            return True
    
    def get_capabilities(self, persona: Persona, role: Role) -> frozenset:
        """Get all capabilities for a (persona, role) pair."""
        with self._lock:
            key = (persona, role)
            caps = self._grants.get(key, set())
            return frozenset(caps)


# Global registry per tenant
_registries: Dict[str, CapabilityRegistry] = {}
_registries_lock = threading.Lock()


def get_registry(tenant_id: str) -> CapabilityRegistry:
    """Get or create the capability registry for a tenant."""
    with _registries_lock:
        if tenant_id not in _registries:
            _registries[tenant_id] = CapabilityRegistry(tenant_id)
        return _registries[tenant_id]


def bootstrap_default_capabilities(tenant_id: str) -> None:
    """Bootstrap default capabilities for a tenant at boot time."""
    registry = get_registry(tenant_id)
    
    capabilities = [
        Capability("read_audit_log", "Read the audit trail", Tier.COMPLIANCE, requires_mfa=True),
        Capability("verify_audit_chain", "Verify audit chain integrity", Tier.COMPLIANCE, requires_mfa=True),
        Capability("read_settings", "Read system settings", Tier.STANDARD),
        Capability("write_settings", "Modify system settings", Tier.STANDARD),
        Capability("start_service", "Start/stop services", Tier.STANDARD),
        Capability("read_user_preferences", "Read personal preferences", Tier.USER),
        Capability("write_user_preferences", "Update personal preferences", Tier.USER),
    ]
    
    for cap in capabilities:
        try:
            registry.register_capability(cap)
        except ValueError:
            pass
    
    # Console operator (admin role): everything
    for cap in capabilities:
        registry.grant(Persona.CONSOLE_OPERATOR, Role.ADMIN, cap.id)
    
    # Console operator (operator role): most things except MFA-required
    for cap in capabilities:
        if not cap.requires_mfa:
            registry.grant(Persona.CONSOLE_OPERATOR, Role.OPERATOR, cap.id)
    
    # Voice user (user role): personal preferences only
    registry.grant(Persona.VOICE_USER, Role.USER, "read_user_preferences")
    registry.grant(Persona.VOICE_USER, Role.USER, "write_user_preferences")
