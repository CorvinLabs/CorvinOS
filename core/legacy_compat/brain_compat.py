"""
Brain Engineering Compat Layer → os.context_adapter Skill

Transparent routing: old get_session_context() calls → new Skill.

**Fail-closed guarantee:** Errors propagate (never silent fallback to old code).
**Audit trail:** Every call logged as DeprecatedAPIEvent + SkillExecutedEvent.
**Tenant-safe:** All calls tenant-scoped (GDPR Art. 5).
"""

from typing import Optional, Dict, Any
from core.telemetry.deprecated_api_calls import log_deprecated_call, log_deprecated_error
from core.skills.os_skills.context_adapter import ContextAdapterSkill


def get_session_context(
    task_id: str,
    tenant_id: str = "_default",
    user_id: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Deprecated: Use ContextAdapterSkill directly.

    Old API (Brain Engineering): Retrieve session context for a task.

    **Phase B behavior:**
    - Transparently calls os.context_adapter Skill
    - Returns same shape as old API (backward compatible)
    - Logs every call to audit trail

    **Phase C (week 8+):** This function will be deleted.

    Args:
        task_id: Task to retrieve context for
        tenant_id: Tenant scope (ADR-0007, GDPR Art. 5)
        user_id: Optional user ID (scrubbed, no PII in audit)
        **kwargs: Legacy compatibility args (ignored, logged)

    Returns:
        dict: Task context (same shape as old API)

    Raises:
        Exception: If Skill call fails (fail-closed: never silent fallback)
    """
    try:
        # Log deprecated call (telemetry + audit trail)
        event = log_deprecated_call(
            api_name="get_session_context",
            module="core.brain.conversation_recall",
            tenant_id=tenant_id,
            task_id=task_id,
            user_id=user_id,
        )

        # Call new Skill (Phase B: both old + new code present)
        skill = ContextAdapterSkill()
        result = skill.execute(task_id=task_id, tenant_id=tenant_id)

        if result.status != "success":
            raise RuntimeError(f"Skill failed: {result.error}")

        # Return in old shape (transparent to caller)
        return result.output

    except Exception as e:
        # Fail-closed: error propagates (never fallback to old code)
        log_deprecated_error(
            api_name="get_session_context",
            module="core.brain.conversation_recall",
            error=e,
            tenant_id=tenant_id,
            task_id=task_id,
        )
        raise  # Propagate error


def recall_recent_sessions(
    user_id: str,
    limit: int = 5,
    tenant_id: str = "_default",
) -> list:
    """
    Deprecated: Use ContextAdapterSkill directly.

    Old API (Brain Engineering): Recall recent sessions for a user.

    **Phase B behavior:**
    - Transparently calls os.context_adapter Skill
    - Returns same shape as old API
    - Logged to audit trail

    **Phase C (week 8+):** This function will be deleted.
    """
    try:
        event = log_deprecated_call(
            api_name="recall_recent_sessions",
            module="core.brain.conversation_recall",
            tenant_id=tenant_id,
            user_id=user_id,
        )

        skill = ContextAdapterSkill()
        result = skill.execute(
            user_id=user_id,
            limit=limit,
            tenant_id=tenant_id,
        )

        if result.status != "success":
            raise RuntimeError(f"Skill failed: {result.error}")

        return result.output

    except Exception as e:
        log_deprecated_error(
            api_name="recall_recent_sessions",
            module="core.brain.conversation_recall",
            error=e,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        raise
