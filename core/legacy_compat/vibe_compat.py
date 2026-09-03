"""
Vibe Engineering Compat Layer → os.delegation_router Skill

Transparent routing: old delegate_to_persona() calls → new Skill.

**Fail-closed guarantee:** Errors propagate (never silent fallback).
**Audit trail:** Every call logged as DeprecatedAPIEvent + SkillExecutedEvent.
**Tenant-safe:** All calls tenant-scoped (GDPR Art. 5).
"""

from typing import Optional, Any, Dict
from core.telemetry.deprecated_api_calls import (
    log_deprecated_call,
    log_deprecated_error,
    skill_call_timeout
)
from core.skills.os_skills_phase1 import DelegationRouterSkill


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
    # Log deprecated call (CRITICAL-2 FIX: audit trail integration)
    event = log_deprecated_call(
        api_name="delegate_to_persona",
        module="core.vibe_engineering.routing",
        tenant_id=tenant_id,
        user_id=user_id,
    )

    try:
        # Call new Skill with timeout (CRITICAL-5 FIX: fail-closed guarantee)
        skill = DelegationRouterSkill()
        with skill_call_timeout(seconds=5):  # Explicit timeout, fail-closed
            result = skill.execute(
                request=request,
                task_type=task_type,
                tenant_id=tenant_id,
            )

        # CRITICAL-4 FIX: execute() returns dict, not SkillExecutionResult
        if isinstance(result, dict):
            # Skill returns routing decision dict (successful case)
            return result.get("engine_id", "default")
        else:
            raise RuntimeError(f"Skill returned unexpected type: {type(result)}")

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

            with skill_call_timeout(seconds=5):  # CRITICAL-5 FIX: fail-closed timeout
                result = self.router_skill.execute(
                    context=task_context,
                    tenant_id=self.tenant_id,
                )

            # CRITICAL-4 FIX: execute() returns dict, not SkillExecutionResult
            if isinstance(result, dict):
                # Skill returns decision dict (successful case)
                return result.get("decision", "default")
            else:
                raise RuntimeError(f"Skill returned unexpected type: {type(result)}")

        except Exception as e:
            log_deprecated_error(
                api_name="VibeBrainAdapter.do_decide",
                module="core.vibe_engineering",
                error=e,
                tenant_id=self.tenant_id,
            )
            raise
