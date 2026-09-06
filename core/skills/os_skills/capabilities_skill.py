"""
os.capabilities Skill — Replaces CapabilityRegistry (ADR-0537 Phase 1)

Immutable input validation, format checks, audit-first fail-closed design.
Thread-safe manifest loading (double-checked locking).
"""

import re
import threading
from dataclasses import dataclass
from typing import Optional, Set
from enum import Enum


class CapabilityCheckRole(Enum):
    """Valid roles for capability checks."""
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"


@dataclass(frozen=True)
class CapabilityCheckInput:
    """Immutable input for capability checks."""
    role: str  # Must be in CapabilityCheckRole
    tenant_id: str  # Scoped; fail-closed if null
    capability_id: str  # Atomic permission (alphanumeric+underscore only)


@dataclass(frozen=True)
class CapabilityCheckOutput:
    """Immutable output from capability check."""
    has_capability: bool
    reason: Optional[str]  # Explanation (e.g., "denied: operator lacks delete_user")
    capability_id: str
    role: str


class CapabilitiesSkill:
    """
    Replaces CapabilityRegistry.has_capability().

    Characteristics:
    - Immutable input/output (dataclasses, frozen)
    - Fail-closed: null tenant_id → SkillExecutionError (deny)
    - Fail-closed: audit write error → SkillExecutionError (deny)
    - Thread-safe manifest loading (double-checked locking)
    - PII-safe: capability_id must match regex ^[a-z0-9_]+$
    """

    skill_id = "os.capabilities"
    version = "1.0.0"
    tier = "compliance"  # Unskippable

    # Thread-safe manifest loading (double-checked locking pattern)
    _manifest = None
    _manifest_lock = threading.Lock()

    def __init__(self):
        """Initialize Skill with thread-safe manifest loading."""
        if CapabilitiesSkill._manifest is None:
            with CapabilitiesSkill._manifest_lock:
                if CapabilitiesSkill._manifest is None:
                    CapabilitiesSkill._manifest = self._load_manifest()
        self.capabilities_by_role = CapabilitiesSkill._manifest

    def execute(self, input: CapabilityCheckInput) -> CapabilityCheckOutput:
        """
        Check if role has capability_id.

        Fail-closed design:
        - null tenant_id → raise SkillExecutionError
        - invalid role → raise SkillExecutionError
        - capability_id format invalid → raise SkillExecutionError
        - audit write fails → raise SkillExecutionError (deny, not allow)

        Args:
            input: CapabilityCheckInput (immutable)

        Returns:
            CapabilityCheckOutput (immutable)

        Raises:
            SkillExecutionError: on any validation failure or audit error (fail-closed)
        """
        # Fail-closed: null tenant_id
        if not input.tenant_id:
            raise SkillExecutionError("tenant_id required (fail-closed)")

        # Fail-closed: invalid role
        if input.role not in [r.value for r in CapabilityCheckRole]:
            raise SkillExecutionError(f"invalid role: {input.role}")

        # Fail-closed: PII prevention — capability_id format validation
        # Only allow alphanumeric + underscore (no emails, secrets, special chars)
        if not re.match(r"^[a-z0-9_]+$", input.capability_id):
            raise SkillExecutionError(
                f"invalid capability_id format (alphanumeric+underscore only): {input.capability_id}"
            )

        # Query capability table
        caps = self.capabilities_by_role.get(input.role, set())
        has_cap = input.capability_id in caps

        # Audit-first fail-closed: emit event BEFORE returning
        # If audit write fails, exception is raised (deny)
        try:
            self._emit_audit_event(
                event_type="capability_checked",
                role=input.role,
                capability_id=input.capability_id,
                result=has_cap,
                tenant_id=input.tenant_id
            )
        except Exception as audit_error:
            # Fail-closed: if audit fails, deny (log error but prioritize fail-closed)
            import logging
            logging.error(f"Audit write failed in CapabilitiesSkill: {audit_error}")
            raise SkillExecutionError(
                "Audit trail write failed; denying capability (fail-closed)"
            )

        return CapabilityCheckOutput(
            has_capability=has_cap,
            reason=self._explain_decision(input.role, input.capability_id, has_cap),
            capability_id=input.capability_id,
            role=input.role
        )

    def _load_manifest(self) -> dict:
        """
        Load capability definitions from manifest (immutable, frozen).

        Returns:
            dict mapping role → Set of capability_ids
        """
        return {
            "admin": {
                "read_audit_log",
                "write_config",
                "delete_user",
                "manage_roles",
                "migrate_data",
            },
            "operator": {
                "read_audit_log",
                "write_config",
                "manage_personas",
            },
            "user": {
                "read_context",
            },
        }

    def _explain_decision(self, role: str, cap_id: str, has_cap: bool) -> str:
        """Generate human-readable explanation of capability decision."""
        if has_cap:
            return f"allowed: {role} has {cap_id}"
        else:
            return f"denied: {role} lacks {cap_id}"

    def _emit_audit_event(self, **kwargs) -> None:
        """
        Emit immutable audit event to audit backend.

        Fail-closed: if write fails, raises exception (deny).

        Args:
            event_type: str (e.g., "capability_checked")
            role: str
            capability_id: str
            result: bool
            tenant_id: str

        Raises:
            Exception: if audit backend write fails (fail-closed)
        """
        from core.learning.audit_backend import audit_backend

        # Write to audit backend (fail-closed if error)
        audit_backend.write_event(
            tenant_id=kwargs["tenant_id"],
            event_type=kwargs["event_type"],
            payload={
                "role": kwargs["role"],
                "capability_id": kwargs["capability_id"],
                "result": kwargs["result"],
            }
        )


class SkillExecutionError(Exception):
    """Raised when Skill execution fails (fail-closed)."""
    pass
