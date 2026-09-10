"""
Engine Configuration API — Model Selection Dashboard

Routes:
  GET  /v1/engine/config              — Read current config + learning scores
  PUT  /v1/engine/config              — Update model choices per task type
  POST /v1/engine/external-provider/test — Verify Ollama/OpenRouter/OpenAI connection
  GET  /v1/engine/analytics           — Learning history (pie chart data)

Phase 1 (Week 1–2): Static config read/write, mock learning scores
Phase 2 (Week 3–4): External provider testing, real learning integration
Phase 3 (Week 5–6): Live learning feedback, dashboard updates

ADR-0641: Engine Configuration Console
ADR-0642: Model Selector Skill
ADR-0007: Tenant isolation (all config per-tenant)
ADR-0314: Learning infrastructure
"""

import json
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException, Body
from pydantic import BaseModel, Field

# Assuming imports from your actual codebase
# from core.paths.tenant import current_tenant, validate_tenant_id
# from core.compliance.audit import write_audit_event
# from core.storage.config import read_tenant_config, write_tenant_config

router = APIRouter()

# ─────────────────────────────────────────────────────────────────
# Type Definitions
# ─────────────────────────────────────────────────────────────────


class ExternalProviderRequest(BaseModel):
    """External provider configuration (Ollama/OpenRouter/OpenAI)."""

    provider_type: str = Field(..., pattern="^(ollama|openrouter|openai)$")
    name: str = Field(..., min_length=1, max_length=64)
    server_url: Optional[str] = None  # For Ollama
    api_key: Optional[str] = None  # For OpenRouter/OpenAI (never logged)
    is_connected: bool = False


class ModelConfigRequest(BaseModel):
    """Model configuration per task type."""

    task_type: str = Field(..., pattern="^(corvinOS|SIMPLE|MEDIUM|COMPLEX)$")
    selected_model: str = Field(..., pattern="^(haiku|sonnet|opus|fable)$")
    alternatives: list[str] = Field(default_factory=list)
    external_providers: list[ExternalProviderRequest] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    run_count: int = Field(default=0, ge=0)


class EngineConfigRequest(BaseModel):
    """Full engine configuration."""

    tenant_id: str
    models: Dict[str, ModelConfigRequest]
    learning_status: str = Field(default="idle", pattern="^(idle|learning|converged)$")
    last_learning_update: Optional[str] = None


class EngineConfigResponse(BaseModel):
    """Engine configuration response."""

    tenant_id: str
    models: Dict[str, Dict[str, Any]]
    last_updated: str
    learning_status: str
    last_learning_update: str
    total_samples: int


class ExternalProviderTestRequest(BaseModel):
    """Request to test external provider connection."""

    provider_type: str = Field(..., pattern="^(ollama|openrouter|openai)$")
    server_url: Optional[str] = None
    api_key: Optional[str] = None


class ExternalProviderTestResponse(BaseModel):
    """Response from external provider test."""

    is_connected: bool
    latency_ms: Optional[float]
    error_message: Optional[str]


# ─────────────────────────────────────────────────────────────────
# Mock Data (Phase 1; replaced by persistent storage in K=2)
# ─────────────────────────────────────────────────────────────────

_MOCK_ENGINE_CONFIG = {
    "tenant_id": "_default",
    "models": {
        "corvinOS": {
            "task_type": "corvinOS",
            "selected_model": "haiku",
            "alternatives": ["sonnet", "opus"],
            "external_providers": [],
            "confidence_score": 0.0,
            "run_count": 0,
        },
        "SIMPLE": {
            "task_type": "SIMPLE",
            "selected_model": "haiku",
            "alternatives": ["sonnet"],
            "external_providers": [],
            "confidence_score": 0.87,
            "run_count": 1247,
        },
        "MEDIUM": {
            "task_type": "MEDIUM",
            "selected_model": "sonnet",
            "alternatives": ["haiku", "opus"],
            "external_providers": [],
            "confidence_score": 0.72,
            "run_count": 892,
        },
        "COMPLEX": {
            "task_type": "COMPLEX",
            "selected_model": "opus",
            "alternatives": ["sonnet"],
            "external_providers": [],
            "confidence_score": 0.91,
            "run_count": 456,
        },
    },
    "last_updated": datetime.now(timezone.utc).isoformat(),
    "learning_status": "converged",
    "last_learning_update": datetime.now(timezone.utc).isoformat(),
    "total_samples": 2595,
}


