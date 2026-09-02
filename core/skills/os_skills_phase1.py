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

    Replaces feature flag: spec.features.vibe_engineering_v0_2 / v0_3

    Input:
        user_id: str (optional)
        task_description: str
        priority_hint: int (1-10, user-suggested priority)
        time_budget_ms: int (how much time available)

    Output:
        vibe_score: float (0.0-1.0, engagement level)
        priority_adjustment: int (-5 to +5, relative priority change)
        reasoning: str (why this adjustment)
    """

    def __init__(self):
        metadata = SkillMetadata(
            id="os.vibe_engineering",
            name="Vibe Engineering",
            description="Apply vibe-informed heuristics for task prioritization",
            version="0.2.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["vibe", "prioritization", "heuristics"],
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute vibe analysis.

        Args:
            input: Dictionary with task context

        Returns:
            Dictionary with vibe_score, priority_adjustment, reasoning
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

        logger.info(
            f"VibeEngineering: vibe_score={vibe_score:.2f}, "
            f"adjustment={priority_adjustment}"
        )

        return {
            "vibe_score": vibe_score,
            "priority_adjustment": priority_adjustment,
            "reasoning": reasoning,
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

    skills_registry.register(router_skill)
    skills_registry.register(vibe_skill)
    skills_registry.register(context_skill)

    logger.info("Builtin OS Skills registered (Phase 1)")
