"""
Context Engineering v1 Compat Layer → HybridContextModel Skill

Transparent routing: old create_snapshot_v1() calls → new Model/Skill.

**Fail-closed guarantee:** Errors propagate (never silent fallback).
**Audit trail:** Every call logged as DeprecatedAPIEvent + SkillExecutedEvent.
**Tenant-safe:** All calls tenant-scoped (GDPR Art. 5).
"""

from typing import Optional, Any, Dict
from core.telemetry.deprecated_api_calls import log_deprecated_call, log_deprecated_error
from core.skills.os_skills.context_adapter import HybridContextModel


def create_snapshot_v1(
    context: Dict[str, Any],
    tenant_id: str = "_default",
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deprecated: Use HybridContextModel directly.

    Old API (Context Engineering v1): Create snapshot of task context.

    **Phase B behavior:**
    - Transparently calls HybridContextModel
    - Returns same shape as old API (immutable snapshot)
    - Logged to audit trail

    **Phase C (week 8+):** This function will be deleted.

    Args:
        context: Task context to snapshot
        tenant_id: Tenant scope (ADR-0007, GDPR Art. 5)
        user_id: Optional user ID (scrubbed, no PII in audit)

    Returns:
        dict: Immutable snapshot (same shape as v1 API)

    Raises:
        Exception: If snapshot creation fails (fail-closed)
    """
    try:
        # Log deprecated call
        event = log_deprecated_call(
            api_name="create_snapshot_v1",
            module="core.context_engineering.snapshot",
            tenant_id=tenant_id,
            user_id=user_id,
        )

        # Call new model
        model = HybridContextModel()
        snapshot = model.create_snapshot(
            context=context,
            tenant_id=tenant_id,
        )

        # Return in old shape (transparent to caller)
        return snapshot

    except Exception as e:
        # Fail-closed: error propagates
        log_deprecated_error(
            api_name="create_snapshot_v1",
            module="core.context_engineering.snapshot",
            error=e,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        raise


def restore_snapshot_v1(
    snapshot: Dict[str, Any],
    tenant_id: str = "_default",
) -> Dict[str, Any]:
    """
    Deprecated: Use HybridContextModel directly.

    Old API (Context Engineering v1): Restore context from snapshot.

    **Phase B behavior:**
    - Transparently calls HybridContextModel.restore()
    - Returns same shape as old API
    - Logged to audit trail

    **Phase C (week 8+):** This function will be deleted.
    """
    try:
        event = log_deprecated_call(
            api_name="restore_snapshot_v1",
            module="core.context_engineering.snapshot",
            tenant_id=tenant_id,
        )

        model = HybridContextModel()
        context = model.restore_from_snapshot(
            snapshot=snapshot,
            tenant_id=tenant_id,
        )

        return context

    except Exception as e:
        log_deprecated_error(
            api_name="restore_snapshot_v1",
            module="core.context_engineering.snapshot",
            error=e,
            tenant_id=tenant_id,
        )
        raise
