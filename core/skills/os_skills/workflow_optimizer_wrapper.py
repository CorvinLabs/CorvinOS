"""os.workflow_optimizer — Skill 2.0 wrapper around the ADR-2030
WorkflowOptimizer (L22).

SHADOW MODE ONLY (ADR-0532 Phase 1 migration). This Skill never changes
which model actually serves a turn — the real OS-model routing decision
stays ``corvin_operator/bridges/shared/model_selector.py::resolve_os_model``
(ADR-0952, the single source of truth for both the console and the bridge).
This wrapper runs *after* that decision is made, purely to emit an audited
comparison so the Skill can accumulate a track record before it is ever
allowed to influence anything (same ADR-2092 G0 gate as os.flow_guard and
os.security_orchestrator: >=500 decisions + join >=0.95 before any
de-escalation path opens).

Do NOT wire this Skill's return value into the model-selection decision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin
from .workflow_optimizer.skill import ModelTier, RoutingInput, WorkflowOptimizer

logger = logging.getLogger(__name__)

# ADR-0952 model family -> this Skill's 3-tier ModelTier vocabulary. The
# production resolver returns a dated/provider-qualified id (e.g.
# "claude-haiku-4-5-20251001" or "anthropic/claude-opus-5"); this maps the
# family substring so the comparison has a stable target.
_FAMILY_TO_TIER = {
    "opus": ModelTier.OPUS_5,
    "sonnet": ModelTier.SONNET_5,
    "haiku": ModelTier.HAIKU_4_5,
}

_optimizer = WorkflowOptimizer()


def _production_model_to_tier(model_id: Optional[str]) -> Optional[ModelTier]:
    if not model_id:
        return None
    lowered = model_id.lower()
    for family, tier in _FAMILY_TO_TIER.items():
        if family in lowered:
            return tier
    return None


class WorkflowOptimizerSkill(Skill):
    """Skill 2.0 shadow wrapper for L22 task-complexity routing.

    Input:
        task_id: str
        task_content: str — the raw task text (same text passed to
            resolve_os_model's task_input, so the two see the same input).
        task_type: str | None
        tenant_id: str

    Output:
        {
            "model": str (ModelTier.value),
            "complexity": str (TaskComplexity.value),
            "confidence": float,
            "reasoning": str,
        }

    This output is NEVER consumed by the model-selection decision. It
    exists to be audited and compared against resolve_os_model()'s own
    choice so the Skill accrues a trust record (ADR-2092).
    """

    def __init__(self) -> None:
        metadata = SkillMetadata(
            id="os.workflow_optimizer",
            name="Workflow Optimizer (L22 shadow)",
            description="Shadow-mode task-complexity routing, compared against the live OS-model resolver",
            version="1.0.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["l22", "workflow", "shadow", "adr-2030", "adr-0532"],
            # CORE (default), not COMPLIANCE: shadow-only output, must stay
            # disableable while it accrues the ADR-2092 trust record.
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        task_id = input.get("task_id") or "unknown"
        task_content = input.get("task_content") or ""
        task_type = input.get("task_type")
        tenant_id = input.get("tenant_id") or "_default"

        if not task_content:
            return {"routed": False, "reason": "no_task_content"}

        routing_input = RoutingInput(
            task_id=task_id,
            task_content=task_content,
            task_type=task_type,
            tenant_id=tenant_id,
        )
        decision = _optimizer.route_task(routing_input)

        return {
            "routed": True,
            "model": decision.model.value,
            "complexity": decision.complexity.value,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
        }


def shadow_compare(
    *,
    task_input: Optional[str],
    tenant_id: str,
    production_model_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Run the Skill in shadow mode (via the audited registry) and return a
    comparison record.

    Routed through ``get_registry().execute()`` (not a direct
    ``WorkflowOptimizerSkill().execute()`` call) so the run is audited as
    ``skill.executed`` / ``skill_executed`` like every other OS-Skill
    invocation.

    Returns None when there is nothing to compare (no raw task text, or
    the production model couldn't be mapped to a tier). Never raises — a
    Skill failure must not affect the caller's model-selection decision.
    """
    if not task_input:
        return None
    production_tier = _production_model_to_tier(production_model_id)
    if production_tier is None:
        return None
    try:
        from ..skill_registry_phase1 import get_registry

        registry = get_registry()
        exec_result = registry.execute(
            "os.workflow_optimizer",
            {"task_id": "shadow", "task_content": task_input, "tenant_id": tenant_id},
            lom="corvin_operator/bridges/shared/adapter.py:_resolve_os_model_bundled",
            tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 — shadow path must never raise
        logger.warning("os.workflow_optimizer shadow execution failed: %r", exc)
        return None

    if exec_result.status != "success":
        return None

    result = exec_result.output or {}
    if not result.get("routed"):
        return None

    skill_tier = result.get("model")
    agree = skill_tier == production_tier.value
    return {
        "skill_model": skill_tier,
        "skill_complexity": result.get("complexity"),
        "production_model_id": production_model_id,
        "production_tier": production_tier.value,
        "agree": agree,
    }
