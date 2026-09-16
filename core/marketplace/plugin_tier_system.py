"""Phase 3 k=4: Plugin Tier System — Access Control & Capability Gating (ADR-0775).

This module implements three-tier plugin classification with capability-based access control:
1. BUILDIN: Hard-wired, audit-first, full access (never removable)
2. VETTED: Reviewed + signed, full access, versioned
3. COMMUNITY: User-contributed, signed, limited access (subprocess-sandboxed)

Fail-closed: unauthorized capability access raises CapabilityDeniedError.
Audit-first: every capability check is logged (ADR-0232).
Tenant-scoped: all access decisions filtered by tenant_id (GDPR Art. 32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Set
from uuid import uuid4

logger = logging.getLogger(__name__)


class PluginTier(Enum):
    """Plugin classification tier (ADR-0775)."""

    BUILDIN = "buildin"  # Hard-wired, audit-first, never removable
    VETTED = "vetted"  # Reviewed, signed, versioned
    COMMUNITY = "community"  # User-contributed, sandboxed, limited access


class PluginCapability(Enum):
    """Capabilities a plugin can access (role-based)."""

    # Core capabilities (restricted)
    AUDIT_CHAIN_READ = "audit_chain_read"  # Can read immutable audit trail
    AUDIT_CHAIN_WRITE = "audit_chain_write"  # Can append to audit trail (BUILDIN only)
    TENANT_DATA_READ = "tenant_data_read"  # Can read tenant-scoped data
    TENANT_DATA_WRITE = "tenant_data_write"  # Can write tenant data (BUILDIN/VETTED)

    # Standard capabilities
    SKILL_EXECUTION = "skill_execution"  # Can execute Skills
    MODEL_SELECTION = "model_selection"  # Can select model variants
    CONTEXT_ADAPTATION = "context_adaptation"  # Can adapt context
    CONFIGURATION_READ = "configuration_read"  # Can read config
    CONFIGURATION_WRITE = "configuration_write"  # Can write config (BUILDIN/VETTED)

    # External capabilities
    NETWORK_EGRESS = "network_egress"  # Can make outbound network calls
    SUBPROCESS_SPAWN = "subprocess_spawn"  # Can spawn subprocesses (COMMUNITY only)


# Capability matrix: tier → allowed capabilities
TIER_CAPABILITY_MATRIX: dict[PluginTier, Set[PluginCapability]] = {
    PluginTier.BUILDIN: {
        # Full access (core + standard + external)
        PluginCapability.AUDIT_CHAIN_READ,
        PluginCapability.AUDIT_CHAIN_WRITE,
        PluginCapability.TENANT_DATA_READ,
        PluginCapability.TENANT_DATA_WRITE,
        PluginCapability.SKILL_EXECUTION,
        PluginCapability.MODEL_SELECTION,
        PluginCapability.CONTEXT_ADAPTATION,
        PluginCapability.CONFIGURATION_READ,
        PluginCapability.CONFIGURATION_WRITE,
        PluginCapability.NETWORK_EGRESS,
    },
    PluginTier.VETTED: {
        # Full access except audit write
        PluginCapability.AUDIT_CHAIN_READ,
        PluginCapability.TENANT_DATA_READ,
        PluginCapability.TENANT_DATA_WRITE,
        PluginCapability.SKILL_EXECUTION,
        PluginCapability.MODEL_SELECTION,
        PluginCapability.CONTEXT_ADAPTATION,
        PluginCapability.CONFIGURATION_READ,
        PluginCapability.CONFIGURATION_WRITE,
        PluginCapability.NETWORK_EGRESS,
    },
    PluginTier.COMMUNITY: {
        # Limited access (no audit, no config write, subprocess only)
        PluginCapability.TENANT_DATA_READ,
        PluginCapability.SKILL_EXECUTION,
        PluginCapability.MODEL_SELECTION,
        PluginCapability.CONTEXT_ADAPTATION,
        PluginCapability.CONFIGURATION_READ,
        PluginCapability.SUBPROCESS_SPAWN,  # Sandboxed execution
    },
}


class CapabilityDeniedError(Exception):
    """Raised when capability access is denied."""

    pass


@dataclass(frozen=True)
class PluginMetadata:
    """Immutable plugin metadata with tier and capabilities."""

    plugin_id: str
    tier: PluginTier
    version: str
    source_url: str  # Where plugin is hosted
    signature: Optional[str] = None  # Ed25519 signature (base64)
    signature_verified: bool = False
    maintainer_key_id: Optional[str] = None  # Which key signed this
    timestamp: datetime = field(default_factory=datetime.now)
    removable: bool = True  # BUILDIN plugins are never removable


@dataclass(frozen=True)
class CapabilityCheckEvent:
    """Immutable audit event for capability check (ADR-0232)."""

    plugin_id: str
    capability: PluginCapability
    allowed: bool
    tier: PluginTier
    tenant_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    reason: Optional[str] = None  # Why denied (if allowed=False)


class PluginTierGate:
    """Enforces capability-based access control (ADR-0775).

    Fail-closed: unauthorized access raises CapabilityDeniedError.
    Audit-first: every check is logged for compliance (GDPR Art. 30, 32).
    """

    def __init__(self, tenant_id: str):
        """Initialize gate for a tenant.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)

        Raises:
            ValueError: If tenant_id missing (fail-closed)
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")

        self.tenant_id = tenant_id
        self._plugin_registry: dict[str, PluginMetadata] = {}
        self._check_history: list[CapabilityCheckEvent] = []
        self._blocked_capabilities: dict[str, int] = {}  # Denied count per plugin

    def register_plugin(
        self,
        plugin_id: str,
        tier: PluginTier,
        version: str,
        source_url: str,
        signature: Optional[str] = None,
        signature_verified: bool = False,
        maintainer_key_id: Optional[str] = None,
    ) -> PluginMetadata:
        """Register a plugin with tier and signature.

        Args:
            plugin_id: Unique plugin identifier
            tier: PluginTier (BUILDIN/VETTED/COMMUNITY)
            version: Semantic version (e.g., "1.0.0")
            source_url: Where plugin is hosted
            signature: Ed25519 signature (base64, required for VETTED/COMMUNITY)
            signature_verified: Whether signature is verified (fail-closed: False unless verified)
            maintainer_key_id: ID of key that signed this

        Returns:
            Immutable PluginMetadata

        Raises:
            ValueError: If validation fails (fail-closed)
        """
        if not plugin_id:
            raise ValueError("plugin_id required")
        if not version:
            raise ValueError("version required")

        # VETTED and COMMUNITY plugins must be signed
        if tier in (PluginTier.VETTED, PluginTier.COMMUNITY) and not signature:
            raise ValueError(f"{tier.value} plugins must be signed")

        # BUILDIN plugins cannot be removed
        removable = tier != PluginTier.BUILDIN

        metadata = PluginMetadata(
            plugin_id=plugin_id,
            tier=tier,
            version=version,
            source_url=source_url,
            signature=signature,
            signature_verified=signature_verified,
            maintainer_key_id=maintainer_key_id,
            removable=removable,
        )

        self._plugin_registry[plugin_id] = metadata
        logger.info(f"Registered plugin {plugin_id} as {tier.value} (v{version})")

        return metadata

    def check_capability(
        self,
        plugin_id: str,
        capability: PluginCapability,
        lom: Optional[str] = None,
    ) -> bool:
        """Check if plugin can access capability (fail-closed).

        Every check is audited regardless of result.

        Args:
            plugin_id: Plugin requesting capability
            capability: PluginCapability being requested
            lom: Line of Moral Responsibility (audit attribution)

        Returns:
            True if allowed, False if denied

        Raises:
            CapabilityDeniedError: If denied (fail-closed, caller must handle)
            ValueError: If plugin not registered (fail-closed)
        """
        if plugin_id not in self._plugin_registry:
            raise ValueError(f"Plugin {plugin_id} not registered")

        metadata = self._plugin_registry[plugin_id]
        allowed_capabilities = TIER_CAPABILITY_MATRIX.get(metadata.tier, set())
        allowed = capability in allowed_capabilities

        # Create audit event
        reason = None
        if not allowed:
            reason = f"Capability {capability.value} not in {metadata.tier.value} tier"
            if plugin_id not in self._blocked_capabilities:
                self._blocked_capabilities[plugin_id] = 0
            self._blocked_capabilities[plugin_id] += 1

        event = CapabilityCheckEvent(
            plugin_id=plugin_id,
            capability=capability,
            allowed=allowed,
            tier=metadata.tier,
            tenant_id=self.tenant_id,
            reason=reason,
        )
        self._check_history.append(event)

        # Log
        if allowed:
            logger.debug(f"Capability check: {plugin_id}.{capability.value} → ALLOWED")
        else:
            logger.warning(
                f"Capability check: {plugin_id}.{capability.value} → DENIED "
                f"({metadata.tier.value} tier)"
            )

        # Fail-closed: raise if denied
        if not allowed:
            raise CapabilityDeniedError(
                f"Plugin {plugin_id} (tier={metadata.tier.value}) cannot access "
                f"{capability.value}"
            )

        return True

    def get_plugin_metadata(self, plugin_id: str) -> Optional[PluginMetadata]:
        """Get plugin metadata (immutable).

        Args:
            plugin_id: Plugin to query

        Returns:
            PluginMetadata or None if not registered
        """
        return self._plugin_registry.get(plugin_id)

    def get_plugin_capabilities(self, plugin_id: str) -> Set[PluginCapability]:
        """Get allowed capabilities for a plugin.

        Args:
            plugin_id: Plugin to query

        Returns:
            Set of allowed PluginCapability (empty if plugin not registered)
        """
        metadata = self._plugin_registry.get(plugin_id)
        if not metadata:
            return set()
        return TIER_CAPABILITY_MATRIX.get(metadata.tier, set())

    def get_capability_check_history(self) -> list[CapabilityCheckEvent]:
        """Get all capability checks (audit trail, read-only).

        Returns:
            List of CapabilityCheckEvent in chronological order
        """
        return list(self._check_history)

    def get_denied_capabilities_count(self, plugin_id: str) -> int:
        """Get number of denied capability checks for a plugin.

        Args:
            plugin_id: Plugin to query

        Returns:
            Count of denials (0 if plugin not registered or all allowed)
        """
        return self._blocked_capabilities.get(plugin_id, 0)

    def is_removable(self, plugin_id: str) -> bool:
        """Check if plugin can be removed (BUILDIN plugins cannot).

        Args:
            plugin_id: Plugin to query

        Returns:
            True if removable, False if BUILDIN (locked)
        """
        metadata = self._plugin_registry.get(plugin_id)
        return metadata.removable if metadata else True


