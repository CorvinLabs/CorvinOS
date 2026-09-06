"""OS-Skills for Phase 1: Replace feature flags with Skills.

This module implements builtin Corvin OS Skills that replace feature flags.

Skills implemented:
- os.delegation_router: Route tasks by complexity, engine type, etc.
- os.vibe_engineering: Apply vibe-informed heuristics
- os.context_adapter: 3-tier hybrid context (ADR-0555)

Compliance:
- GDPR Art. 30: All executions logged
- GDPR Art. 32: Immutable results
- EU AI Act Art. 50: LoM binding in every execution
- ADR-0555: Hybrid Context Model (3-tier)
- ADR-0544: Phase 1 big bang feature flags refactoring
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import logging
from dataclasses import dataclass

from .skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HybridContextTier:
    """Immutable context tier (one of 3: base, injected, merged)."""
    tier_name: str  # "base", "injected", "merged"
    engine: str
    priority: int
    context_fields: Dict[str, Any]
    metadata: Dict[str, Any]  # Source tracking, timestamp, etc.


class HybridContextModel:
    """3-tier context model (ADR-0555): Base → Injected → Merged.

    TIER 1 (Base): Immutable Phase 3 context
    - recent_decisions: List of recent decisions (read-only)
    - user_profile: User style/preferences (from Phase 3 learning)
    - success_rate: Small-n suppressed metrics
    - attention_budget: Remaining tokens

    TIER 2 (Injected): Learned layers (can fail without breaking)
    - vibe_score: Engagement level (from VibeEngineeringSkill)
    - priority_adjustment: Relative priority change
    - style_preference: Learned user style (if available)
    - attention_boost: Dynamically allocated

    TIER 3 (Merged): Fail-closed merge
    - If Tier 2 generation fails: use Tier 1 only
    - If merge logic fails: use Tier 1 only
    - Never return partially-merged context (safe by default)
    """

    @staticmethod
    def build_base_tier(
        task_type: str,
        priority_hint: int,
        user_context: Dict[str, Any],
    ) -> HybridContextTier:
        """Build Tier 1 (immutable base, Phase 3 origin).

        Args:
            task_type: Task type identifier
            priority_hint: User-suggested priority (1-10)
            user_context: Optional user/task context

        Returns:
            Immutable base tier (Phase 3 data)
        """
        return HybridContextTier(
            tier_name="base",
            engine="unknown",  # Will be filled by routing skill
            priority=priority_hint,
            context_fields={
                "task_type": task_type,
                "priority_hint": priority_hint,
                "recent_decisions": user_context.get("recent_decisions", []),
                "user_profile": user_context.get("user_profile", {}),
                "success_rate": user_context.get("success_rate"),
                "attention_budget": user_context.get("attention_budget", 100000),
            },
            metadata={
                "origin": "phase3_immutable",
                "immutable": True,
                "gdpr_compliant": True,
            },
        )

    @staticmethod
    def build_injected_tier(
        base_tier: HybridContextTier,
        vibe_score: float,
        priority_adjustment: int,
        user_style: Optional[str] = None,
    ) -> Optional[HybridContextTier]:
        """Build Tier 2 (learned/injected, can fail gracefully).

        Args:
            base_tier: Base tier (for context)
            vibe_score: Engagement level (0.0-1.0)
            priority_adjustment: Priority delta (-5 to +5)
            user_style: Learned user style (optional)

        Returns:
            Injected tier, or None if generation fails (fail-closed)
        """
        try:
            final_priority = base_tier.priority + priority_adjustment
            final_priority = max(1, min(10, final_priority))  # Clamp 1-10

            return HybridContextTier(
                tier_name="injected",
                engine=base_tier.engine,
                priority=final_priority,
                context_fields={
                    "vibe_score": vibe_score,
                    "priority_adjustment": priority_adjustment,
                    "user_style": user_style or "neutral",
                    "attention_boost": int(vibe_score * 10000),  # Boost tokens by vibe
                },
                metadata={
                    "origin": "learned_layers",
                    "immutable": False,
                    "vibe_driven": True,
                    "fallible": True,  # Can be dropped if generation fails
                },
            )
        except Exception as e:
            logger.warning(f"Injected tier generation failed: {e}. Using base tier only.")
            return None

    @staticmethod
    def merge_tiers_fail_closed(
        base_tier: HybridContextTier,
        injected_tier: Optional[HybridContextTier],
    ) -> HybridContextTier:
        """Merge tiers with fail-closed semantics (ADR-0555).

        Rules:
        - If injected_tier is None: return base_tier (safe default)
        - If merge logic fails: return base_tier (never partial)
        - Always prefer immutable base over risky learned layers

        Args:
            base_tier: Immutable base tier
            injected_tier: Optional learned tier

        Returns:
            Merged tier (frozen, audit-ready)
        """
        if injected_tier is None:
            # Injected layer failed: use base only
            logger.info("Injected tier unavailable. Using base context only (fail-closed).")
            return HybridContextTier(
                tier_name="merged",
                engine=base_tier.engine,
                priority=base_tier.priority,
                context_fields=base_tier.context_fields.copy(),
                metadata={
                    "origin": "base_only_failclosed",
                    "immutable": True,
                    "injected_used": False,
                },
            )

        try:
            # Merge: base + injected (injected overrides if present)
            merged_fields = {**base_tier.context_fields}
            merged_fields.update(injected_tier.context_fields)

            return HybridContextTier(
                tier_name="merged",
                engine=base_tier.engine,
                priority=injected_tier.priority,  # Use injected priority (more up-to-date)
                context_fields=merged_fields,
                metadata={
                    "origin": "merged_base_injected",
                    "immutable": True,  # Result is frozen after merge
                    "injected_used": True,
                    "merge_successful": True,
                },
            )
        except Exception as e:
            logger.error(f"Merge failed: {e}. Falling back to base tier (fail-closed).")
            return base_tier  # Ultimate fallback: immutable base


def _flag_state(flag_id: str, tenant_id: Optional[str], default: bool) -> tuple[bool, str]:
    """Resolve a per-tenant feature flag through ``corvin_core.feature_flags``.

    The Phase 1 refactor replaced ``is_enabled(<flag>, tenant_id)`` call sites with
    Skills that merely ECHOED their input (``{"enabled": True}`` → ``True``), which
    silently deleted the operator's per-tenant control (features.json overlay).
    Skills now consult the same registry the flags always lived in; only when the
    flags module is genuinely unavailable does the caller-supplied default apply.

    Returns:
        (enabled, source) — source is "feature_flags" or "default:<reason>"
    """
    try:
        from corvin_core.feature_flags import is_enabled  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 — stripped install without the console package
        return default, f"default:flags_unavailable:{type(exc).__name__}"
    try:
        return bool(is_enabled(flag_id, tenant_id or "_default")), "feature_flags"
    except Exception as exc:  # noqa: BLE001 — unknown flag / unreadable overlay → default
        return default, f"default:{type(exc).__name__}"


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

        # Learned config (ADR-0549 loop closure, 2026-09-06): the tenant's
        # SkillAdapter config is the OUTPUT of the feedback → hypothesis →
        # optimizer chain; reading it here is what makes an accepted hypothesis
        # change the next decision. ``confidence_threshold`` gates escalation:
        # a heuristic decision the tenant has learned to distrust (confidence
        # below the learned threshold) is escalated one engine tier. With the
        # default config (0.70) no heuristic branch is below threshold, so a
        # tenant that never gave feedback routes exactly as before.
        tenant_id = input.get("tenant_id")
        learned_version = None
        threshold = None
        if isinstance(tenant_id, str) and tenant_id:
            from .os_skills.skill_adapter import load_skill_config  # noqa: PLC0415

            try:
                cfg, learned_version = load_skill_config("os.delegation_router", tenant_id)
                threshold = cfg.confidence_threshold
            except Exception as exc:  # noqa: BLE001 — a config problem must not break routing
                logger.warning("DelegationRouter: learned config unreadable (%s)", type(exc).__name__)
            if threshold is not None and confidence < threshold:
                escalation = {"claude-haiku-4": "claude-sonnet-4", "claude-sonnet-4": "claude-opus-5"}
                if engine in escalation:
                    engine = escalation[engine]
                    reasoning = (
                        f"{reasoning}; escalated: confidence {confidence:.2f} below learned "
                        f"threshold {threshold:.2f} ({learned_version or 'default'})"
                    )

        logger.info(
            f"DelegationRouter: complexity={complexity}, task_type={task_type} → {engine}"
        )

        result: Dict[str, Any] = {
            "engine": engine,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        if threshold is not None:
            result["confidence_threshold"] = threshold
            result["learned_config_version"] = learned_version
        if input.get("shadow"):
            # Advisory execution (L5 shadow wiring): the bundled engine decided;
            # record both so the learning store can measure agreement.
            result["shadow"] = True
            result["bundled_engine"] = input.get("bundled_engine")
        return result


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

        # Active-state comes from the per-tenant flag (vibe_engineering_active),
        # not from the caller's input — the caller is asking, not telling.
        enabled, enabled_source = _flag_state(
            "vibe_engineering_active", input.get("tenant_id"), bool(input.get("enabled", True))
        )

        logger.info(
            f"VibeEngineering: vibe_score={vibe_score:.2f}, "
            f"adjustment={priority_adjustment}, enabled={enabled}"
        )

        return {
            "vibe_score": vibe_score,
            "priority_adjustment": priority_adjustment,
            "reasoning": reasoning,
            "enabled": enabled,
            "enabled_source": enabled_source,
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
            learn=False,  # flag/manifest lookup: audited, not a learning signal (F31)
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute health monitoring decision.

        Args:
            input: Dictionary (optional tenant_id)

        Returns:
            Dictionary with enabled status and reasoning
        """
        enabled, source = _flag_state(
            "plugin_health_monitoring", input.get("tenant_id"), bool(input.get("enabled", True))
        )

        logger.info(f"PluginHealthMonitoring: enabled={enabled} ({source})")

        return {
            "enabled": enabled,
            "reason": "Health monitoring active" if enabled else "Health monitoring disabled",
            "source": source,
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
            learn=False,  # flag/manifest lookup: audited, not a learning signal (F31)
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute headless mode decision.

        Args:
            input: Dictionary with optional mode

        Returns:
            Dictionary with headless status
        """
        headless, source = _flag_state(
            "headless_api_mode", input.get("tenant_id"), bool(input.get("headless_enabled", False))
        )
        mode = "headless" if headless else "console"

        logger.info(f"HeadlessMode: mode={mode} ({source})")

        return {
            "headless_enabled": headless,
            "mode": mode,
            "reason": f"Running in {mode} mode" if mode == "headless" else "Console UI enabled",
            "source": source,
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
        enabled, source = _flag_state(
            "plugin_builder_enabled", input.get("tenant_id"), bool(input.get("enabled", True))
        )

        logger.info(f"PluginBuilder: enabled={enabled} ({source})")

        return {
            "enabled": enabled,
            "reason": "Plugin builder available" if enabled else "Plugin builder disabled",
            "source": source,
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
            learn=False,  # flag/manifest lookup: audited, not a learning signal (F31)
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute capabilities lookup.

        Args:
            input: Dictionary with tenant_id and gated_flags

        Returns:
            Dictionary with flags mapping
        """
        gated_flags = list(input.get("gated_flags", []) or [])
        tenant_id = input.get("tenant_id", "_default")

        # Resolve every gated flag against the per-tenant flag registry. The
        # Phase 1 stub returned ``False`` for everything, which hid every
        # flag-gated console panel (Vibe Dashboard, Learning Hub, Marketplace …)
        # for every tenant, unconditionally. Unreadable → False (safe direction).
        flags: Dict[str, bool] = {}
        sources: Dict[str, str] = {}
        for flag in gated_flags:
            flags[flag], sources[flag] = _flag_state(str(flag), tenant_id, False)

        logger.info(f"Capabilities: tenant={tenant_id}, flags_checked={len(gated_flags)}")

        return {
            "flags": flags,
            "tenant_id": tenant_id,
            "sources": sources,
        }


class ContextAdapterSkill(Skill):
    """3-tier Hybrid Context Model (ADR-0555).

    Orchestrates DelegationRouter + VibeEngineering to build immutable,
    learned, and merged context tiers for agent decision-making.

    Input:
        complexity: int (1-10)
        task_type: str (e.g., "analysis", "code", "chat")
        task_description: str (full task text)
        priority_hint: int (1-10, user-suggested)
        user_context: dict (optional, includes recent_decisions, user_profile, etc.)

    Output:
        {
            "base_tier": HybridContextTier (immutable Phase 3),
            "injected_tier": HybridContextTier | None (learned layers, can be None if failed),
            "merged_tier": HybridContextTier (fail-closed merge result),
            "routing_decision": dict (from DelegationRouter),
            "vibe_analysis": dict (from VibeEngineering),
        }

    Compliance:
    - GDPR Art. 5: Immutable base tier (Phase 3)
    - GDPR Art. 32: Fail-closed merge (never partial context)
    - ADR-0555: 3-tier hybrid context model
    """

    def __init__(self, skills_registry: Optional[Any] = None):
        metadata = SkillMetadata(
            id="os.context_adapter",
            name="Context Adapter",
            description="3-tier Hybrid Context Model (base/injected/merged, fail-closed)",
            version="1.0.0",  # Major version bump for ADR-0555
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["context", "routing", "vibe", "hybrid-model", "adr-0555"],
        )
        super().__init__(metadata)
        self.skills_registry = skills_registry

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute 3-tier context adaptation (ADR-0555).

        Args:
            input: Dict with complexity, task_type, task_description, priority_hint, user_context

        Returns:
            Dict with base_tier, injected_tier, merged_tier (all immutable)
        """
        task_type = input.get("task_type", "general")
        task_description = input.get("task_description", "")
        priority_hint = input.get("priority_hint", 5)
        user_context = input.get("user_context", {})
        complexity = input.get("complexity", 5)

        # Get routing + vibe decisions (external Skills)
        router_skill = DelegationRouterSkill()
        vibe_skill = VibeEngineeringSkill()

        routing_decision = router_skill.execute(input)
        vibe_analysis = vibe_skill.execute(input)

        # TIER 1: Build immutable base tier (Phase 3, GDPR-locked)
        base_tier = HybridContextModel.build_base_tier(
            task_type=task_type,
            priority_hint=priority_hint,
            user_context=user_context,
        )
        # Attach engine from routing decision
        base_tier = HybridContextTier(
            tier_name=base_tier.tier_name,
            engine=routing_decision["engine"],
            priority=base_tier.priority,
            context_fields=base_tier.context_fields,
            metadata=base_tier.metadata,
        )

        # TIER 2: Build injected tier (learned, can fail gracefully)
        injected_tier = HybridContextModel.build_injected_tier(
            base_tier=base_tier,
            vibe_score=vibe_analysis.get("vibe_score", 0.5),
            priority_adjustment=vibe_analysis.get("priority_adjustment", 0),
            user_style=user_context.get("user_style"),
        )

        # TIER 3: Merge with fail-closed semantics (never partial)
        merged_tier = HybridContextModel.merge_tiers_fail_closed(
            base_tier=base_tier,
            injected_tier=injected_tier,
        )

        logger.info(
            f"ContextAdapter: base={base_tier.engine}, "
            f"injected={'present' if injected_tier else 'failed'}, "
            f"merged_priority={merged_tier.priority} (ADR-0555)"
        )

        return {
            "base_tier": {
                "tier_name": base_tier.tier_name,
                "engine": base_tier.engine,
                "priority": base_tier.priority,
                "context_fields": base_tier.context_fields,
                "metadata": base_tier.metadata,
            },
            "injected_tier": (
                {
                    "tier_name": injected_tier.tier_name,
                    "engine": injected_tier.engine,
                    "priority": injected_tier.priority,
                    "context_fields": injected_tier.context_fields,
                    "metadata": injected_tier.metadata,
                }
                if injected_tier
                else None
            ),
            "merged_tier": {
                "tier_name": merged_tier.tier_name,
                "engine": merged_tier.engine,
                "priority": merged_tier.priority,
                "context_fields": merged_tier.context_fields,
                "metadata": merged_tier.metadata,
            },
            "routing_decision": routing_decision,
            "vibe_analysis": vibe_analysis,
        }


# Factory function to create and register all builtin Skills
def register_builtin_skills(skills_registry: Any) -> None:
    """Register all builtin OS Skills with the registry.

    Args:
        skills_registry: SkillsRegistry instance
    """
    builtin = [
        DelegationRouterSkill(),
        VibeEngineeringSkill(),
        ContextAdapterSkill(skills_registry),
        PluginHealthMonitoringSkill(),
        HeadlessModeSkill(),
        PluginBuilderSkill(),
        CapabilitiesSkill(),
    ]

    # Idempotent: a second boot in the same process (tests, hot reload) must not
    # raise on the already-registered ids.
    registered = 0
    for skill in builtin:
        if skills_registry.get(skill.metadata.id) is None:
            skills_registry.register(skill)
            registered += 1

    logger.info(
        "Builtin OS Skills registered (%d new, %d total)", registered, len(BUILTIN_SKILL_IDS)
    )


BUILTIN_SKILL_IDS: tuple[str, ...] = (
    "os.delegation_router",
    "os.vibe_engineering",
    "os.context_adapter",
    "os.plugin_health_monitoring",
    "os.headless_mode",
    "os.plugin_builder",
    "os.capabilities",
)
