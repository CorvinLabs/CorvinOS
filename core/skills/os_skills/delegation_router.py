"""L5 Delegation Router Skill — advice on the worker-engine route (ADR-0532, ADR-2092).

Skill ID: os.delegation_router · Version 2.0.0 · Tier core · Origin builtin

The bundled rule in ``corvin_operator/bridges/shared/delegation_policy.py`` decides the
route. This Skill ADVISES, and its advice has exactly two admissible shapes:

* confirm the bundled engine, or
* de-escalate to ``native`` when the delegation looks unnecessary.

It never advises an escalation or an engine the operator did not select — the same
bound ADR-0251 D2 puts on plugin hooks. In shadow mode the advice is only recorded; in
Phase 2 the call site clamps it again (``monitoring.dual_write.clamp``) before serving.

Input (all optional except ``tenant_id``)::

    {
      "tenant_id": str,
      "bundled_engine": "native" | "acs" | "tde",
      "force_delegate": bool, "is_big_data": bool,
      "mode": "native" | "acs" | "tde",
      "features": {complexity, len_bucket, has_table, has_code, ...},  # closed vocab
      "complexity": int 1-10 (legacy callers; mapped onto the feature tier),
      "shadow": bool,
    }

Output::

    {"engine": str, "decision": str, "confidence": float, "reasoning": str,
     "bundled_engine": str?, "shadow": bool?, "confidence_threshold": float?,
     "learned_config_version": str?}

``engine`` and ``decision`` carry the same value: ``engine`` is what the Skill audit
projection (``skill_registry_phase1.decision_summary``) keeps and what L10's context
adapter reads; ``decision`` is kept for existing readers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin, SkillTier

logger = logging.getLogger(__name__)

_ENGINES = ("native", "acs", "tde")


def _tier_from_legacy(complexity: Any) -> str:
    if isinstance(complexity, bool) or not isinstance(complexity, (int, float)):
        return "unknown"
    if complexity >= 8:
        return "complex"
    if complexity >= 5:
        return "medium"
    return "simple"


def advise(*, bundled: str, force_delegate: bool, is_big_data: bool, mode: str,
           features: Dict[str, Any]) -> tuple[str, float, str]:
    """Pure advice: ``(engine, confidence, reasoning_code)``. No I/O, no state."""
    if bundled not in _ENGINES:
        bundled = "native"
    if bundled == "native":
        return "native", 0.95, "bundled_native_confirmed"
    if force_delegate:
        return bundled, 0.99, "explicit_delegate_confirmed"
    tier = features.get("complexity", "unknown")
    short = features.get("len_bucket") in ("xs", "s")
    tabular = bool(features.get("has_table"))
    if is_big_data and tier == "simple" and short and not tabular:
        return "native", 0.80, "big_data_signal_on_short_simple_request"
    if not is_big_data and mode in ("acs", "tde") and tier == "simple" and short:
        return "native", 0.80, "delegating_mode_on_short_simple_request"
    return bundled, 0.90, "delegation_confirmed"


class DelegationRouterSkill(Skill):
    """Advise confirm-or-de-escalate on the bundled worker-engine route."""

    def __init__(self) -> None:
        super().__init__(SkillMetadata(
            id="os.delegation_router",
            name="Delegation Router",
            description="Advise whether a delegated route can be served natively (L5)",
            version="2.0.0",
            origin=SkillOrigin.BUILTIN,
            tier=SkillTier.CORE,
            owner="corvin-os-team",
            tags=["routing", "delegation", "os-core", "l5"],
        ))

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        tenant_id = input.get("tenant_id", "_default")
        bundled = input.get("bundled_engine") or "native"
        force_delegate = bool(input.get("force_delegate", False))
        is_big_data = bool(input.get("is_big_data", False))
        mode = input.get("mode") if input.get("mode") in _ENGINES else "native"
        features = dict(input.get("features") or {})
        if "complexity" not in features:
            features["complexity"] = _tier_from_legacy(input.get("complexity"))
        if not input.get("bundled_engine") and (force_delegate or is_big_data):
            bundled = "acs"

        try:
            engine, confidence, reasoning = advise(
                bundled=bundled, force_delegate=force_delegate,
                is_big_data=is_big_data, mode=mode, features=features,
            )
        except Exception as exc:  # noqa: BLE001 — advice failure confirms the rule
            logger.warning("DelegationRouter advice failed (%s)", type(exc).__name__)
            engine, confidence, reasoning = bundled, 0.5, f"advice_error:{type(exc).__name__}"

        result: Dict[str, Any] = {
            "engine": engine,
            "decision": engine,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        threshold = _learned_threshold(tenant_id)
        if threshold is not None:
            result["confidence_threshold"] = threshold[0]
            result["learned_config_version"] = threshold[1]
        if input.get("shadow"):
            result["shadow"] = True
            result["bundled_engine"] = bundled
        return result


def _learned_threshold(tenant_id: Any) -> Optional[tuple[float, Optional[str]]]:
    """The learned acceptance threshold for de-escalation advice, if one was learned."""
    if not isinstance(tenant_id, str) or not tenant_id:
        return None
    try:
        from .skill_adapter import load_skill_config  # noqa: PLC0415

        config, version = load_skill_config("os.delegation_router", tenant_id)
    except Exception:  # noqa: BLE001 — no learned config is the normal state
        return None
    if version is None:
        return None
    value = getattr(config, "confidence_threshold", None)
    if isinstance(value, (int, float)) and 0.0 <= value <= 1.0:
        return float(value), version
    return None
