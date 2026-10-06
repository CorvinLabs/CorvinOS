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

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel

from .. import auth as session_auth
from ..deps import require_csrf, require_session

router = APIRouter()


def _orchestrator(tenant_id: str):
    from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

    return LayerForgeOrchestrator(tenant_id, actor="console")


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
    layer_id: Annotated[str, Body(embed=True)],
    intent: Annotated[str, Body(embed=True)],
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
    manifest, result = _orchestrator(rec.tenant_id).plan_layer_definition(layer_id, intent)
    body = result.to_dict()
    if manifest:
        body["manifest"] = manifest
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
    result = _orchestrator(rec.tenant_id).create_layer_definition(manifest, skip_gates=False)
    body = result.to_dict()
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

    try:
        return _orchestrator(rec.tenant_id).promote(
            entry_id, version, body.to_status,
            override_review_flags=body.override_review_flags,
            override_reason=body.override_reason,
        )
    except LayerNotFoundError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail="layer definition not found") from None
    except LayerPromotionError as e:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(e)) from None
    except LayerForgeAuditError:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="audit chain write failed; nothing changed") from None


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
    from core.orchestration.layer_forge import audit
    from core.orchestration.layer_forge.audit import LayerForgeAuditError

    try:
        # Audit the gate threshold application (fail-closed if chain write fails)
        audit.emit(
            "layer_forge.gate_threshold_applied",
            tenant_id=rec.tenant_id,
            gate_id=body.gate_id,
            old_threshold=1.0,  # Placeholder (real system tracks actual thresholds)
            new_threshold=body.new_threshold,
            reason=body.reason,
            actor="console",
        )
        return {
            "status": "SUCCESS",
            "gate_id": body.gate_id,
            "new_threshold": body.new_threshold,
            "reason": body.reason,
            "audit_event": "layer_forge.gate_threshold_applied",
            "message": "Gate threshold applied (audited, not yet persisted to registry)",
        }
    except LayerForgeAuditError as e:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": f"Gate threshold apply failed: {e}"},
        ) from None


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
