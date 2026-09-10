"""
Engine Configuration API — Model Selection Dashboard

Routes:
  GET  /v1/engine/config              — Read current per-task-type config + real classification stats
  PUT  /v1/engine/config              — Update model/provider choice per task type (persisted)
  POST /v1/engine/external-provider/test — Real Ollama/OpenRouter/OpenAI connectivity test
  GET  /v1/engine/health              — Health check

Real data (2026-09-10), replacing the Phase-1 K=1 mock:
  - Persistence: core.models.model_selection_config (JSON file per tenant),
    consulted by the shadow classifier (model_selector_shadow.py) via
    ModelSelector(overrides=...) — a saved choice has a real effect on the
    next turn's classification, not just on this page.
  - Stats (run_count / confidence / last_updated): aggregated from the
    tenant's real hash-chained audit chain, filtered to
    ``skill.model_selector.classified`` events (emitted by the SHADOW
    classification wired into operator/bridges/shared/adapter.py's two real
    turn call sites — see model_selector_shadow.py for why). Honest empty
    state (0 samples) is expected and correct until real turns accrue.
  - "corvinOS" task type has no classifier equivalent (task classification
    itself is deterministic regex/keyword rules, not model-driven) — its
    stats are always 0/none; the field stays for UI symmetry.

ADR-0641: Engine Configuration Console
ADR-0642: Model Selector Skill
ADR-0007: Tenant isolation (all config per-tenant)
ADR-0314: Learning infrastructure
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_csrf, require_session

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

router = APIRouter()

TASK_TYPES = ("corvinOS", "SIMPLE", "MEDIUM", "COMPLEX")
_VALID_ANTHROPIC_MODELS = (
    "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-fable-5",
)


# ─────────────────────────────────────────────────────────────────
# Request/Response models
# ─────────────────────────────────────────────────────────────────


class ModelConfigRequest(BaseModel):
    """Model configuration for one task type."""
    model_config = {"extra": "forbid"}

    task_type: str = Field(..., pattern="^(corvinOS|SIMPLE|MEDIUM|COMPLEX)$")
    selected_model: str = Field(..., min_length=1, max_length=128)
    provider: Optional[str] = Field(
        None,
        description="ADR-0181 provider id (anthropic/openai/ollama_local/ollama_cloud/"
                    "openrouter); null = native Anthropic",
    )
    alternatives: list[str] = Field(default_factory=list)


class EngineConfigRequest(BaseModel):
    """Full engine configuration update."""
    model_config = {"extra": "forbid"}

    models: dict[str, ModelConfigRequest]


class ExternalProviderTestRequest(BaseModel):
    model_config = {"extra": "forbid"}

    provider: str = Field(..., pattern="^(ollama_local|ollama_cloud|openrouter|openai|anthropic)$")


class ExternalProviderTestResponse(BaseModel):
    is_connected: bool
    latency_ms: Optional[float]
    error_message: Optional[str]
    model_count: int = 0


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────


def _tenant_id(rec: session_auth.SessionRecord) -> str:
    return rec.tenant_id or "_default"


def _model_config_module():
    """core.models.model_selection_config — imported lazily so a stripped
    install without ``core`` degrades to an HTTP 503 rather than a boot crash."""
    try:
        from core.models import model_selection_config as m  # noqa: PLC0415
        return m
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"model_selection_config unavailable: {exc}") from exc


def _real_stats(tenant_id: str) -> tuple[dict[str, dict[str, Any]], int, str | None]:
    """Aggregate REAL ``skill.model_selector.classified`` audit events into
    per-task-type {run_count, confidence_score} — never fabricated.

    Returns (stats_by_task_type, total_samples, last_ts_iso). An unreadable
    or absent chain is a normal, expected state (no turns yet) — returns all
    zeros, never raises.
    """
    from core.models.model_selection_config import COMPLEXITY_BY_TASK_TYPE  # noqa: PLC0415
    complexity_to_task = {v: k for k, v in COMPLEXITY_BY_TASK_TYPE.items()}
    sums: dict[str, float] = {t: 0.0 for t in TASK_TYPES}
    counts: dict[str, int] = {t: 0 for t in TASK_TYPES}
    last_ts: float | None = None

    try:
        from core.skills.skill_audit import audit_chain_path  # noqa: PLC0415
        path = audit_chain_path(tenant_id)
    except Exception:  # noqa: BLE001
        path = None

    if path is not None and path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    if rec.get("event_type") != "skill.model_selector.classified":
                        continue
                    details = rec.get("details") or {}
                    task_type = complexity_to_task.get(details.get("complexity"))
                    if task_type is None:
                        continue
                    confidence = details.get("confidence")
                    if isinstance(confidence, (int, float)):
                        sums[task_type] += float(confidence)
                        counts[task_type] += 1
                    ts = rec.get("ts")
                    if isinstance(ts, (int, float)):
                        last_ts = ts if last_ts is None else max(last_ts, ts)
        except OSError:
            pass

    stats: dict[str, dict[str, Any]] = {}
    for t in TASK_TYPES:
        n = counts[t]
        stats[t] = {
            "run_count": n,
            "confidence_score": round(sums[t] / n, 4) if n else 0.0,
        }
    total = sum(counts.values())
    last_iso = (
        datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat() if last_ts is not None else None
    )
    return stats, total, last_iso


# ─────────────────────────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────────────────────────


@router.get("/v1/engine/config")
async def get_engine_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Read current per-task-type config + REAL, LEARNED confidence.

    ``confidence_score``/``run_count`` per task type come from
    core.learning.model_selection_optimizer.ConfidenceOptimizer (ADR-0644,
    Bayesian + EMA) for the tier's CURRENTLY selected model — real outcome
    feedback closes this loop end to end (2026-09-10):
    operator/bridges/shared/model_selector_shadow.py classifies every real
    turn (SHADOW — never alters routing) and reports its real success/failure
    back into this same optimizer. 0 samples is the honest, expected state
    until real turns accrue; ``is_converged`` only turns true once the
    variance criterion (ADR-0644: n>=5, then <0.05 variance over the last 50
    samples) is genuinely met — never claimed early.
    """
    tenant_id = _tenant_id(rec)
    cfg_mod = _model_config_module()
    cfg = cfg_mod.load_config(tenant_id)
    _, total_classified, last_ts = _real_stats(tenant_id)

    from core.learning.model_selection_optimizer import get_optimizer
    optimizer = get_optimizer()

    models: dict[str, Any] = {}
    converged_count = 0
    learned_samples = 0
    for task_type in TASK_TYPES:
        entry = cfg.get(task_type) or {}
        model_id = entry.get("selected_model") or ""
        if model_id:
            model_stats = optimizer.get_stats(task_type, model_id, tenant_id)
            is_converged = optimizer.is_converged(task_type, model_id, tenant_id)
        else:
            model_stats = None
            is_converged = False
        n = model_stats.n_samples if model_stats else 0
        learned_samples += n
        if is_converged:
            converged_count += 1
        models[task_type] = {
            "task_type": task_type,
            "selected_model": entry.get("selected_model"),
            "provider": entry.get("provider"),
            "alternatives": entry.get("alternatives") or [],
            "confidence_score": round(model_stats.confidence_score, 4) if model_stats else 0.0,
            "run_count": n,
            "is_converged": is_converged,
        }

    return {
        "tenant_id": tenant_id,
        "models": models,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        # "idle" — no real turn classified yet. "learning" — real outcome
        # samples exist, not yet converged everywhere. "converged" — every
        # task type with a model assigned has met the real variance
        # criterion. Never fabricated; a fresh tenant genuinely starts idle.
        "learning_status": (
            "idle" if total_classified == 0
            else "converged"
            if (_tiers_with_model := sum(1 for t in TASK_TYPES if cfg.get(t, {}).get("selected_model")))
            and converged_count == _tiers_with_model
            else "learning"
        ),
        "last_learning_update": last_ts,
        "total_samples": total_classified,
        "total_learned_samples": learned_samples,
    }


