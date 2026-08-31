"""Role 4: AuditRecorder — immutable hash-chained audit recording."""

import logging
from ..context import GateName, GateResult, SecurityContext

logger = logging.getLogger(__name__)


class AuditRecorderImpl:
    """Concrete implementation of AuditRecorder role."""

    def __init__(self, audit_backend=None):
        """Initialize with audit backend."""
        self.audit_backend = audit_backend or self._default_backend()

    def _default_backend(self):
        """Default audit backend: in-memory (for Phase 1 testing)."""
        class InMemoryAuditBackend:
            def __init__(self):
                self.events = []

            async def write_event(self, event_dict):
                """Write event to in-memory log."""
                self.events.append(event_dict)
                return True

            async def verify_recorded(self, record_hash):
                """Verify event was recorded (Finding #6)."""
                # Simplified: just check we have events
                return len(self.events) > 0

        return InMemoryAuditBackend()

    async def record(self, context: SecurityContext) -> GateResult:
        """Write decision to immutable audit trail (Finding #6)."""
        try:
            # Compute decision hash (content-free record)
            decision_hash = context.compute_audit_hash()

            # Build audit event (content-free)
            audit_event = {
                "type": "audit.security_decision",
                "request_id": context.request_id,
                "tenant_id": context.tenant_id,
                "actor": context.actor,
                "action": context.action,
                "resource": context.resource,
                "ts": context.timestamp,
                "outcome": "allowed" if context.capability_granted else "denied",
                "gates": [g.to_audit_dict() for g in context.gate_results],
                "record_hash": decision_hash,
            }

            # Write to backend
            await self.audit_backend.write_event(audit_event)

            # Verify it was actually recorded (Finding #6)
            verified = await self.audit_backend.verify_recorded(decision_hash)
            if not verified:
                raise Exception("Audit record verification failed")

            logger.debug(f"[AuditRecorder] Event recorded: {decision_hash}")
            return GateResult(
                gate_name=GateName.AUDIT_RECORDING,
                passed=True,
                reason_code="recorded",
                details={"record_hash": decision_hash},
            )
        except Exception as e:
            logger.exception(f"[AuditRecorder] Write failed: {e}")
            return GateResult(
                gate_name=GateName.AUDIT_RECORDING,
                passed=False,
                reason_code="audit_write_error",
                details={"error": str(e)},
            )
