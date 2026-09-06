"""
Compatibility layer for Persona system → Skills 2.0 migration (ADR-0537 Phase 1)

Old APIs continue to work. Internally, routes through Skills.
Dual-log mode: both old + new paths run in parallel, results compared for divergence.
Emits audit event if divergence detected (investigation aid for operator).
"""

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Persona(Enum):
    """DEPRECATED: Use os.capabilities Skill instead. Kept for compat only."""
    CONSOLE_OPERATOR = "console_operator"
    VOICE_USER = "voice_user"
    BRIDGE_ADAPTER = "bridge_adapter"
    MCP_TOOL = "mcp_tool"


class Role(Enum):
    """DEPRECATED: Use Skill manifest field `required_role` instead."""
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"


class _CompatCapabilityRegistry:
    """
    Deprecated compat layer. Routes to os.capabilities Skill.

    Characteristics:
    - Old CapabilityRegistry.has_capability() API still works
    - Internally calls os.capabilities Skill
    - Dual-log mode: compares old + new results, emits divergence audit event
    - Fail-closed: exceptions from Skill → return False (deny)
    """

    def __init__(self):
        """Initialize compat layer."""
        from core.skills.os_skills.capabilities_skill import CapabilitiesSkill

        self.skill = CapabilitiesSkill()
        self.divergence_count = 0
        self.divergence_threshold = 5  # Alert after 5 divergences

    def has_capability(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
        tenant_id: str = "_default"
    ) -> bool:
        """
        Old API; routes to Skill.

        Dual-log mode: compares old + new results, emits divergence audit event if different.

        Args:
            persona: Persona (DEPRECATED; kept for compat)
            role: Role
            capability_id: str
            tenant_id: str (default: "_default")

        Returns:
            bool (has_capability)

        Note:
            Personas are now mapped to roles; persona parameter is unused but accepted.
            Fail-closed: if Skill raises exception, returns False (deny).
        """
        from core.skills.os_skills.capabilities_skill import CapabilityCheckInput
        from core.learning.audit_backend import audit_backend

        # Get old registry result (query old CapabilityRegistry directly)
        try:
            old_result = self._query_old_registry(persona, role, capability_id, tenant_id)
        except Exception as e:
            logger.warning(f"Old registry query failed: {e}; treating as deny")
            old_result = False

        # Get new Skill result
        try:
            input = CapabilityCheckInput(
                role=role.value,
                tenant_id=tenant_id,
                capability_id=capability_id
            )
            new_result = self.skill.execute(input)
            new_capability = new_result.has_capability
        except Exception as e:
            # Skill error: deny (fail-closed)
            logger.warning(f"Skill execution failed: {e}; denying capability")
            new_capability = False

        # DIVERGENCE CHECK: compare results
        if old_result != new_capability:
            self.divergence_count += 1

            try:
                # Emit divergence audit event (for operator investigation)
                audit_backend.write_event(
                    tenant_id=tenant_id,
                    event_type="dual_log_divergence",
                    payload={
                        "old_registry_result": old_result,
                        "new_skill_result": new_capability,
                        "role": role.value,
                        "capability_id": capability_id,
                        "divergence_count": self.divergence_count,
                    }
                )
            except Exception as e:
                logger.error(f"Failed to emit divergence audit event: {e}")

            # Alert operator if threshold crossed
            if self.divergence_count >= self.divergence_threshold:
                logger.error(
                    f"DUAL_LOG_DIVERGENCE ALERT: {self.divergence_count} divergences detected. "
                    f"Old registry and new Skill are NOT in sync. Review audit trail for details."
                )

        # Return old result (Personas still primary during Phase 1)
        return old_result

    def _query_old_registry(
        self,
        persona: Persona,
        role: Role,
        capability_id: str,
        tenant_id: str
    ) -> bool:
        """
        Query the original CapabilityRegistry (direct access).

        This assumes the old registry code is still available (not deleted yet).
        Will be removed in Phase 4 when old code is deleted.
        """
        from core.context_engineering.persona_model import _REGISTRY as OLD_REGISTRY

        return OLD_REGISTRY.has_capability(persona, role, capability_id, tenant_id)


# Global compat instance (replaces old _REGISTRY)
_REGISTRY = _CompatCapabilityRegistry()


def has_capability(
    persona: Persona,
    role: Role,
    capability_id: str,
    tenant_id: str = "_default"
) -> bool:
    """
    DEPRECATED: Use os.capabilities Skill directly.

    This is a compat wrapper for old code.
    """
    import warnings

    warnings.warn(
        "has_capability() is deprecated. Use os.capabilities Skill instead. "
        "(See ADR-0537 for migration path.)",
        DeprecationWarning,
        stacklevel=2
    )

    return _REGISTRY.has_capability(persona, role, capability_id, tenant_id)
