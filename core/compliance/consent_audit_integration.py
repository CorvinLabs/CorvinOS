"""
Consent + Audit Integration — Emit audit events on all consent operations

GDPR Art. 30 compliance: Every consent grant/revoke must be recorded in the
immutable audit trail.

Wraps ConsentStore operations to emit audit events via audit_backend.
"""

import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def emit_consent_audit_event(
    event_type: str,
    user_id: str,
    scope: str,
    tenant_id: str,
    details: Optional[dict] = None
) -> None:
    """
    Emit an immutable audit event for consent operations (GDPR Art. 30).

    Args:
        event_type: 'consent_granted', 'consent_revoked', or 'consent_checked'
        user_id: User who consented
        scope: Consent scope (skill_generation, telemetry_ping, etc.)
        tenant_id: Tenant (must match, fail-closed)
        details: Optional extra details (TTL, expires_at, etc.)
    """
    try:
        from core.compliance.audit_backend import log_audit_event

        if not tenant_id or not user_id:
            logger.error(f"Consent audit emit failed: missing tenant_id or user_id (fail-closed)")
            return  # Fail-silent on audit (operation still proceeds, but logged)

        # Build audit event payload
        payload = {
            "event_type": event_type,
            "user_id": user_id,
            "scope": scope,
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Add optional details (TTL, expires_at, etc.)
        if details:
            payload.update(details)

        # Emit to audit backend (hash-chained, immutable)
        log_audit_event(
            event_type=event_type,
            payload=payload,
            tenant_id=tenant_id
        )

        logger.info(f"Consent audit emitted: event_type={event_type}, user={user_id}, scope={scope}, tenant={tenant_id}")

    except Exception as e:
        logger.error(f"Failed to emit consent audit event: {e} (operation continues, but audit trail incomplete)")


def grant_consent_with_audit(
    tenant_id: str,
    user_id: str,
    scope: str,
    ttl_days: int = 90
) -> dict:
    """
    Grant consent and emit audit event (GDPR Art. 6, 7, 30 compliant).

    Args:
        tenant_id: Tenant identifier
        user_id: User identifier
        scope: Consent scope
        ttl_days: Time-to-live (default 90 per GDPR)

    Returns:
        dict: Consent record with timestamps
    """
    try:
        from core.compliance.consent_store import get_consent_store

        # Fail-closed: require valid parameters
        if not tenant_id or not user_id or not scope:
            logger.error("grant_consent_with_audit: invalid parameters (fail-closed)")
            return {"error": "Invalid parameters"}

        # Grant consent via store
        store = get_consent_store(tenant_id=tenant_id)
        record = store.grant_consent(user_id=user_id, scope=scope, ttl_days=ttl_days)

        # Emit audit event
        emit_consent_audit_event(
            event_type="consent_granted",
            user_id=user_id,
            scope=scope,
            tenant_id=tenant_id,
            details={
                "ttl_days": ttl_days,
                "expires_at": record.expires_at,
                "granted_at": record.granted_at
            }
        )

        logger.info(f"Consent granted + audited: user={user_id}, scope={scope}, tenant={tenant_id}")
        return record.to_dict()

    except Exception as e:
        logger.error(f"Error in grant_consent_with_audit: {e}")
        raise


def revoke_consent_with_audit(
    tenant_id: str,
    user_id: str,
    scope: str
) -> Optional[dict]:
    """
    Revoke consent and emit audit event (GDPR Art. 7 right to withdraw).

    Args:
        tenant_id: Tenant identifier
        user_id: User identifier
        scope: Consent scope

    Returns:
        dict: Revoked consent record, or None if no record existed
    """
    try:
        from core.compliance.consent_store import get_consent_store

        # Fail-closed: require valid parameters
        if not tenant_id or not user_id or not scope:
            logger.error("revoke_consent_with_audit: invalid parameters (fail-closed)")
            return None

        # Revoke consent via store
        store = get_consent_store(tenant_id=tenant_id)
        record = store.revoke_consent(user_id=user_id, scope=scope)

        if record is None:
            logger.info(f"Revoke consent: no active record found (user={user_id}, scope={scope})")
            return None

        # Emit audit event
        emit_consent_audit_event(
            event_type="consent_revoked",
            user_id=user_id,
            scope=scope,
            tenant_id=tenant_id,
            details={
                "revoked_at": record.revoked_at,
                "was_active_until": record.expires_at
            }
        )

        logger.info(f"Consent revoked + audited: user={user_id}, scope={scope}, tenant={tenant_id}")
        return record.to_dict()

    except Exception as e:
        logger.error(f"Error in revoke_consent_with_audit: {e}")
        raise


def check_consent_with_audit(
    tenant_id: str,
    user_id: str,
    scope: str
) -> bool:
    """
    Check if user has consent and emit audit event.

    Args:
        tenant_id: Tenant identifier
        user_id: User identifier
        scope: Consent scope

    Returns:
        True if valid, active consent exists; False otherwise
    """
    try:
        from core.compliance.consent_store import get_consent_store

        # Fail-closed: require valid parameters
        if not tenant_id or not user_id or not scope:
            logger.warning("check_consent_with_audit: invalid parameters (fail-closed)")
            emit_consent_audit_event(
                event_type="consent_checked",
                user_id=user_id or "unknown",
                scope=scope or "unknown",
                tenant_id=tenant_id or "unknown",
                details={"result": "DENIED", "reason": "invalid_parameters"}
            )
            return False

        # Check consent via store
        store = get_consent_store(tenant_id=tenant_id)
        has_consent = store.get_consent(user_id=user_id, scope=scope)

        # Emit audit event (including check result)
        emit_consent_audit_event(
            event_type="consent_checked",
            user_id=user_id,
            scope=scope,
            tenant_id=tenant_id,
            details={"result": "GRANTED" if has_consent else "DENIED"}
        )

        logger.info(f"Consent checked: user={user_id}, scope={scope}, result={'GRANTED' if has_consent else 'DENIED'}")
        return has_consent

    except Exception as e:
        logger.error(f"Error in check_consent_with_audit: {e}")
        emit_consent_audit_event(
            event_type="consent_checked",
            user_id=user_id or "unknown",
            scope=scope or "unknown",
            tenant_id=tenant_id or "unknown",
            details={"result": "DENIED", "reason": "check_error", "error": str(e)}
        )
        return False
