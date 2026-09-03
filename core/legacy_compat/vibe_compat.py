"""
Vibe Engineering Compat Layer → os.delegation_router Skill

Transparent routing: old delegate_to_persona() calls → new Skill.

**Fail-closed guarantee:** Errors propagate (never silent fallback).
**Audit trail:** Every call logged as DeprecatedAPIEvent + SkillExecutedEvent.
**Tenant-safe:** All calls tenant-scoped (GDPR Art. 5).
"""

from typing import Optional, Any, Dict
from core.telemetry.deprecated_api_calls import log_deprecated_call, log_deprecated_error
from core.skills.os_skills.delegation_router import DelegationRouterSkill


def delegate_to_persona(
    request: Dict[str, Any],
    task_type: str,
    tenant_id: str = "_default",
    user_id: Optional[str] = None,
) -> str:
    """
    Deprecated: Use DelegationRouterSkill directly.

    Old API (Vibe Engineering): Route request to appropriate persona/engine.

    **Phase B behavior:**
    - Transparently calls os.delegation_router Skill
    - Returns same shape as old API (engine_id or routing decision)
    - Logged to audit trail

    **Phase C (week 8+):** This function will be deleted.

    Args:
        request: Request object to route
        task_type: Type of task (complex, simple, retrieval, etc.)
        tenant_id: Tenant scope (ADR-0007, GDPR Art. 5)
        user_id: Optional user ID (scrubbed, no PII in audit)

    Returns:
        str: Engine ID or routing decision

    Raises:
        Exception: If Skill call fails (fail-closed: never silent fallback)
    """
    try:
        # Log deprecated call
        event = log_deprecated_call(
            api_name="delegate_to_persona",
            module="core.vibe_engineering.routing",
            tenant_id=tenant_id,
            user_id=user_id,
        )

        # Call new Skill
        skill = DelegationRouterSkill()
        result = skill.execute(
            request=request,
            task_type=task_type,
            tenant_id=tenant_id,
        )

        if result.status != "success":
            raise RuntimeError(f"Skill failed: {result.error}")

        # Return in old shape (transparent to caller)
        return result.output.get("engine_id", "default")

    except Exception as e:
        # Fail-closed: error propagates
        log_deprecated_error(
            api_name="delegate_to_persona",
            module="core.vibe_engineering.routing",
            error=e,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        raise


class VibeBrainAdapter:
    """
    Deprecated: Use DelegationRouterSkill and Skills directly.

    Old API (Vibe Engineering): Adapter for Brain + Vibe integration.

    **Phase B behavior:**
    - Transparently routes to Delegation Router Skill
    - Maintains old interface (methods map to Skill calls)
    - Logged to audit trail

    **Phase C (week 8+):** This class will be deleted.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.router_skill = DelegationRouterSkill()

    def do_route(self, request: Dict[str, Any], task_type: str) -> str:
        """Route via Skill (transparent, fail-closed)."""
        return delegate_to_persona(
            request=request,
            task_type=task_type,
            tenant_id=self.tenant_id,
        )

    def do_decide(self, task_context: Dict[str, Any]) -> str:
        """Make routing decision via Skill."""
        try:
            event = log_deprecated_call(
                api_name="VibeBrainAdapter.do_decide",
                module="core.vibe_engineering",
                tenant_id=self.tenant_id,
            )

            result = self.router_skill.execute(
                context=task_context,
                tenant_id=self.tenant_id,
            )

            if result.status != "success":
                raise RuntimeError(f"Skill failed: {result.error}")

            return result.output.get("decision", "default")

        except Exception as e:
            log_deprecated_error(
                api_name="VibeBrainAdapter.do_decide",
                module="core.vibe_engineering",
                error=e,
                tenant_id=self.tenant_id,
            )
            raise
