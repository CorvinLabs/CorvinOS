"""Credential Rotation Daemon — Phase 1 Execution (ADR-0869).

NO ROTATION IMPLEMENTED (measured 2026-10-01, ADR-0891 amendment).
``rotate_credential()`` refuses with ``FAILED`` / ``rotation_not_implemented``
and ``verify_audit_trail()`` reports ``not_implemented``. Both used to report
success — "completed" for a rotation that changed nothing, "audit_trail_intact"
without reading the chain — the same false-success shape as the defused
``scripts/rotate_corvin_keys_gdpr.py``. ``bootstrap_pipeline`` only constructs
the daemon; nothing calls these methods in production.

Implements credential rotation with:
- Policy-driven scheduling
- Fail-closed error handling
- Immutable audit trail (hash-chained)
- Tenant isolation

Compliance:
  - GDPR Art. 30: Every rotation event audited
  - GDPR Art. 32: Fail-closed on error (never partial rotation)
  - Tenant-scoped: no cross-tenant leakage
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.security.credential_rotation_policy import (
    RotationAudit,
    RotationPolicy,
    RotationScheduler,
    RotationScheduleType,
    RotationStatus,
    _lom,
)

logger = logging.getLogger(__name__)

_LOM_FILE = "core/security/credential_rotation_daemon.py"


def _get_lom(func_name: str) -> str:
    """Get Line of Moral Responsibility."""
    return f"{_LOM_FILE}:{func_name}:L{sys._getframe(1).f_lineno}"


@dataclass
class RotationResult:
    """Result of a credential rotation attempt."""

    credential_id: str
    status: RotationStatus
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    audit_event: Optional[RotationAudit] = None


class RotationDaemon:
    """Credential rotation daemon — Phase 1 implementation.

    Responsibilities:
    - Apply rotation policies
    - Track rotation schedules
    - Execute rotations (Phase 2)
    - Emit audit events (immutable, hash-chained)
    - Fail-closed on error
    """

    def __init__(
        self,
        tenant_id: str = "_default",
        policy: Optional[RotationPolicy] = None,
        audit_backend: Optional[Any] = None,
    ):
        """Initialize rotation daemon.

        Args:
            tenant_id: Tenant scope
            policy: Rotation policy (optional)
            audit_backend: Audit trail backend with write_event method
        """
        self.tenant_id = tenant_id
        self.policy = policy
        self.audit_backend = audit_backend
        self.scheduler = RotationScheduler(tenant_id=tenant_id)
        self._last_audit_hash = ""

    def apply_policy(self, policy: RotationPolicy) -> None:
        """Apply rotation policy.

        Args:
            policy: RotationPolicy instance
        """
        self.policy = policy
        for credential_id in policy.credential_ids:
            self.scheduler.add_credential(credential_id)
        logger.info(
            f"Applied rotation policy with {len(policy.credential_ids)} credentials"
        )

    def get_due_credentials(self) -> list[str]:
        """Get credentials due for rotation.

        Returns:
            List of credential IDs due for rotation
        """
        return self.scheduler.get_due_credentials()

    def emit_audit_event(
        self,
        event_type: str,
        credential_id: str,
        status: RotationStatus,
        error_message: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> Optional[RotationAudit]:
        """Emit immutable audit event (hash-chained, GDPR Art. 30, 32).

        Args:
            event_type: Event type (rotation_started, rotation_completed, etc.)
            credential_id: Credential identifier
            status: Rotation status (pending, in_progress, completed, failed)
            error_message: Error message if failed
            duration_ms: Execution time in milliseconds

        Returns:
            RotationAudit event if emitted successfully, None otherwise
        """
        if not self.audit_backend:
            logger.warning(
                f"No audit backend configured; event {event_type} not emitted"
            )
            return None

        try:
            now = datetime.now(timezone.utc).isoformat()
            event = RotationAudit(
                event_type=event_type,
                tenant_id=self.tenant_id,
                credential_id=credential_id,
                timestamp=now,
                status=status.value,
                prev_hash=self._last_audit_hash,
                lom=_get_lom("emit_audit_event"),
                error_message=error_message,
                duration_ms=duration_ms,
            )

            # Compute hash for chain integrity
            event_hash = event.compute_hash()
            event_dict = event.to_dict()
            event_dict["hash"] = event_hash

            # Write to audit backend
            self.audit_backend.write_event(event_dict, lom=event.lom)
            self._last_audit_hash = event_hash

            logger.info(
                f"Audit event emitted: {event_type} for {credential_id} "
                f"(status={status.value})"
            )
            return event

        except Exception as e:
            logger.error(f"Failed to emit audit event {event_type}: {e}")
            return None

    def rotate_credential(
        self, credential_id: str
    ) -> RotationResult:
        """Refuse: this daemon has no rotation implementation.

        The credentials it schedules are third-party secrets (GitHub, Hetzner,
        Cloudflare, PyPI, OpenAI, ...). Rotating them needs the provider's own
        revocation + issuance, which happens in the provider's dashboard
        (ADR-0891 Phase 1). Reporting COMPLETED here would claim a rotation
        that did not happen, so the result is FAILED and the scheduler is not
        advanced.
        """
        logger.warning(
            "credential rotation refused for %s: rotation_not_implemented",
            credential_id,
        )
        return RotationResult(
            credential_id=credential_id,
            status=RotationStatus.FAILED,
            error_message="rotation_not_implemented",
            duration_ms=0,
        )

    def verify_audit_trail(self) -> tuple[bool, str]:
        """Report that no verification is performed here.

        The audit chain is verified at boot by the ADR-0232 tripwire
        (``tripwire.py::assert_all``); this daemon does not read it.
        """
        return False, "not_implemented"


def bootstrap_rotation_daemon_phase1(
    tenant_id: str = "_default",
    audit_backend: Optional[Any] = None,
    policy: Optional[RotationPolicy] = None,
) -> RotationDaemon:
    """Bootstrap rotation daemon for Phase 1 (inventory + audit baseline).

    Called from core.pipeline.bootstrap.bootstrap_pipeline.

    Args:
        tenant_id: Tenant scope
        audit_backend: Audit trail backend
        policy: Optional rotation policy

    Returns:
        RotationDaemon instance (initialized, not started)
    """
    try:
        daemon = RotationDaemon(
            tenant_id=tenant_id,
            audit_backend=audit_backend,
            policy=policy,
        )

        # Apply default policy if none provided
        if not policy:
            # Default: all credentials, monthly rotation
            policy = RotationPolicy(
                credential_ids=[
                    "GITHUB_TOKEN",
                    "HETZNER_API_TOKEN",
                    "HETZNER_ROOT_PASSWORT",
                    "CLOUDFLARE_ID",
                    "CLOUDFLARE_API_TOKEN",
                    "PYPI_TOKEN",
                    "RESEND_API_KEY",
                    "CORVIN_TTS_OPENAI_KEY",
                    "CORVIN_STT_OPENAI_KEY",
                    "OPENAI_API_KEY",
                    "GMAIL_APP_PASSWORD",
                    "OLLAMA_API_KEY",
                    "HETZNER_SSH_KEY_NAME",
                ],
                schedule_type=RotationScheduleType.MONTHLY,
                interval_days=90,
                backup_enabled=True,
                rollback_on_error=True,
            )
            daemon.apply_policy(policy)

        logger.info(f"Rotation daemon bootstrapped for tenant {tenant_id}")
        return daemon

    except Exception as e:
        logger.error(f"Rotation daemon bootstrap failed: {e}")
        # Return stub daemon (non-blocking failure)
        return RotationDaemon(tenant_id=tenant_id, audit_backend=None)