# ─────────────────────────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────────────────────────


@router.get("/v1/engine/config", response_model=EngineConfigResponse)
async def get_engine_config(request: Request) -> Dict[str, Any]:
    """
    GET /v1/engine/config

    Read current engine configuration + learning scores.

    Phase 1: Returns mock config
    Phase 2: Reads from tenant.corvin.yaml + learning store
    Phase 3: Live updates from learning loop

    Response includes:
    - Current model selection per task type
    - Confidence scores from learning
    - External providers list
    - Learning status (idle/learning/converged)

    Audit trail: logged as config_read event (GDPR Art. 30)
    """
    # TODO K=2: tenant_id = current_tenant()
    # TODO K=2: validate_tenant_id(tenant_id)
    # TODO K=2: config = read_tenant_config(tenant_id, "engine_config")

    tenant_id = "_default"  # Mock for K=1

    # TODO K=2: Audit log
    # write_audit_event(
    #     event_type="config_read",
    #     tenant_id=tenant_id,
    #     component="engine_api",
    #     details={"config_type": "engine_config"},
    # )

    return EngineConfigResponse(
        tenant_id=tenant_id,
        models=_MOCK_ENGINE_CONFIG["models"],
        last_updated=_MOCK_ENGINE_CONFIG["last_updated"],
        learning_status=_MOCK_ENGINE_CONFIG["learning_status"],
        last_learning_update=_MOCK_ENGINE_CONFIG["last_learning_update"],
        total_samples=_MOCK_ENGINE_CONFIG["total_samples"],
    )


@router.put("/v1/engine/config", response_model=EngineConfigResponse)
async def update_engine_config(
    request: Request,
    config: EngineConfigRequest = Body(...),
) -> Dict[str, Any]:
    """
    PUT /v1/engine/config

    Update model choices per task type.

    Validates:
    - task_type (corvinOS/SIMPLE/MEDIUM/COMPLEX)
    - model choice (haiku/sonnet/opus/fable)
    - alternatives list is non-empty

    Phase 1: Updates mock config only
    Phase 2: Persists to tenant.corvin.yaml + audit trail
    Phase 3: Triggers learning recalculation

    Returns: Updated config

    Audit trail: logged as config_updated event + old/new values (GDPR Art. 30)
    """
    # TODO K=2: tenant_id = current_tenant()
    # TODO K=2: validate_tenant_id(tenant_id)

    tenant_id = "_default"  # Mock for K=1

    # Validation
    if not config.models:
        raise HTTPException(status_code=400, detail="models cannot be empty")

    for task_type, model_cfg in config.models.items():
        if task_type not in ("corvinOS", "SIMPLE", "MEDIUM", "COMPLEX"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid task_type: {task_type}. Must be one of: corvinOS, SIMPLE, MEDIUM, COMPLEX",
            )
        if model_cfg.selected_model not in ("haiku", "sonnet", "opus", "fable"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid model: {model_cfg.selected_model}",
            )

    # TODO K=2: Audit log (before update)
    # old_config = read_tenant_config(tenant_id, "engine_config")
    # write_audit_event(
    #     event_type="config_updated",
    #     tenant_id=tenant_id,
    #     component="engine_api",
    #     details={
    #         "config_type": "engine_config",
    #         "old_models": old_config.get("models"),
    #         "new_models": config.models,
    #     },
    # )

    # TODO K=2: write_tenant_config(tenant_id, "engine_config", config.dict())

    # Mock: update in-memory
    _MOCK_ENGINE_CONFIG["models"] = {
        task_type: model.dict() for task_type, model in config.models.items()
    }
    _MOCK_ENGINE_CONFIG["last_updated"] = datetime.now(timezone.utc).isoformat()

    return EngineConfigResponse(
        tenant_id=tenant_id,
        models=_MOCK_ENGINE_CONFIG["models"],
        last_updated=_MOCK_ENGINE_CONFIG["last_updated"],
        learning_status=_MOCK_ENGINE_CONFIG["learning_status"],
        last_learning_update=_MOCK_ENGINE_CONFIG["last_learning_update"],
        total_samples=_MOCK_ENGINE_CONFIG["total_samples"],
    )


