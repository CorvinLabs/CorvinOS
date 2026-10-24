"""Layer Forge console routes (ADR-2222, ADR-2225 Phase 1).

    GET  /layer-forge/definitions                          list (session)
    GET  /layer-forge/definitions/{entry_id}[?version=]    one entry (session)
    POST /layer-forge/definitions                          run the pipeline (session + CSRF)
    POST /layer-forge/definitions/{entry_id}/{version}/transition   status change (session + CSRF)
    POST /layer-forge/plan                                  LLM plan (session + CSRF)

Tenant is ALWAYS the authenticated session's ``rec.tenant_id``. Every decision is
written audit-first into that tenant's hash chain by the orchestrator; a chain
write that does not commit answers 503 and changes nothing. Quality gates cannot
be skipped from here (the CLI's ``--skip-gates`` is an operator-local tool).

Handlers are sync ``def``: the pipeline runs pytest subprocesses, and FastAPI
runs sync handlers in its threadpool instead of on the event loop.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel, ConfigDict

from .. import auth as session_auth
from .. import forge_runs
from ..deps import require_csrf, require_session

router = APIRouter()

#: ADR-2253 D1: each layer operation is its own run with its own phases (the three requests are independent: a
#: CREATE needs no PLAN and a promote can come days later), linked by ``artifact_ref`` ``id@version``.
_PLAN_PHASES = ("plan",)
_CREATE_PHASES = ("validate", "test", "enforce", "review", "create")
_PROMOTE_PHASES = ("promote",)
_PCT = {"plan": 50, "validate": 10, "test": 30, "enforce": 55, "review": 75, "create": 90, "promote": 50}


def _orchestrator(tenant_id: str, observer=None):
    from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

    return LayerForgeOrchestrator(tenant_id, actor="console", observer=observer)


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _identifiable(manifest: Any) -> bool:
    """True when a manifest at least names an artifact (a well-formed id and semver). A request that does not is
    garbage, not a Forge operation: it records no run and no artifact reference (the same rule as a promote of an
    unknown definition) - otherwise the history fills with whatever text a caller posts."""
    return (isinstance(manifest, dict) and isinstance(manifest.get("id"), str) and isinstance(manifest.get("version"), str)
            and bool(_ID_RE.match(manifest["id"])) and bool(_VERSION_RE.match(manifest["version"])))


def _start_run(rec: session_auth.SessionRecord, operation: str, phases: tuple[str, ...]):
    """A run for one synchronous layer operation, plus the observer that feeds it. A failing run store never
    fails the operation: the pipeline is the product, the run record is its transcript."""
    try:
        run = forge_runs.InlineRun(tenant_id=rec.tenant_id, kind="layer", operation=operation, phases=phases,
                                   sid_fingerprint=rec.sid_fingerprint)
    except Exception:  # noqa: BLE001
        return None, None

    def observe(event: str, phase: str, message: str, data: dict) -> None:
        if event == "phase":
            run.progress(phase, _PCT.get(phase, 50), message)
        else:
            run.event("gate", message, phase=phase, data=data)

    return run, observe


def _finish_run(run, result, *, own_phase: str, artifact_id: str = "") -> None:
    """Fold a LayerForgeResult into the run. An ``audit`` failure is reported in the operation's own phase (a failing
    phase must always be one of the run's ``phases``) with ``failure_code: audit_unavailable``."""
    if run is None:
        return
    if result.status == "SUCCESS":
        run.succeed(artifact_id or str(result.registry_key or ""), f"{own_phase} succeeded")
        return
    audit_down = result.phase == "audit"
    run.fail(own_phase if audit_down else str(result.phase or own_phase), str(result.error or "failed"),
             code="audit_unavailable" if audit_down else None, artifact_id=artifact_id)


class PlanBody(BaseModel):
    """``extra=forbid``: an unknown field is a 422, never silently dropped (ADR-2254 - a client that sends attachment ids
    to a path that does not consume them must hear it)."""
    model_config = ConfigDict(extra="forbid")
    layer_id: str
    intent: str
    #: ADR-2254: ids returned by POST /forge-intake/attachments; the PLAN sees only their redacted projection.
    attachment_ids: list[str] = []


class TransitionBody(BaseModel):
    to_status: Literal["accepted", "deployed", "superseded", "proposed"]
    override_review_flags: bool = False
    override_reason: str = ""


class GateThresholdAnalysisBody(BaseModel):
    gate_id: str
    lookback_days: int = 30


class GateThresholdApplyBody(BaseModel):
    gate_id: str
    new_threshold: float
    reason: str


