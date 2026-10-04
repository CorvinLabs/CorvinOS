"""os.flow_guard — Skill 2.0 wrapper around the ADR-2032 FlowGuard (L34).

SHADOW MODE ONLY (ADR-0532 Phase 1 migration). This Skill's decision is
never authoritative — the production L34 gate stays
``corvin_operator/bridges/shared/spawn_gates.py::check_l34`` ->
``DataFlowGuard.validate()``. This wrapper is called *after* that decision
is made, purely to emit an audited comparison so the Skill can accumulate
a track record before it is ever allowed to decide anything (ADR-2092 G0
gate: >=500 decisions + join >=0.95 before any de-escalation path opens).

Do NOT wire this Skill's return value into a spawn-blocking decision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin
from .flow_guard.flow_guard import FlowGuard

logger = logging.getLogger(__name__)

# One FlowGuard instance per tenant (mirrors the tenant-scoped policy store).
_guard_cache: Dict[str, FlowGuard] = {}


def _get_guard(tenant_id: str) -> FlowGuard:
    guard = _guard_cache.get(tenant_id)
    if guard is None:
        guard = FlowGuard(tenant_id=tenant_id)
        _guard_cache[tenant_id] = guard
    return guard


class FlowGuardSkill(Skill):
    """Skill 2.0 shadow wrapper for L34 data-flow classification.

    Input:
        data: str — the raw text being classified (required; a caller with
            no raw text, e.g. a pre-classified acs_runtime spawn, should not
            call this Skill — there is nothing for the classifier to see).
        destination_engine: str — engine id data would flow to.
        tenant_id: str — tenant scope.

    Output:
        {
            "decision": "allow" | "deny" | "uncertain" | "blocked" (FlowDecision.value),
            "data_class": str,
            "confidence": float,
            "reasoning": str,
        }

    This output is NEVER consumed by a spawn gate. It exists to be
    audited and compared against the production gate's decision so the
    Skill accrues a trust record (ADR-2092).
    """

    def __init__(self) -> None:
        metadata = SkillMetadata(
            id="os.flow_guard",
            name="Flow Guard (L34 shadow)",
            description="Shadow-mode data-flow classification, compared against the live L34 gate",
            version="1.0.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["l34", "data-flow", "shadow", "adr-2032", "adr-0532"],
            # CORE (default), not COMPLIANCE: this Skill's output is shadow-only
            # (never spawn-blocking) and must stay disableable while it accrues
            # the ADR-2092 trust record. Promote to COMPLIANCE only alongside
            # the de-escalation path that makes it authoritative.
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        data = input.get("data")
        destination_engine = input.get("destination_engine")
        tenant_id = input.get("tenant_id") or "_default"

        if not data or not isinstance(data, str):
            # No raw text to classify — nothing this Skill can contribute.
            return {"decision": "SKIPPED", "reason": "no_raw_data_for_classification"}
        if not destination_engine or not isinstance(destination_engine, str):
            return {"decision": "SKIPPED", "reason": "no_destination_engine"}

        guard = _get_guard(tenant_id)
        try:
            evaluation = guard.evaluate_flow(data=data, destination_engine=destination_engine)
        except ValueError as exc:
            return {"decision": "SKIPPED", "reason": f"evaluate_flow_rejected_input: {exc}"}

        return {
            "decision": evaluation.decision.value,
            "data_class": evaluation.data_class,
            "confidence": evaluation.classification_confidence,
            "policy_confidence": evaluation.policy_confidence,
            "reasoning": evaluation.reasoning,
        }


def shadow_compare(
    *,
    prompt: Optional[str],
    engine_id: str,
    tenant_id: str,
    production_allowed: bool,
) -> Optional[Dict[str, Any]]:
    """Run the Skill in shadow mode (via the audited registry) and return a
    comparison record.

    Routed through ``get_registry().execute()`` rather than a direct
    ``FlowGuardSkill().execute()`` call so the run is audited as
    ``skill.executed`` / ``skill_executed`` (ADR-0314/ADR-0537) like every
    other OS-Skill invocation — a shadow Skill that bypasses the audit path
    would accrue no auditable trust record at all (ADR-2092 G0 needs one).

    Returns None when there is nothing to compare (no raw prompt) or the
    Skill is not registered for this tenant. Never raises — a Skill failure
    must not affect the caller's spawn decision.
    """
    if not prompt:
        return None
    try:
        from ..skill_registry_phase1 import get_registry

        registry = get_registry()
        exec_result = registry.execute(
            "os.flow_guard",
            {"data": prompt, "destination_engine": engine_id, "tenant_id": tenant_id},
            lom="corvin_operator/bridges/shared/spawn_gates.py:check_l34",
            tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 — shadow path must never raise
        logger.warning("os.flow_guard shadow execution failed: %r", exc)
        return None

    if exec_result.status != "success":
        return None

    result = exec_result.output or {}
    if result.get("decision") == "SKIPPED":
        return None

    skill_allowed = result.get("decision") == "allow"
    return {
        "skill_decision": result.get("decision"),
        "skill_data_class": result.get("data_class"),
        "production_allowed": production_allowed,
        "skill_allowed": skill_allowed,
        "agree": skill_allowed == production_allowed,
    }
