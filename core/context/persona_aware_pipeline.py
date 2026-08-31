"""Persona-Aware Context Pipeline — Integrates Context-Pipeline v2 with ADR-0302 Persona Capabilities.

k=2: Quality Gate Refinement with persona-scoped context filtering.
Different personas receive different tiers of context based on capability axis.

ADR-0399: Context-Pipeline v2 (Preservation+Additive)
ADR-0302: Persona Capability Axis (Deny-by-default, role/tier isolation)
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Literal, Any
from core.context import PipelineContext, PipelineAddition, QualityTier

# ADR-0302 persona capabilities are a deny-by-default SECURITY mechanism, so this
# import is deliberately unguarded. It previously pointed at `core.security.
# persona_model` -- a module that has never existed -- behind a `try/except
# ImportError` that substituted empty stub classes. The result was silent
# failure: `Persona` became a bare object, the `_DEFAULT_POLICIES` table below
# raised AttributeError on `Persona.CONSOLE_OPERATOR` at import time, and
# `core/context/__init__.py` swallowed THAT too, so the whole persona-aware
# pipeline was unreachable while every import still looked healthy.
# The canonical home of the model is `core.context_engineering`; a failure to
# import it must be loud, never a degraded no-gate pipeline.
from core.context_engineering import (
    Persona,
    Role,
    Tier,
    CapabilityRegistry,
)

logger = logging.getLogger(__name__)


class ContextVisibility(Enum):
    """What level of context a persona can access."""
    FULL = "full"               # All tiers (Tier 1/2/3)
    ENHANCED = "enhanced"       # Tier 1 + Tier 2 (blocking + optimization)
    MINIMAL = "minimal"         # Tier 1 only (blocking/safety)
    NONE = "none"              # No pipeline context


@dataclass
class PersonaContextPolicy:
    """Policy defining context visibility for a persona + role combination."""

    persona: Persona
    role: Role
    visibility: ContextVisibility
    max_memory_additions: int = 10
    require_relevance_clause: bool = True
    auto_tier_classification: bool = True

    def allows_tier(self, tier: QualityTier) -> bool:
        """Check if this persona can see a given tier."""
        if self.visibility == ContextVisibility.FULL:
            return True
        elif self.visibility == ContextVisibility.ENHANCED:
            return tier in [QualityTier.TIER_1_ALWAYS, QualityTier.TIER_2_FLAG]
        elif self.visibility == ContextVisibility.MINIMAL:
            return tier == QualityTier.TIER_1_ALWAYS
        else:
            return False


# Default policies per persona
DEFAULT_CONTEXT_POLICIES = {
    (Persona.CONSOLE_OPERATOR, Role.ADMIN): PersonaContextPolicy(
        persona=Persona.CONSOLE_OPERATOR,
        role=Role.ADMIN,
        visibility=ContextVisibility.FULL,
        max_memory_additions=20,
    ),
    (Persona.CONSOLE_OPERATOR, Role.OPERATOR): PersonaContextPolicy(
        persona=Persona.CONSOLE_OPERATOR,
        role=Role.OPERATOR,
        visibility=ContextVisibility.ENHANCED,
        max_memory_additions=15,
    ),
    (Persona.VOICE_USER, Role.USER): PersonaContextPolicy(
        persona=Persona.VOICE_USER,
        role=Role.USER,
        visibility=ContextVisibility.ENHANCED,
        max_memory_additions=10,
    ),
    (Persona.BRIDGE_ADAPTER, Role.USER): PersonaContextPolicy(
        persona=Persona.BRIDGE_ADAPTER,
        role=Role.USER,
        visibility=ContextVisibility.MINIMAL,
        max_memory_additions=5,
    ),
    (Persona.MCP_TOOL, Role.USER): PersonaContextPolicy(
        persona=Persona.MCP_TOOL,
        role=Role.USER,
        visibility=ContextVisibility.MINIMAL,
        max_memory_additions=3,
    ),
}


class PersonaAwarePipeline:
    """Context pipeline that filters additions based on persona capabilities.

    Implements k=2 quality gate with persona-aware visibility rules.
    Different personas see different levels of context based on their role + tier.
    """

    def __init__(
        self,
        pipeline: PipelineContext,
        persona: Persona,
        role: Role,
        capability_registry: Optional[CapabilityRegistry] = None,
    ):
        """Initialize persona-aware pipeline.

        Args:
            pipeline: Base PipelineContext to filter
            persona: Current persona (from ContextVar)
            role: Current role (from ContextVar)
            capability_registry: Optional capability registry for dynamic checks
        """
        self.pipeline = pipeline
        self.persona = persona
        self.role = role
        self.capability_registry = capability_registry
        self.policy = self._resolve_policy()

    def _resolve_policy(self) -> PersonaContextPolicy:
        """Resolve context policy for this persona + role combination."""
        key = (self.persona, self.role)

        # Check if custom policy exists
        if key in DEFAULT_CONTEXT_POLICIES:
            return DEFAULT_CONTEXT_POLICIES[key]

        # Fallback to minimal visibility
        logger.warning(
            f"No policy for persona={self.persona}, role={self.role}. "
            "Using MINIMAL visibility."
        )
        return PersonaContextPolicy(
            persona=self.persona,
            role=self.role,
            visibility=ContextVisibility.MINIMAL,
        )

    def get_filtered_additions(self) -> list[PipelineAddition]:
        """Get pipeline additions filtered by this persona's visibility policy.

        Returns only additions that this persona is allowed to see.
        """
        visible_additions = []

        for addition in self.pipeline.additions:
            # Filter 1: Tier-based visibility
            if not self.policy.allows_tier(addition.tier):
                logger.debug(
                    f"Persona {self.persona} cannot see {addition.tier} tier"
                )
                continue

            # Filter 2: Capability-based access (if registry provided)
            if self.capability_registry:
                required_cap = self._get_required_capability(addition)
                if required_cap:
                    has_cap = self.capability_registry.has_capability(
                        self.persona, self.role, required_cap
                    )
                    if not has_cap:
                        logger.debug(
                            f"Persona {self.persona} lacks capability {required_cap}"
                        )
                        continue

            visible_additions.append(addition)

        # Enforce max additions limit
        if len(visible_additions) > self.policy.max_memory_additions:
            logger.warning(
                f"Too many visible additions ({len(visible_additions)} > "
                f"{self.policy.max_memory_additions}). Truncating."
            )
            visible_additions = visible_additions[: self.policy.max_memory_additions]

        return visible_additions

    def _get_required_capability(self, addition: PipelineAddition) -> Optional[str]:
        """Determine if this addition requires a specific capability.

        Maps addition sources/tiers to capability requirements.
        """
        # Safety/audit additions require audit capability
        if "audit" in addition.source.lower() or "safety" in addition.source.lower():
            return "audit_access"

        # ADR/precedent additions require architecture access
        if addition.source.startswith("adr:"):
            return "architecture_access"

        # Memory additions require memory_access
        if addition.source.startswith("memory:"):
            return "memory_access"

        return None

    def get_filtered_system_prompt_section(self) -> str:
        """Render pipeline context as system prompt, filtered by persona visibility.

        Only includes additions this persona is allowed to see.
        """
        visible = self.get_filtered_additions()

        if not visible:
            return f"## PIPELINE CONTEXT [Supplementary]\n(none — restricted for {self.persona})"

        sections = [
            f"## PIPELINE CONTEXT [Supplementary — ADDS to Original]\n"
            f"[Visibility: {self.policy.visibility.value} for {self.persona}]"
        ]

        # Organize by tier
        for tier in [QualityTier.TIER_1_ALWAYS, QualityTier.TIER_2_FLAG, QualityTier.TIER_3_ASK]:
            tier_additions = [a for a in visible if a.tier == tier]
            if tier_additions:
                sections.append(f"\n### {tier.name.replace('_', ' ')}")
                for addition in tier_additions:
                    sections.append(addition.to_system_prompt_section())

        return "\n".join(sections)

    def summary(self) -> dict:
        """Summary of filtering applied."""
        visible = self.get_filtered_additions()
        total = len(self.pipeline.additions)

        return {
            "persona": self.persona.value,
            "role": self.role.value,
            "visibility_policy": self.policy.visibility.value,
            "total_additions": total,
            "visible_additions": len(visible),
            "filtered_out": total - len(visible),
            "max_allowed": self.policy.max_memory_additions,
            "truncated": len(visible) > self.policy.max_memory_additions,
        }


def create_persona_aware_pipeline(
    pipeline: PipelineContext,
    persona: Persona,
    role: Role,
) -> PersonaAwarePipeline:
    """Factory to create persona-aware pipeline wrapper.

    Args:
        pipeline: Base PipelineContext
        persona: Current persona
        role: Current role

    Returns:
        PersonaAwarePipeline with applied visibility filters
    """
    return PersonaAwarePipeline(pipeline, persona, role)
