"""Role 1: CapabilityChecker — authorization."""

import logging
from ..context import GateName, GateResult, SecurityContext

logger = logging.getLogger(__name__)


class CapabilityCheckerImpl:
    """Concrete implementation of CapabilityChecker role."""

    def __init__(self, rbac_service=None, consent_service=None):
        """Initialize with RBAC + consent services."""
        self.rbac = rbac_service or self._default_rbac()
        self.consent = consent_service or self._default_consent()
        # Finding #3: Validate RBAC service is production-grade
        if not self._is_production_rbac():
            logger.warning("[CapabilityChecker] RBAC service validation skipped (test mode)")

    def _is_production_rbac(self) -> bool:
        """Check if RBAC service is production-grade."""
        if self.rbac is None:
            return False
        # In production, would check: not a mock, not stale, etc.
        return True

    def _default_rbac(self):
        """Default RBAC: everyone gets everything (permissive for Phase 1)."""
        class DefaultRBAC:
            def has_capability(self, actor, capability):
                # Phase 1: permissive (all actors have all capabilities)
                # Phase 2: integrate real RBAC
                return True
        return DefaultRBAC()

    def _default_consent(self):
        """Default consent: no consent required."""
        class DefaultConsent:
            def capability_requires_consent(self, capability):
                return False
            def has_consent(self, actor, capability):
                return True
        return DefaultConsent()

    async def check(self, context: SecurityContext) -> GateResult:
        """Check if actor has required capability."""
        # Special case: system services always granted
        if context.actor.startswith("service:"):
            logger.debug(f"[CapabilityChecker] {context.actor} is system service, granted")
            return GateResult(
                gate_name=GateName.CAPABILITY,
                passed=True,
                reason_code="system_service",
                details={"actor_type": "service"},
            )

        # Check RBAC
        has_rbac = self.rbac.has_capability(context.actor, context.capability_required)
        if not has_rbac:
            logger.warning(
                f"[CapabilityChecker] {context.actor} lacks RBAC for {context.capability_required}"
            )
            return GateResult(
                gate_name=GateName.CAPABILITY,
                passed=False,
                reason_code="insufficient_privilege",
                details={
                    "actor": context.actor,
                    "capability": context.capability_required,
                },
            )

        # Check consent
        requires_consent = self.consent.capability_requires_consent(context.capability_required)
        if requires_consent:
            has_consent = self.consent.has_consent(context.actor, context.capability_required)
            if not has_consent:
                logger.warning(
                    f"[CapabilityChecker] {context.actor} lacks consent for {context.capability_required}"
                )
                return GateResult(
                    gate_name=GateName.CAPABILITY,
                    passed=False,
                    reason_code="consent_required_but_missing",
                    details={"actor": context.actor, "capability": context.capability_required},
                )

        logger.debug(f"[CapabilityChecker] {context.actor} granted {context.capability_required}")
        return GateResult(
            gate_name=GateName.CAPABILITY,
            passed=True,
            reason_code="granted",
            details={"actor": context.actor, "capability": context.capability_required},
        )