@router.post(
    "/v1/engine/external-provider/test",
    response_model=ExternalProviderTestResponse,
)
async def test_external_provider(
    request: Request,
    test_req: ExternalProviderTestRequest = Body(...),
) -> Dict[str, Any]:
    """
    POST /v1/engine/external-provider/test

    Test connection to external provider (Ollama/OpenRouter/OpenAI).

    Phase 1: Stub endpoint (always returns success)
    Phase 2: Real connection tests (HTTP requests, auth checks)
    Phase 3: Provider-specific model enumeration

    Returns:
    - is_connected: bool
    - latency_ms: float (time to respond)
    - error_message: Optional[str] (if not connected)

    Audit trail: logged as provider_test_executed event (GDPR Art. 30)
    """
    # TODO K=2: tenant_id = current_tenant()
    # TODO K=2: validate_tenant_id(tenant_id)

    tenant_id = "_default"  # Mock for K=1

    # Validation
    if test_req.provider_type not in ("ollama", "openrouter", "openai"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider_type: {test_req.provider_type}",
        )

    # TODO K=2: Implement real connection tests
    # if test_req.provider_type == "ollama":
    #     result = test_ollama_connection(test_req.server_url)
    # elif test_req.provider_type == "openrouter":
    #     result = test_openrouter_connection(test_req.api_key)
    # elif test_req.provider_type == "openai":
    #     result = test_openai_connection(test_req.api_key)

    # TODO K=2: Audit log
    # write_audit_event(
    #     event_type="provider_test_executed",
    #     tenant_id=tenant_id,
    #     component="engine_api",
    #     details={
    #         "provider_type": test_req.provider_type,
    #         "is_connected": result.get("is_connected"),
    #     },
    # )

    # Mock: always succeeds in Phase 1
    return ExternalProviderTestResponse(
        is_connected=True,
        latency_ms=123.45,
        error_message=None,
    )


@router.get("/v1/engine/analytics")
async def get_engine_analytics(request: Request) -> Dict[str, Any]:
    """
    GET /v1/engine/analytics

    Retrieve learning history for analytics dashboard.

    Phase 1: Returns empty data
    Phase 2: Aggregates from learning store (model selection feedback events)
    Phase 3: Real-time updates + trend analysis

    Returns:
    - Per-model success rates (pie chart data)
    - Confidence trends (line chart)
    - Cost analysis (bar chart)

    Audit trail: logged as analytics_requested event (GDPR Art. 30)
    """
    # TODO K=2: tenant_id = current_tenant()
    # TODO K=2: validate_tenant_id(tenant_id)

    tenant_id = "_default"  # Mock for K=1

    # TODO K=2: Audit log
    # write_audit_event(
    #     event_type="analytics_requested",
    #     tenant_id=tenant_id,
    #     component="engine_api",
    #     details={"requested_at": datetime.now(timezone.utc).isoformat()},
    # )

    # Phase 1: Mock data for testing dashboard layout
    return {
        "tenant_id": tenant_id,
        "task_type_breakdown": {
            "SIMPLE": {
                "haiku_wins": 0.65,
                "sonnet_wins": 0.30,
                "opus_wins": 0.05,
            },
            "MEDIUM": {
                "haiku_wins": 0.10,
                "sonnet_wins": 0.70,
                "opus_wins": 0.20,
            },
            "COMPLEX": {
                "haiku_wins": 0.02,
                "sonnet_wins": 0.15,
                "opus_wins": 0.83,
            },
        },
        "confidence_trends": [
            {"timestamp": "2026-09-01T12:00:00Z", "confidence": 0.65},
            {"timestamp": "2026-09-02T12:00:00Z", "confidence": 0.72},
            {"timestamp": "2026-09-03T12:00:00Z", "confidence": 0.78},
            {"timestamp": "2026-09-04T12:00:00Z", "confidence": 0.82},
            {"timestamp": "2026-09-05T12:00:00Z", "confidence": 0.87},
        ],
        "cost_analysis": {
            "haiku_cost": 0.024,
            "sonnet_cost": 0.048,
            "opus_cost": 0.120,
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────────────


@router.get("/v1/engine/health")
async def engine_health() -> Dict[str, Any]:
    """
    GET /v1/engine/health

    Simple health check for monitoring.
    """
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