class PluginCapabilityRegistry:
    """Registry of all plugin tiers and capabilities (read-only reference).

    Provides operator-facing capability matrix for documentation and UI.
    """

    @staticmethod
    def get_tier_display(tier: PluginTier) -> dict[str, any]:
        """Get display info for a tier.

        Args:
            tier: PluginTier to describe

        Returns:
            Dict with description, removable status, capabilities
        """
        descriptions = {
            PluginTier.BUILDIN: "Hard-wired, audit-first, full access (cannot be removed)",
            PluginTier.VETTED: "Reviewed + signed, full access except audit write",
            PluginTier.COMMUNITY: "User-contributed, signed, limited access (sandboxed)",
        }

        removable = {
            PluginTier.BUILDIN: False,
            PluginTier.VETTED: True,
            PluginTier.COMMUNITY: True,
        }

        return {
            "tier": tier.value,
            "description": descriptions[tier],
            "removable": removable[tier],
            "capabilities": [
                cap.value for cap in TIER_CAPABILITY_MATRIX.get(tier, set())
            ],
        }

    @staticmethod
    def get_all_tiers() -> list[dict]:
        """Get display info for all tiers.

        Returns:
            List of tier info dicts
        """
        return [
            PluginCapabilityRegistry.get_tier_display(tier) for tier in PluginTier
        ]

    @staticmethod
    def get_capability_description(capability: PluginCapability) -> str:
        """Get human-readable description of a capability.

        Args:
            capability: PluginCapability to describe

        Returns:
            Description string
        """
        descriptions = {
            PluginCapability.AUDIT_CHAIN_READ: "Can read immutable audit trail",
            PluginCapability.AUDIT_CHAIN_WRITE: "Can append to audit trail (BUILDIN only)",
            PluginCapability.TENANT_DATA_READ: "Can read tenant-scoped data",
            PluginCapability.TENANT_DATA_WRITE: "Can write tenant data",
            PluginCapability.SKILL_EXECUTION: "Can execute Skills",
            PluginCapability.MODEL_SELECTION: "Can select model variants",
            PluginCapability.CONTEXT_ADAPTATION: "Can adapt context",
            PluginCapability.CONFIGURATION_READ: "Can read configuration",
            PluginCapability.CONFIGURATION_WRITE: "Can write configuration",
            PluginCapability.NETWORK_EGRESS: "Can make outbound network calls",
            PluginCapability.SUBPROCESS_SPAWN: "Can spawn subprocesses (sandboxed)",
        }
        return descriptions.get(capability, "Unknown capability")
