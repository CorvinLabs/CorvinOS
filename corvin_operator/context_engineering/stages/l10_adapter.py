"""L10 Context Adapter stage (ADR-0532 Phase 1) — os.context_adapter in SHADOW mode.

Runs the ``os.context_adapter`` Skill once per CEL turn and AUDITS its advice;
it never changes what is served. Same contract as the L5 shadow route
(``delegation_policy._acp_shadow_route``, ADR-0613): the Skill decides, the
decision is recorded, the existing behaviour stands.

Reachability (the production chain, both hosts):
  turn → ``adapter.py`` / ``chat_runtime.py`` (``vibe_engineering`` flag)
       → ``pipeline.build_brief`` / ``run_full_pipeline[_async]``
       → ``pipeline.build_context`` → this stage (in DEFAULT_PIPELINE and
         ACTIVE_PIPELINE, pure / pre-gate) → ``adapt_context_l10``
       → ``SkillsRegistry.execute("os.context_adapter")``.

Shadow means:
  - ``bundle.brief`` is not touched and nothing rendered into the prompt is
    derived from the Skill output. Only a content-free summary lands in
    ``bundle.scratch["l10_shadow"]`` (not rendered, not gated, not bound).
  - The Skill gets content-free inputs only (complexity bucket, a fixed task
    type, empty description / user context): the task text never leaves this
    stage, so it cannot reach the Skill's audit or learning records.

Audit (every execution, two records, both hash-chained):
  - ``skill.executed`` — written by the registry through the audit backend
    ``core.skills.boot.boot_skills`` wired at boot.
  - ``context.adapted`` (``adaptation_type="l10_shadow"``) — written here into
    the TURN tenant's chain (``forge.paths.tenant_audit_chain``). A write
    failure is logged at ERROR, never swallowed silently; the turn continues
    because the adaptation is advisory.

Only the BOOTED registry is used. A process that never ran ``boot_platform``
(the bridge adapter today) has no audit backend on the Skills registry; the
old code lazily built one there via ``get_integration()`` and ran the Skill
with ``audit_backend=None`` — an unaudited execution on every bridge turn. Now
that case is ``skipped`` (``skills_not_booted``), visible in the CEL trace. A
turn whose tenant is not the registry's boot tenant is ``skipped``
(``tenant_not_booted``) too, so a Skill record is never written into another
tenant's chain. Checking ``sys.modules`` first keeps the hot path import-free
when Skills were never booted.
"""
from __future__ import annotations

import logging
import sys
import time
import uuid

from .base import StageTelemetry
from .registry import register_stage

_log = logging.getLogger(__name__)

_REGISTRY_MODULE = "core.skills.skill_registry_phase1"
_INTEGRATION_MODULE = "core.skills.os_skills_integration"
_SKILL_ID = "os.context_adapter"
# Advisory work on the turn's critical path — bounded well below the registry
# default (5 s). ContextAdapterSkill is pure Python and runs in milliseconds.
_TIMEOUT_MS = 2000
_COMPLEXITY_BUCKETS = {"simple": 3, "moderate": 5, "complex": 8}


def _booted_integration():
    """The integration layer ``boot_skills`` created in THIS process, or None.

    Never initialises anything: a lazy init would build a registry without an
    audit backend (see module docstring).
    """
    reg_mod = sys.modules.get(_REGISTRY_MODULE)
    integ_mod = sys.modules.get(_INTEGRATION_MODULE)
    if reg_mod is None or integ_mod is None:
        return None
    registry = getattr(reg_mod, "_global_registry", None)
    integration = getattr(integ_mod, "_integration_instance", None)
    if registry is None or integration is None:
        return None
    if getattr(integration, "registry", None) is not registry:
        return None
    if getattr(registry, "audit_backend", None) is None:
        return None  # an unaudited registry is not an admissible execution target
    if registry.get(_SKILL_ID) is None:
        return None
    return integration


def _emit_context_adapted(tenant_id: str, summary: dict) -> bool:
    """Chain the shadow decision into the turn tenant's audit chain."""
    delta = (
        f"served=unchanged;skill={'ok' if summary['skill_executed'] else 'fallback'};"
        f"engine={summary['engine'] or '-'};priority={summary['priority']};"
        f"injected={int(summary['injected'])}"
    )[:120]
    try:
        from forge.paths import tenant_audit_chain  # noqa: PLC0415
        from forge.security_events import write_event  # noqa: PLC0415
        write_event(
            tenant_audit_chain(tenant_id), "context.adapted",
            tool="context_engineering",
            details={
                "context_id": uuid.uuid4().hex[:16],
                "tenant_id": tenant_id,
                "adaptation_type": "l10_shadow",
                "delta_summary": delta,
                "user_model_updated": False,
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001 — advisory stage: surface, do not break the turn
        _log.error("L10 shadow: context.adapted AUDIT-WRITE FAILED (tenant %s): %s "
                   "— record NOT persisted to the hash chain", tenant_id,
                   type(exc).__name__)
        return False


class L10AdapterStage:
    """CEL stage: execute os.context_adapter in shadow mode and audit it."""
    id = "l10_adapter"
    requires: tuple = ("graph",)
    effect = "pure"      # no egress; the only side effect is the audit record
    trust = "builtin"

    def run(self, bundle, ctx):
        t0 = time.monotonic()
        tenant_id = getattr(ctx, "tenant_id", "") or ""
        integration = _booted_integration()
        if integration is None:
            return bundle, StageTelemetry(stage=self.id, status="skipped",
                                          reason="skills_not_booted")
        if tenant_id != getattr(integration, "tenant_id", None):
            return bundle, StageTelemetry(stage=self.id, status="skipped",
                                          reason="tenant_not_booted")
        try:
            from core.skills.os_skills_integration import adapt_context_l10  # noqa: PLC0415

            task_obj = getattr(ctx, "task_obj", None)
            complexity = _COMPLEXITY_BUCKETS.get(
                getattr(task_obj, "task_complexity", "moderate"), 5)
            # THE production call site. Content-free inputs only (module docstring).
            result = adapt_context_l10(
                complexity=complexity,
                task_type="general",
                task_description="",
                priority_hint=5,
                user_context={},
                tenant_id=tenant_id,
                timeout_ms=_TIMEOUT_MS,
            ) or {}
            merged = result.get("merged_tier") or {}
            summary = {
                "skill_executed": bool(result.get("skill_executed")),
                "engine": merged.get("engine") if isinstance(merged.get("engine"), str) else "",
                "priority": merged.get("priority") if isinstance(merged.get("priority"), int) else 0,
                "injected": result.get("injected_tier") is not None,
            }
            summary["audited"] = _emit_context_adapted(tenant_id, summary)
            # Shadow: a content-free summary in scratch; brief and prompt untouched.
            bundle.scratch["l10_shadow"] = summary
            return bundle, StageTelemetry(
                stage=self.id,
                status="ok" if summary["audited"] else "failed",
                confidence_tier="medium" if summary["skill_executed"] else "low",
                duration_ms=round((time.monotonic() - t0) * 1000, 3),
                reason=("shadow" if summary["audited"] else "audit_write_failed"),
            )
        except Exception as exc:  # noqa: BLE001 — advisory stage never breaks the turn
            _log.error("L10 shadow adapter failed: %s", type(exc).__name__)
            return bundle, StageTelemetry(stage=self.id, status="failed",
                                          confidence_tier="low", reason="error")


register_stage(L10AdapterStage())
