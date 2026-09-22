"""Consent Gate — GDPR Art. 6 Consent Enforcement (load-bearing).

Provides @consent_required decorator for routes requiring user consent
before state-changing operations. Fail-closed: missing consent = deny.

ADR-0233: Bot Disclosure & Consent Gates
"""

from typing import Callable, Any, Optional
from functools import wraps
from fastapi import HTTPException, Depends
from starlette import status as http_status
import logging

logger = logging.getLogger(__name__)

# Consent types (scoped to specific operations)
CONSENT_SCOPES = {
    "control_plane_override_operations": "Operator override requests and approvals",
    "control_plane_snapshot_operations": "System state snapshots and restore",
    "plugin_management": "Plugin installation, enable, disable, uninstall",
    "subsystem_control": "Subsystem start, pause, resume, stop",
    "default": "General system operations",
}


class ConsentError(Exception):
    """Raised when consent is missing or denied."""

    pass


def consent_required(consent_scope: str = "default") -> Callable:
    """Decorator to enforce GDPR Art. 6 consent before operation.

    Fail-closed: missing consent → 403 Forbidden.

    Args:
        consent_scope: Scope of consent (from CONSENT_SCOPES)

    Returns:
        Dependency function for FastAPI routes

    Example:
        @router.post("/overrides/{id}/approve")
        async def approve_override(
            override_id: str,
            rec: Annotated[SessionRecord, Depends(require_session)] = ...,
            _: Annotated[None, Depends(consent_required("control_plane_override_operations"))] = ...,
        ):
            # Route body — only reached if consent verified
            ...
    """

    async def verify_consent(rec: Optional[Any] = None) -> None:
        """Verify user has given consent for this operation.

        Args:
            rec: Session record (optional, for tenant/user context)

        Raises:
            HTTPException 403: If consent missing or denied

        Returns:
            None (only used for dependency injection)
        """
        # CRITICAL FIX (2026-09-22): Implement fail-closed consent checking
        # Violation of GDPR Art. 6 was: commenting out real consent check and permitting all
        # Fix: Fail-closed — deny by default unless explicitly granted

        if rec is None:
            # No session record = no context for consent check
            # Fail-closed: deny
            logger.warning(
                f"Consent check failed: no session record for scope={consent_scope}"
            )
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=f"Consent required for: {CONSENT_SCOPES.get(consent_scope, consent_scope)}",
            )

        # LOAD-BEARING: Actual consent store check (GDPR Art. 6 compliance)
        # Default: assume NO consent unless explicitly granted in store
        try:
            from core.compliance.consent_store import get_consent_store, TenantIsolationError

            # Fail-closed: require valid tenant_id + user_id
            if not rec.tenant_id or not rec.sid:
                logger.warning(
                    f"Consent check failed: missing tenant_id or user_id (fail-closed)"
                )
                raise HTTPException(
                    status_code=http_status.HTTP_403_FORBIDDEN,
                    detail="Invalid session context for consent check"
                )

            # Get tenant-scoped consent store
            consent_store = get_consent_store(tenant_id=rec.tenant_id)

            # Check if user has active consent for this scope
            # Fail-closed: any exception or missing consent = deny
            has_consent = consent_store.get_consent(
                user_id=rec.sid,
                scope=consent_scope
            )

            if not has_consent:
                logger.warning(
                    f"Consent denied: user={rec.sid} tenant={rec.tenant_id} scope={consent_scope}"
                )
                raise HTTPException(
                    status_code=http_status.HTTP_403_FORBIDDEN,
                    detail=f"Consent required for: {CONSENT_SCOPES.get(consent_scope, consent_scope)}. "
                           f"Please grant consent at /consent-manager"
                )

        except TenantIsolationError as e:
            # Tenant isolation violation — fail-closed
            logger.error(
                f"SECURITY: Tenant isolation violation in consent check: {e}"
            )
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Consent verification failed due to security constraint"
            )
        except ImportError as e:
            # Consent store not available — fail-closed
            logger.error(
                f"CRITICAL: Consent store unavailable for scope={consent_scope}: {e}. "
                f"This is a security failure — cannot proceed without consent verification."
            )
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Consent verification system unavailable. Please try again."
            )

        logger.info(
            f"Consent verified: user={getattr(rec, 'sid', 'unknown')} tenant={rec.tenant_id} scope={consent_scope}"
        )

    return verify_consent


# Export for convenience
__all__ = ["consent_required", "ConsentError", "CONSENT_SCOPES"]