@router.get("/layer-forge/definitions")
def list_definitions(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    items = _orchestrator(rec.tenant_id).list_definitions()
    return {"items": items, "count": len(items)}


@router.get("/layer-forge/definitions/{entry_id}")
def get_definition(
    entry_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    version: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    from core.orchestration.layer_forge.registry import LayerNotFoundError

    try:
        return _orchestrator(rec.tenant_id).get(entry_id, version)
    except LayerNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail="layer definition not found") from None


@router.post("/layer-forge/plan")
def plan_definition(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body_in: PlanBody,
) -> dict[str, Any]:
    """Generate a layer-definition manifest via LLM.

    Input:
        layer_id: Layer identifier (e.g., 'L34')
        intent: What the layer should do (e.g., 'audit L10 + enforce bounds')

    Output (success):
        {status: 'SUCCESS', manifest: {...}, registry_key: null}

    Output (failure):
        {status: 'FAILED', error: '...', phase: 'plan'}
    """
    layer_id, intent = body_in.layer_id, body_in.intent
    if body_in.attachment_ids:  # re-analysed from the stored bytes now; any problem fails the whole request
        from .forge_intake_api import pack_prompt_block, resolve_pack  # noqa: PLC0415
        intent += pack_prompt_block(resolve_pack(body_in.attachment_ids, tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint))
    run, observe = _start_run(rec, "plan", _PLAN_PHASES)
    if run is not None:
        run.progress("plan", 20, "Asking the engine for a manifest")
    manifest, result = _orchestrator(rec.tenant_id, observe).plan_layer_definition(layer_id, intent)
    body = result.to_dict()
    if manifest:
        body["manifest"] = manifest
    _finish_run(run, result, own_phase="plan",
                artifact_id=f"{manifest.get('id')}@{manifest.get('version')}" if isinstance(manifest, dict) and manifest.get("id") else "")
    if run is not None:
        body["run_id"] = run.run_id
    if result.status == "SUCCESS":
        return body
    if result.phase == "audit":
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail=body)
    raise HTTPException(status_code=422, detail=body)


@router.post("/layer-forge/definitions")
def create_definition(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    manifest: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    identifiable = _identifiable(manifest)
    run, observe = _start_run(rec, "create", _CREATE_PHASES) if identifiable else (None, None)
    result = _orchestrator(rec.tenant_id, observe).create_layer_definition(manifest, skip_gates=False)
    artifact = f"{manifest['id']}@{manifest['version']}" if identifiable else ""
    _finish_run(run, result, own_phase="create", artifact_id=artifact)
    body = result.to_dict()
    if run is not None:
        body["run_id"] = run.run_id
    if result.status == "SUCCESS":
        return body
    if result.phase == "audit":
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail=body)
    if result.phase == "validate" and "already exists" in (result.error or ""):
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=body)
    raise HTTPException(status_code=422, detail=body)


