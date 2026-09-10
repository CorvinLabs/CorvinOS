"""Model Selector shadow classification (ADR-0641/0642) — SHADOW mode only.

Mirrors delegation_policy.py::_acp_shadow_route's contract exactly: the
classification NEVER alters which model/provider actually serves the turn.
It only runs ModelSelector.classify() (deterministic, no network, no LLM
call — feature_extractor.py is regex/string-only) and appends one audit
event to the tenant's hash-chained core chain via skill_audit.emit_skill_audit
— the same sink os.delegation_router's L5 shadow record uses. That gives the
console's Engine Configuration page a REAL signal source (classification
count + confidence per complexity tier) instead of hand-picked constants.

Until 2026-09-10 core.models.model_selection_routing.ModelSelectionRouter had
zero production callers: the classifier was built, unit-tested, and never
reached by a real turn — the exact gap class ADR-0532 Phase 1's adversarial
review (F1) found for os.delegation_router before its own 2026-09-06 shadow
wiring. operator/bridges/shared/adapter.py::call_claude_streaming is the one
call site every streaming turn passes through with the real prompt text
available, so it is the honest place to attach this — same reasoning
_acp_shadow_route already established for engine routing.

Never raises and never delays the turn beyond a best-effort, in-process call:
a stripped install without ``core.skills``/``core.models``, or a classifier
failure, degrades to "no shadow record" and the caller's turn is untouched.

Audit-safe (ADR-0129 metadata-only floor): the emitted details are counts and
enum labels only (complexity, confidence, provider/model ids, feature
counts) — never the prompt text itself, matching
ClassificationResult.to_dict()'s own "no PII, no prompts" contract.
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger(__name__)

#: chat_key -> {"task_type", "model", "tenant_id", "ts"} for the most recent
#: shadow classification on that chat_key, consumed by report_turn_outcome()
#: at the turn's real success hook (_call_claude_streaming_via_engine, the
#: one function both call_claude() and call_claude_streaming() funnel
#: through for claude_code). A dict, not a parameter thread-through: the
#: classification happens in the outer entry function, the success hook
#: lives in a different (inner) function several calls away, and chat_key is
#: the one identifier both already have in scope. Best-effort: a turn that
#: errors before reaching the success hook just leaves its entry to be
#: overwritten by that chat_key's next turn — never a crash, never a leak
#: beyond the size cap below.
_PENDING: dict[str, dict] = {}
_PENDING_CAP = 2000
_TASK_TYPE_BY_COMPLEXITY = {"simple": "SIMPLE", "medium": "MEDIUM", "complex": "COMPLEX"}


def shadow_classify_task(task_input: str, tenant_id: str, chat_key: str | None = None) -> None:
    """Classify *task_input* and audit the result. SHADOW ONLY — the caller's
    routing/model choice is decided elsewhere and stays untouched; this
    function's return value (None) makes that impossible to get backwards."""
    if not task_input or not isinstance(task_input, str):
        return
    try:
        from core.skills.os_skills.model_selector import ModelSelector  # noqa: PLC0415
        from core.skills.skill_audit import emit_skill_audit  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — bridge-only deployment without core.skills
        return
    try:
        # ADR-0129 M2 positive allowlist (default-deny floor, F-A4): emit_skill_audit
        # registers only its generic skill.* vocabulary (skill_id/status/latency_ms/
        # …) for whatever event_type it's given — none of this event's own fields
        # (complexity, confidence, recommended_provider/model, feature counts) are
        # in it, so every one of them is dropped and logged under _dropped_fields
        # unless registered here too. register_event_allowlist unions per event
        # type, so registering both is safe regardless of call order.
        try:
            import corvin_core._bootstrap  # noqa: F401 — makes `forge` importable in a source checkout
        except Exception:  # noqa: BLE001 — packaged layout without corvin_core
            pass
        from forge.security_events import register_event_allowlist  # type: ignore[import-not-found]  # noqa: PLC0415
        register_event_allowlist("skill.model_selector.classified", {
            "complexity", "confidence", "recommended_provider", "recommended_model",
            "shadow", "token_estimate", "code_blocks", "dependency_count",
        })
    except Exception:  # noqa: BLE001 — best-effort; a failed registration still
        pass          # lets emit_skill_audit try (generic fields would survive)
    overrides: dict = {}
    try:
        from core.models.model_selection_config import classifier_overrides  # noqa: PLC0415
        overrides = classifier_overrides(tenant_id)
    except Exception:  # noqa: BLE001 — no saved preference → hardcoded defaults
        pass
    try:
        result = ModelSelector(overrides=overrides).classify(task_input, tenant_id)
        emit_skill_audit(
            tenant_id=tenant_id,
            event_type="skill.model_selector.classified",
            tool="os.model_selector",
            details={
                "complexity": result.complexity,
                "confidence": result.confidence,
                "recommended_provider": result.recommended_provider,
                "recommended_model": result.recommended_model,
                "shadow": True,
                "token_estimate": result.features.token_estimate,
                "code_blocks": result.features.code_blocks,
                "dependency_count": result.features.dependency_count,
            },
        )
        if chat_key:
            task_type = _TASK_TYPE_BY_COMPLEXITY.get(result.complexity)
            if task_type is not None:
                if len(_PENDING) >= _PENDING_CAP:
                    _PENDING.pop(next(iter(_PENDING)), None)  # evict oldest-ish, cheap
                _PENDING[chat_key] = {
                    "task_type": task_type,
                    "model": result.recommended_model,
                    "tenant_id": tenant_id,
                    "ts": time.time(),
                }
    except Exception:  # noqa: BLE001 — advisory only; the turn already has its model
        _log.debug("model_selector shadow classify skipped", exc_info=True)


def report_turn_outcome(chat_key: str, success: bool) -> None:
    """Feed the real turn outcome into the Bayesian confidence optimizer
    (ADR-0644, core.learning.model_selection_optimizer) for whatever task
    type/model this chat_key's most recent shadow classification produced.

    ``success`` here is coarse and binary by design — "the claude_code engine
    call completed without an error/timeout" — not a nuanced quality
    assessment. That is a real, already-tracked signal (the same one
    _budget_account_turn's existing success-only-accounting convention uses,
    which this call sits next to), not a fabricated one; it is simply a
    starting proxy. A richer signal (user rating, retry detection) can
    replace ``quality_score`` below without changing this function's
    contract. SHADOW: this never influences which model actually served the
    turn — the classification already happened, before the turn ran.

    Never raises, never blocks: pops the pending entry so a chat_key with no
    subsequent turn doesn't retain it, degrades to a no-op silently if
    core.learning is unavailable or nothing was pending (e.g. the turn was
    refused pre-classification, at the budget-preflight gate).
    """
    pending = _PENDING.pop(chat_key, None)
    if pending is None:
        return
    try:
        from core.learning.model_selection_optimizer import get_optimizer  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — bridge-only deployment without core.learning
        return
    quality_score = 0.85 if success else 0.2
    try:
        get_optimizer().process_feedback(
            task_type=pending["task_type"],
            model=pending["model"],
            quality_score=quality_score,
            tenant_id=pending["tenant_id"],
        )
    except Exception:  # noqa: BLE001 — advisory only; the turn already completed
        _log.debug("model_selector outcome report skipped", exc_info=True)
