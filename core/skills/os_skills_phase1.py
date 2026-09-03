"""OS-Skills for Phase 1: Replace feature flags with Skills.

This module implements builtin Corvin OS Skills that replace feature flags.

Skills implemented:
- os.delegation_router: Route tasks by complexity, engine type, etc.
- os.vibe_engineering: Apply vibe-informed heuristics
- os.context_adapter: Compose routing + vibe info

Compliance:
- GDPR Art. 30: All executions logged
- GDPR Art. 32: Immutable results
- EU AI Act Art. 50: LoM binding in every execution
- ADR-0544: Phase 1 big bang feature flags refactoring
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from .skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin

logger = logging.getLogger(__name__)


class DelegationRouterSkill(Skill):
    """Route tasks to appropriate engine based on complexity/type.

    Replaces feature flag: spec.features.vibe_engineering_v0_2

    Input:
        complexity: int (1-10, where 10 is most complex)
        task_type: str (e.g., "analysis", "code", "chat")
        user_context: dict (optional user/task context)

    Output:
        engine: str (e.g., "claude-opus-5", "claude-sonnet-4")
        confidence: float (0.0-1.0, confidence in decision)
        reasoning: str (why this engine was chosen)
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.delegation_router",
            name="Delegation Router",
            description="Route tasks to appropriate Claude engine based on complexity and type",
            version="0.1.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["routing", "delegation", "os-core"],
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute routing decision.

        Args:
            input: Dictionary with complexity, task_type, user_context

        Returns:
            Dictionary with engine, confidence, reasoning
        """
        complexity = input.get("complexity", 5)  # Default: medium complexity
        task_type = input.get("task_type", "general")
        user_context = input.get("user_context", {})

        # Simple heuristic: complexity → engine
        # Real version would use ML model or more sophisticated logic
        if complexity >= 8:
            engine = "claude-opus-5"
            confidence = 0.95
            reasoning = "High complexity task requires most capable engine"
        elif complexity >= 5:
            engine = "claude-sonnet-4"
            confidence = 0.85
            reasoning = "Medium-high complexity routed to Sonnet"
        else:
            engine = "claude-haiku-4"
            confidence = 0.90
            reasoning = "Low-medium complexity uses efficient Haiku engine"

        # Task-type adjustments
        if task_type == "code" and complexity < 7:
            engine = "claude-sonnet-4"
            confidence = 0.80
            reasoning = "Code tasks prefer Sonnet over Haiku"

        logger.info(
            f"DelegationRouter: complexity={complexity}, task_type={task_type} → {engine}"
        )

        return {
            "engine": engine,
            "confidence": confidence,
            "reasoning": reasoning,
        }


class VibeEngineeringSkill(Skill):
    """Apply vibe-informed heuristics to task prioritization.

    Replaces feature flag: spec.features.vibe_engineering_v0_2 / v0_3 and vibe_engineering_active

    Input:
        user_id: str (optional)
        task_description: str
        priority_hint: int (1-10, user-suggested priority)
        time_budget_ms: int (how much time available)
        tenant_id: str (optional)

    Output:
        vibe_score: float (0.0-1.0, engagement level)
        priority_adjustment: int (-5 to +5, relative priority change)
        reasoning: str (why this adjustment)
        enabled: bool (whether vibe engineering is active for this tenant)
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.vibe_engineering",
            name="Vibe Engineering",
            description="Apply vibe-informed heuristics for task prioritization and check active status",
            version="0.2.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["vibe", "prioritization", "heuristics"],
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute vibe analysis.

        Args:
            input: Dictionary with task context and optional tenant_id

        Returns:
            Dictionary with vibe_score, priority_adjustment, reasoning, and enabled status
        """
        task_description = input.get("task_description", "")
        priority_hint = input.get("priority_hint", 5)
        time_budget_ms = input.get("time_budget_ms", 60000)

        # Vibe score: engagement level (higher = more engaging)
        # Simple heuristic: longer descriptions suggest more engaged user
        description_length = len(task_description.split())
        vibe_score = min(1.0, description_length / 100.0)  # Saturate at 100 words

        # Priority adjustment based on vibe + time budget
        priority_adjustment = 0
        reasoning = "No adjustment"

        if vibe_score > 0.7:
            priority_adjustment = +2
            reasoning = "High engagement detected (long description)"
        elif vibe_score < 0.3:
            priority_adjustment = -2
            reasoning = "Low engagement (short description)"

        # Adjust for tight time budget
        if time_budget_ms < 10000:
            priority_adjustment -= 1
            reasoning = f"{reasoning}; tight time budget"

        # Vibe engineering is enabled by default
        enabled = input.get("enabled", True)

        logger.info(
            f"VibeEngineering: vibe_score={vibe_score:.2f}, "
            f"adjustment={priority_adjustment}, enabled={enabled}"
        )

        return {
            "vibe_score": vibe_score,
            "priority_adjustment": priority_adjustment,
            "reasoning": reasoning,
            "enabled": enabled,
        }


class PluginHealthMonitoringSkill(Skill):
    """Control plugin health monitoring system.

    Replaces feature flag: plugin_health_monitoring

    Input:
        tenant_id: str (optional, for tenant-scoped control)

    Output:
        enabled: bool (whether health monitoring is active)
        reason: str (why it's enabled/disabled)
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.plugin_health_monitoring",
            name="Plugin Health Monitoring",
            description="Control plugin health monitoring and self-healing capabilities",
            version="0.1.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["plugins", "health", "monitoring"],
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute health monitoring decision.

        Args:
            input: Dictionary (optional tenant_id)

        Returns:
            Dictionary with enabled status and reasoning
        """
        # By default, health monitoring is enabled for production deployments
        enabled = input.get("enabled", True)

        logger.info(f"PluginHealthMonitoring: enabled={enabled}")

        return {
            "enabled": enabled,
            "reason": "Health monitoring active" if enabled else "Health monitoring disabled",
        }


class HeadlessModeSkill(Skill):
    """Control headless API-only mode.

    Replaces feature flag: headless_api_mode

    Input:
        tenant_id: str (optional)
        mode: str (optional, "headless" or "console")

    Output:
        headless_enabled: bool
        mode: str
        reason: str
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.headless_mode",
            name="Headless Mode",
            description="Control whether console serves API-only (headless) or with UI",
            version="0.1.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["console", "deployment", "api"],
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute headless mode decision.

        Args:
            input: Dictionary with optional mode

        Returns:
            Dictionary with headless status
        """
        headless = input.get("headless_enabled", False)
        mode = "headless" if headless else "console"

        logger.info(f"HeadlessMode: mode={mode}")

        return {
            "headless_enabled": headless,
            "mode": mode,
            "reason": f"Running in {mode} mode" if mode == "headless" else "Console UI enabled",
        }


class PluginBuilderSkill(Skill):
    """Control plugin builder slash command availability.

    Replaces feature flag: plugin_builder_enabled

    Input:
        tenant_id: str (optional)
        user_id: str (optional)

    Output:
        enabled: bool
        reason: str
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.plugin_builder",
            name="Plugin Builder",
            description="Control availability of /build slash command for plugin creation",
            version="0.1.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["plugins", "builder", "features"],
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute plugin builder availability decision.

        Args:
            input: Dictionary with optional tenant_id

        Returns:
            Dictionary with enabled status
        """
        enabled = input.get("enabled", True)

        logger.info(f"PluginBuilder: enabled={enabled}")

        return {
            "enabled": enabled,
            "reason": "Plugin builder available" if enabled else "Plugin builder disabled",
        }


class CapabilitiesSkill(Skill):
    """Return capability manifest with feature flag status.

    Replaces feature flag list lookup in capabilities endpoint

    Input:
        tenant_id: str
        gated_flags: list[str] (flags to check)

    Output:
        flags: dict[str, bool] (flag_name → enabled)
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.capabilities",
            name="Capabilities Manifest",
            description="Return capability manifest with gated feature status",
            version="0.1.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["capabilities", "manifest", "api"],
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute capabilities lookup.

        Args:
            input: Dictionary with tenant_id and gated_flags

        Returns:
            Dictionary with flags mapping
        """
        gated_flags = input.get("gated_flags", [])
        tenant_id = input.get("tenant_id", "_default")

        # For Phase 1, all gated flags are disabled by default
        # Real implementation would query actual flag states
        flags = {flag: False for flag in gated_flags}

        logger.info(f"Capabilities: tenant={tenant_id}, flags_checked={len(gated_flags)}")

        return {
            "flags": flags,
            "tenant_id": tenant_id,
        }


class ContextAdapterSkill(Skill):
    """Compose delegation router + vibe engineering decisions.

    This Skill orchestrates multiple other Skills to provide a comprehensive
    decision on task routing + prioritization.

    Input:
        complexity: int
        task_type: str
        task_description: str
        priority_hint: int
        user_context: dict

    Output:
        routing_decision: dict (from DelegationRouter)
        vibe_analysis: dict (from VibeEngineering)
        final_routing: dict (combined decision)
    """

    def __init__(self, skills_registry: Optional[Any] = None):
        metadata = SkillMetadata(
            id="os.context_adapter",
            name="Context Adapter",
            description="Compose routing + vibe decisions for comprehensive task analysis",
            version="0.1.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["routing", "vibe", "composition"],
        )
        super().__init__(metadata)
        self.skills_registry = skills_registry

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute context adaptation.

        Args:
            input: Comprehensive task input

        Returns:
            Dictionary with routing + vibe decisions
        """
        # For Phase 1, we implement this as a direct composition
        # In future phases, this would use the Skills registry to call
        # DelegationRouter + VibeEngineering Skills

        router_skill = DelegationRouterSkill()
        vibe_skill = VibeEngineeringSkill()

        routing_decision = router_skill.execute(input)
        vibe_analysis = vibe_skill.execute(input)

        # Combine decisions
        final_routing = {
            "engine": routing_decision["engine"],
            "confidence": routing_decision["confidence"],
            "vibe_priority_boost": vibe_analysis["priority_adjustment"],
            "final_priority": input.get("priority_hint", 5) + vibe_analysis[
                "priority_adjustment"
            ],
        }

        logger.info(
            f"ContextAdapter: routing={final_routing['engine']}, "
            f"priority={final_routing['final_priority']}"
        )

        return {
            "routing_decision": routing_decision,
            "vibe_analysis": vibe_analysis,
            "final_routing": final_routing,
        }


# Factory function to create and register all builtin Skills
def register_builtin_skills(skills_registry: Any) -> None:
    """Register all builtin OS Skills with the registry.

    Args:
        skills_registry: SkillsRegistry instance
    """
    router_skill = DelegationRouterSkill()
    vibe_skill = VibeEngineeringSkill()
    context_skill = ContextAdapterSkill(skills_registry)
    health_monitoring_skill = PluginHealthMonitoringSkill()
    headless_skill = HeadlessModeSkill()
    plugin_builder_skill = PluginBuilderSkill()
    capabilities_skill = CapabilitiesSkill()

    skills_registry.register(router_skill)
    skills_registry.register(vibe_skill)
    skills_registry.register(context_skill)
    skills_registry.register(health_monitoring_skill)
    skills_registry.register(headless_skill)
    skills_registry.register(plugin_builder_skill)
    skills_registry.register(capabilities_skill)

    logger.info("Builtin OS Skills registered (Phase 1 k=2-5 refactoring: 7 total Skills)")