@router.post("/layer-forge/definitions/{entry_id}/{version}/transition")
def transition_definition(
    entry_id: str,
    version: str,
    body: TransitionBody,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Transition a layer definition to a new status (Phase 3A with override support).

    Args:
        entry_id: Layer definition ID
        version: Layer definition version
        body:
            to_status: Target status (accepted | deployed | superseded | proposed)
            override_review_flags: Set to true to override FLAGGED review verdict
            override_reason: Reason for override (required if override_review_flags=true)

    Returns:
        Updated definition with new status

    Raises:
        409: FLAGGED without override, or invalid transition
        404: Definition not found
        503: Audit chain write failed
    """
    from core.orchestration.layer_forge.audit import LayerForgeAuditError
    from core.orchestration.layer_forge.registry import LayerNotFoundError, LayerPromotionError

    orch = _orchestrator(rec.tenant_id)
    try:
        orch.get(entry_id, version)  # an unknown definition is a 404 and records no run
    except LayerNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail="layer definition not found") from None
    run, observe = _start_run(rec, "promote", _PROMOTE_PHASES)
    orch.observer = observe
    artifact = f"{entry_id}@{version}"
    if run is not None:
        run.progress("promote", 30, f"Moving {artifact} to {body.to_status}")
    try:
        out = orch.promote(
            entry_id, version, body.to_status,
            override_review_flags=body.override_review_flags,
            override_reason=body.override_reason,
        )
    except LayerNotFoundError:
        if run is not None:
            run.fail("promote", "layer definition not found", code="not_found", artifact_id=artifact)
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail="layer definition not found") from None
    except LayerPromotionError as e:
        if run is not None:
            run.fail("promote", str(e), code="promotion_refused", artifact_id=artifact)
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(e)) from None
    except LayerForgeAuditError:
        if run is not None:
            run.fail("promote", "audit chain write failed; nothing changed", code="audit_unavailable", artifact_id=artifact)
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="audit chain write failed; nothing changed") from None
    if run is not None:
        run.succeed(artifact, f"{artifact} is now {body.to_status}")
        if isinstance(out, dict):
            out = {**out, "run_id": run.run_id}
    return out


@router.post("/layer-forge/gate-thresholds/analyze")
def analyze_gate_threshold(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: GateThresholdAnalysisBody,
) -> dict[str, Any]:
    """Analyze a gate's threshold tuning signal (Phase 4 A1).

    Input:
        gate_id: Quality gate identifier (e.g., 'schema_check')
        lookback_days: Analysis window in days (default 30)

    Output (success):
        {
            "gate_id": "schema_check",
            "total_fails": 5,
            "override_successes": 3,
            "override_failures": 1,
            "override_success_rate": 0.75,
            "signal": "overcautious",
            "is_significant": true
        }
    """
    from core.orchestration.layer_forge.analytics import LayerForgeAnalytics
    from core.orchestration.layer_forge.optimizer import GateThresholdAnalyzer

    orchestrator = _orchestrator(rec.tenant_id)
    analytics = LayerForgeAnalytics(registry=orchestrator.registry, tenant_id=rec.tenant_id)
    analyzer = GateThresholdAnalyzer(rec.tenant_id, analytics_engine=analytics)

    pattern = analyzer.analyze_gate(body.gate_id, lookback_days=body.lookback_days)
    return {
        "gate_id": pattern.gate_id,
        "total_fails": pattern.total_fails,
        "override_successes": pattern.override_successes,
        "override_failures": pattern.override_failures,
        "override_success_rate": round(pattern.override_success_rate, 3),
        "signal": pattern.signal.value,
        "is_significant": pattern.is_significant,
    }


@router.post("/layer-forge/gate-thresholds/apply")
def apply_gate_threshold(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: GateThresholdApplyBody,
) -> dict[str, Any]:
    """Apply a gate-threshold change (operator-explicit, audited, never auto-applied).

    Phase 4 A1: Operator explicitly applies threshold changes; suggestions are never
    auto-applied. The change is recorded in the audit chain fail-closed.

    Input:
        gate_id: Quality gate identifier
        new_threshold: New threshold value
        reason: Why this threshold is being applied

    Output (success):
        {
            "status": "SUCCESS",
            "gate_id": "schema_check",
            "new_threshold": 0.85,
            "reason": "Overcautious feedback: 8/10 overrides succeeded",
            "audit_event": "layer_forge.gate_threshold_applied"
        }

    Output (failure):
        {status: "FAILED", error: "..."}
    """
    # Nothing persists a gate threshold: the registry stores none and no gate
    # reads one. This route used to emit ``layer_forge.gate_threshold_applied``
    # with a hard-coded ``old_threshold=1.0`` and answer "not yet persisted",
    # i.e. a fictional record in the hash chain. It answers 501 and writes
    # nothing until a real threshold store exists (analyze above stays real).
    raise HTTPException(
        status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
        detail=("Gate-threshold apply is not implemented on this build: no store "
                "holds a gate threshold and no gate reads one, so a success would "
                "be cosmetic. Use /layer-forge/gate-thresholds/analyze for the "
                "tuning signal."),
    )


@router.get("/layer-forge/analytics")
def get_analytics(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Operator Dashboard analytics: decisions, confidence, flags.

    Query parameters:
        since: Start date (ISO 8601, e.g., "2026-10-01")
        until: End date (ISO 8601, e.g., "2026-10-05")

    Returns:
        {
            "decisions_accepted": 5,
            "decisions_flagged": 2,
            "decisions_rejected": 1,
            "mean_confidence": 0.72,
            "flags_distribution": {"scope_creep": 1, "security_gap": 1, ...},
            "decisions_by_week": {"2026-W40": {...}},
            "flags_by_week": {"2026-W40": {...}},
            "confidence_by_week": {"2026-W40": 0.75},
            "convergence": [{"timestamp": "...", "delta": 0.15, "entry_id": "L1"}],
            "window": {"since": "2026-10-01", "until": "2026-10-05", "days": 5}
        }
    """
    from core.orchestration.layer_forge.analytics import LayerForgeAnalytics
    from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

    orchestrator = _orchestrator(rec.tenant_id)
    analytics = LayerForgeAnalytics(
        registry=orchestrator.registry,
        tenant_id=rec.tenant_id,
        learning_store=None,  # Learning store integrated in Phase 3.2
    )

    # Get summary metrics
    summary = analytics.get_summary_metrics(since_iso=since, until_iso=until)

    # Get detailed breakdowns
    return {
        **summary,
        "decisions_by_week": analytics.get_decisions_by_week(since_iso=since, until_iso=until),
        "flags_by_week": analytics.get_flags_distribution(since_iso=since, until_iso=until),
        "confidence_by_week": analytics.get_confidence_trend(since_iso=since, until_iso=until),
        "convergence": analytics.get_convergence(since_iso=since, until_iso=until),
    }
