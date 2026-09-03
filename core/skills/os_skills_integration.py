"""OS-Skills Integration Layer — Wire Phase 1 Skills into Core Layers (L5, L10, etc).

This module integrates Phase 1 Skills with Corvin OS layer stack:
- L5 (Auto-routing): DelegationRouterSkill
- L10 (Context): ContextAdapterSkill
- Learning loop integration (ADR-0314)

Design:
- Singleton registry (init once at boot)
- Fallback to hardcoded defaults if Skill errors
- Audit-complete (every decision logged)
- Tenant-scoped (GDPR Art. 5, 6)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .skill_registry_phase1 import (
    SkillsRegistry,
    SkillExecutionResult,
    initialize_registry,
    get_registry,
)
from .os_skills_phase1 import (
    DelegationRouterSkill,
    ContextAdapterSkill,
    register_builtin_skills,
)

logger = logging.getLogger(__name__)

# Global singleton for Skills integration
_integration_instance: Optional[SkillsIntegrationLayer] = None


class SkillsIntegrationLayer:
    """Bridge between Corvin OS layers and Phase 1 Skills.

    Responsibilities:
    - Initialize registry + builtin Skills at boot
    - Provide L5/L10 entry points
    - Handle Skill errors gracefully (fallback to defaults)
    - Emit learning events (ADR-0314)
    - Tenant-scoped execution

    Compliance:
    - GDPR Art. 30: Audit-complete
    - GDPR Art. 32: Tenant isolation + PII scrubbing
    - EU AI Act Art. 50: LoM binding in all decisions
    """

    def __init__(self, audit_backend: Optional[Any] = None, tenant_id: str = "_default"):
        """Initialize Skills integration layer.

        Args:
            audit_backend: Audit trail backend (implements write_event)
            tenant_id: Tenant scope for execution
        """
        self.registry = initialize_registry(audit_backend, tenant_id)
        self.audit_backend = audit_backend
        self.tenant_id = tenant_id
        self._boot_skills()

    def _boot_skills(self) -> None:
        """Register builtin OS-Skills at boot (Phase 1)."""
        try:
            register_builtin_skills(self.registry)
            logger.info("Phase 1 builtin OS-Skills registered (7 total)")
        except Exception as e:
            logger.error(f"Failed to register builtin Skills: {e}")
            raise

    def route_task_l5(
        self,
        complexity: int,
        task_type: str,
        user_context: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """L5 Entry Point: Route task to appropriate engine.

        This is the new Skill-driven routing, replacing hardcoded delegation_policy.

        Args:
            complexity: 1-10 (1=trivial, 10=very complex)
            task_type: "analysis", "code", "chat", etc.
            user_context: Optional user/task context
            tenant_id: Override tenant scope

        Returns:
            {
                "engine": str (e.g., "claude-opus-5"),
                "confidence": float (0.0-1.0),
                "reasoning": str,
                "skill_executed": bool,
                "error": Optional[str]
            }

        Fallback (on Skill error): deterministic hardcoded routing
        """
        effective_tenant_id = tenant_id or self.tenant_id
        user_context = user_context or {}

        # Try Skill execution
        input_data = {
            "complexity": complexity,
            "task_type": task_type,
            "user_context": user_context,
        }
        result = self.registry.execute(
            "os.delegation_router",
            input_data,
            timeout_ms=5000,
            lom="os_skills_integration:route_task_l5:L120",
            tenant_id=effective_tenant_id,
        )

        if result.status == "success":
            return {
                **result.output,
                "skill_executed": True,
                "error": None,
            }

        # Fallback: hardcoded routing (deterministic)
        logger.warning(
            f"DelegationRouterSkill failed ({result.status}): {result.error_message}. "
            "Using fallback routing."
        )
        fallback = self._fallback_routing(complexity, task_type)
        return {
            **fallback,
            "skill_executed": False,
            "error": result.error_message,
        }

    def adapt_context_l10(
        self,
        complexity: int,
        task_type: str,
        task_description: str,
        priority_hint: int = 5,
        user_context: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """L10 Entry Point: Adapt context for task + agent (3-tier hybrid model, ADR-0555).

        Implements 3-tier hybrid context model (ADR-0555):
        - Base (Tier 1): immutable Phase 3 context (GDPR-locked)
        - Injected (Tier 2): learned layers (vibe, attention budget) — can be None if failed
        - Merged (Tier 3): fail-closed merge (uses base if injected failed)

        Args:
            complexity: Task complexity (1-10)
            task_type: Task type identifier
            task_description: Full task description
            priority_hint: User-suggested priority (1-10)
            user_context: Optional context (recent_decisions, user_profile, etc.)
            tenant_id: Override tenant scope

        Returns:
            {
                "base_tier": {...},           # Immutable Phase 3, always present
                "injected_tier": {...} | None,  # Learned layer, None if failed
                "merged_tier": {...},         # Safe merge (never partial)
                "routing_decision": {...},    # Delegation routing info
                "vibe_analysis": {...},       # Vibe engineering scores
                "skill_executed": bool,
                "error": Optional[str]
            }

        Compliance:
        - GDPR Art. 5: Immutable base tier (Phase 3 locked)
        - GDPR Art. 32: Fail-closed merge (never partial context)
        - ADR-0555: 3-tier hybrid context model
        """
        effective_tenant_id = tenant_id or self.tenant_id
        user_context = user_context or {}

        # Try Skill execution (ContextAdapterSkill now returns 3-tier structure)
        input_data = {
            "complexity": complexity,
            "task_type": task_type,
            "task_description": task_description,
            "priority_hint": priority_hint,
            "user_context": user_context,
        }
        result = self.registry.execute(
            "os.context_adapter",
            input_data,
            timeout_ms=5000,
            lom="os_skills_integration:adapt_context_l10:L175",
            tenant_id=effective_tenant_id,
        )

        if result.status == "success":
            # ContextAdapterSkill returns 3-tier structure directly
            return {
                "base_tier": result.output.get("base_tier"),
                "injected_tier": result.output.get("injected_tier"),
                "merged_tier": result.output.get("merged_tier"),
                "routing_decision": result.output.get("routing_decision"),
                "vibe_analysis": result.output.get("vibe_analysis"),
                "skill_executed": True,
                "error": None,
                "adr_0555_compliant": True,
            }

        # Fallback: build minimal immutable base context (fail-closed)
        logger.warning(
            f"ContextAdapterSkill failed ({result.status}): {result.error_message}. "
            "Using minimal base context (immutable) only (fail-closed, ADR-0555)."
        )
        fallback_base = {
            "tier_name": "base",
            "engine": "claude-sonnet-4",  # Safe default
            "priority": priority_hint,
            "context_fields": {
                "task_type": task_type,
                "priority_hint": priority_hint,
            },
            "metadata": {
                "origin": "fallback_base_only",
                "immutable": True,
                "adr_0555_failclosed": True,
            },
        }

        return {
            "base_tier": fallback_base,
            "injected_tier": None,  # Not available
            "merged_tier": fallback_base,  # Use base as merged (safe)
            "routing_decision": self._fallback_routing(complexity, task_type),
            "vibe_analysis": {"vibe_score": 0.5, "priority_adjustment": 0},
            "skill_executed": False,
            "error": result.error_message,
            "adr_0555_compliant": True,  # Still compliant (fail-closed)
        }

    @staticmethod
    def _fallback_routing(complexity: int, task_type: str) -> Dict[str, Any]:
        """Deterministic fallback routing (no Skill).

        Hardcoded heuristic, used when DelegationRouterSkill fails.

        Args:
            complexity: 1-10
            task_type: Task type

        Returns:
            Routing decision (same format as DelegationRouterSkill)
        """
        if complexity >= 8:
            engine = "claude-opus-5"
            confidence = 0.95
            reasoning = "Fallback: high complexity requires Opus"
        elif complexity >= 5:
            engine = "claude-sonnet-4"
            confidence = 0.85
            reasoning = "Fallback: medium-high complexity routed to Sonnet"
        else:
            engine = "claude-haiku-4"
            confidence = 0.90
            reasoning = "Fallback: low-medium complexity uses Haiku"

        # Task-type overrides
        if task_type == "code" and complexity < 7:
            engine = "claude-sonnet-4"
            confidence = 0.80
            reasoning = "Fallback: code tasks prefer Sonnet"

        return {
            "engine": engine,
            "confidence": confidence,
            "reasoning": reasoning,
        }


def initialize_integration(
    audit_backend: Optional[Any] = None, tenant_id: str = "_default"
) -> SkillsIntegrationLayer:
    """Initialize global Skills integration layer at boot."""
    global _integration_instance
    _integration_instance = SkillsIntegrationLayer(audit_backend, tenant_id)
    logger.info("OS-Skills integration layer initialized (Phase 1 L5 + L10 wiring)")
    return _integration_instance


def get_integration() -> SkillsIntegrationLayer:
    """Get global Skills integration layer (lazy init if needed)."""
    global _integration_instance
    if _integration_instance is None:
        initialize_integration()
    return _integration_instance


def route_task_l5(
    complexity: int,
    task_type: str,
    user_context: Optional[Dict[str, Any]] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """L5 Routing via global integration layer."""
    integration = get_integration()
    return integration.route_task_l5(complexity, task_type, user_context, tenant_id)


def adapt_context_l10(
    complexity: int,
    task_type: str,
    task_description: str,
    priority_hint: int = 5,
    user_context: Optional[Dict[str, Any]] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """L10 Context Adaptation via global integration layer."""
    integration = get_integration()
    return integration.adapt_context_l10(
        complexity, task_type, task_description, priority_hint, user_context, tenant_id
    )