@router.put("/v1/engine/config")
async def update_engine_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    config: EngineConfigRequest = Body(...),
) -> dict[str, Any]:
    """Persist model/provider choice per task type.

    Validates: task_type is one of the four known ones; a null/anthropic
    provider requires a known Claude model id; a non-anthropic provider is
    checked against the live ADR-0181 provider registry.
    """
    tenant_id = _tenant_id(rec)
    if not config.models:
        raise HTTPException(status_code=400, detail="models cannot be empty")

    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        known_providers = set(load_providers(force_reload=True).keys())
    except Exception:  # noqa: BLE001
        known_providers = set()

    to_save: dict[str, dict[str, Any]] = {}
    for task_type, model_cfg in config.models.items():
        if task_type not in TASK_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid task_type: {task_type}")
        if model_cfg.provider is None:
            if model_cfg.selected_model not in _VALID_ANTHROPIC_MODELS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown Anthropic model {model_cfg.selected_model!r} for {task_type}. "
                          f"Choose from: {list(_VALID_ANTHROPIC_MODELS)}",
                )
        elif known_providers and model_cfg.provider not in known_providers:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider {model_cfg.provider!r} for {task_type}. "
                      f"Choose from: {sorted(known_providers)}",
            )
        to_save[task_type] = {
            "selected_model": model_cfg.selected_model,
            "provider": model_cfg.provider,
            "alternatives": model_cfg.alternatives,
        }

    cfg_mod = _model_config_module()
    merged = cfg_mod.load_config(tenant_id)
    merged.update(to_save)
    cfg_mod.save_config(tenant_id, merged)

    try:
        from .. import audit as console_audit  # noqa: PLC0415
        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="model_selection.config_updated",
            target_kind="engine_config",
            target_id=",".join(sorted(to_save.keys())),
        )
    except Exception:  # noqa: BLE001
        pass

    return await get_engine_config(rec)


@router.post(
    "/v1/engine/external-provider/test",
    response_model=ExternalProviderTestResponse,
)
async def test_external_provider(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    test_req: ExternalProviderTestRequest = Body(...),
) -> dict[str, Any]:
    """Real connectivity test — reuses the same ADR-0181 live-fetch path the
    Engine Configuration (provider/model) endpoints use, so "Test Connection"
    reports the actual reachability + model count, not a canned success."""
    import time

    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        from engine_providers import fetch_models  # type: ignore[import]  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"is_connected": False, "latency_ms": None, "error_message": str(exc), "model_count": 0}

    spec = load_providers(force_reload=True).get(test_req.provider)
    if spec is None:
        return {
            "is_connected": False, "latency_ms": None,
            "error_message": f"unknown provider '{test_req.provider}'", "model_count": 0,
        }

    start = time.time()
    result = fetch_models(
        test_req.provider, base_url=spec.base_url,
        model_source=spec.model_source, credential_env=spec.credential_env,
    )
    latency_ms = (time.time() - start) * 1000
    return {
        "is_connected": bool(result.get("reachable")),
        "latency_ms": round(latency_ms, 1),
        "error_message": result.get("error"),
        "model_count": result.get("count", 0),
    }


@router.get("/v1/engine/health")
async def engine_health() -> dict[str, Any]:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
