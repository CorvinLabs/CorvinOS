"""
Model Selection Analytics API — Phase 3, Component 3.

REST API endpoints for model confidence scores, analytics, and learning control.

Endpoints:
  GET  /v1/engine/analytics              → confidence pie chart data (all models)
  GET  /v1/engine/analytics/task-type/{task_type}  → per-task-type breakdown
  GET  /v1/engine/analytics/model/{model_id}       → per-model history
  POST /v1/engine/analytics/reset         → reset learning (operator action)
  GET  /v1/engine/analytics/export        → CSV export of confidence weights
  GET  /v1/engine/analytics/recent        → newest shadow classifications (ADR-0885)
  POST /v1/engine/analytics/feedback      → operator rating → one learner sample (ADR-0885)

Auth: requires session (tenant isolation enforced via rec.tenant_id)
Audit: all actions logged

Constraint (ADR-0644):
- Confidence scores are REAL (no hardcoding)
- Per-tenant isolation: all queries filter by tenant_id
- Reset action audited + logged
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel

try:
    from core.learning.model_selection_optimizer import ConfidenceOptimizer, get_optimizer
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.learning.model_selection_optimizer import ConfidenceOptimizer, get_optimizer

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/engine/analytics", tags=["model-selection-analytics"])


# ── Pydantic models for responses ──────────────────────────────────────

class ConfidenceEntry(BaseModel):
    """Single model's confidence and stats.

    The four optional fields are filled by the per-task-type ranking
    (``ConfidenceOptimizer.rank_models``, ADR-0885): the raw Beta mean and the
    published rate card. A rate of ``null`` means "not on the rate card" and
    must be rendered as unknown, never as free."""
    model: str
    confidence: float
    n_samples: int
    mean_quality: float
    variance: float
    is_converged: bool
    posterior_mean: Optional[float] = None
    input_usd_per_1k: Optional[float] = None
    output_usd_per_1k: Optional[float] = None
    priced: Optional[bool] = None


class AnalyticsSummary(BaseModel):
    """Summary of all model confidences."""
    timestamp: str
    tenant_id: str
    total_samples: int
    models: List[ConfidenceEntry]
    top_model: Optional[str] = None
    top_confidence: Optional[float] = None


class TaskTypeBreakdown(BaseModel):
    """Per-task-type confidence breakdown."""
    task_type: str
    timestamp: str
    models: List[ConfidenceEntry]


class ModelHistory(BaseModel):
    """Model's confidence history."""
    model: str
    task_type: str
    samples: List[Dict[str, Any]]  # Recent samples with timestamps


class ResetResponse(BaseModel):
    """Response to reset action."""
    status: str
    message: str
    reset_at: str


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("", response_model=AnalyticsSummary)
async def get_analytics(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get all model confidences and analytics.

    Returns confidence scores across all models, sorted by confidence (descending).
    Real data from the learning backend.
    """
    optimizer = get_optimizer()
    tenant_id = rec.tenant_id

    try:
        from core.learning.confidence_persistence import list_entries

        models_data = []
        total_samples = 0

        # optimizer._stats_cache is process-local and only holds keys THIS
        # process has already asked about — real enumeration comes from the
        # persisted store (list_entries), which every process shares.
        for task_type, model in list_entries(tenant_id):
            stats = optimizer.get_stats(task_type, model, tenant_id)

            entry = ConfidenceEntry(
                model=model,
                confidence=stats.confidence_score,
                n_samples=stats.n_samples,
                mean_quality=stats.mean_quality,
                variance=stats.variance,
                is_converged=optimizer.is_converged(task_type, model, tenant_id),
            )
            models_data.append(entry)
            total_samples += stats.n_samples

        # Sort by confidence (descending)
        models_data.sort(key=lambda x: x.confidence, reverse=True)

        top_model = models_data[0].model if models_data else None
        top_confidence = models_data[0].confidence if models_data else None

        logger.info(f"Analytics retrieved for tenant {tenant_id}: {len(models_data)} models")

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "total_samples": total_samples,
            "models": models_data,
            "top_model": top_model,
            "top_confidence": top_confidence,
        }

    except Exception as e:
        logger.error(f"Failed to get analytics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get analytics")


@router.get("/task-type/{task_type}", response_model=TaskTypeBreakdown)
async def get_task_type_analytics(
    task_type: str,
    rec: session_auth.SessionRecord = Depends(require_session),
    max_output_usd_per_1k: Optional[float] = Query(
        None, ge=0.0,
        description="Keep only models whose published output rate is known and "
                    "at or under this cap (USD per 1k tokens). Unpriced models "
                    "are excluded under a cap, never treated as free.",
    ),
) -> Dict[str, Any]:
    """Get per-task-type confidence breakdown.

    Shows all models' confidences for a specific task type, ranked by
    ``ConfidenceOptimizer.rank_models`` (ADR-0885) — the ONE ranking the
    learning tab and the selector share, with the rate card attached.
    """
    optimizer = get_optimizer()
    tenant_id = rec.tenant_id

    try:
        models_data = []

        for row in optimizer.rank_models(
            task_type, None, tenant_id, max_output_usd_per_1k=max_output_usd_per_1k,
        ):
            stats = optimizer.get_stats(task_type, row.model, tenant_id)
            models_data.append(ConfidenceEntry(
                model=row.model,
                confidence=row.confidence,
                n_samples=row.n_samples,
                mean_quality=stats.mean_quality,
                variance=stats.variance,
                is_converged=row.is_converged,
                posterior_mean=row.posterior_mean,
                input_usd_per_1k=row.input_usd_per_1k,
                output_usd_per_1k=row.output_usd_per_1k,
                priced=row.priced,
            ))

        logger.info(f"Task-type analytics retrieved: {task_type}, {len(models_data)} models")

        return {
            "task_type": task_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "models": models_data,
        }

    except Exception as e:
        logger.error(f"Failed to get task-type analytics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get task-type analytics")


# ─────────────────────────────────────────────────────────────────
# ADR-0885 step 2b — recent shadow classifications + operator feedback
# ─────────────────────────────────────────────────────────────────

RECENT_SCAN_LINES = 50_000   # bounds the backwards chain walk (0.44 % density → ~220 hits)


class RecentClassification(BaseModel):
    """ONE shadow classification, as the Learning tab lists it. An explicit
    five-field projection — writer markers and every other detail stay out."""
    model_config = {"extra": "forbid"}
    record_hash: str
    ts: float
    task_type: str
    model: str
    confidence: Optional[float] = None


class RecentResponse(BaseModel):
    model_config = {"extra": "forbid"}
    tenant_id: str
    windowed: bool = False   # a LIST, not a total — the ADR-0760 epoch does not apply
    items: List[RecentClassification]


class FeedbackRequest(BaseModel):
    """``record_hash`` names one of the tenant's own classified records; the
    server derives task_type and model from it — never from the client.
    No free text is accepted (ADR-0613)."""
    model_config = {"extra": "forbid"}
    record_hash: str
    rating: str  # "good" | "poor"


class FeedbackResponse(BaseModel):
    model_config = {"extra": "forbid"}
    record_hash: str
    task_type: str
    model: str
    confidence: float
    n_samples: int
    is_converged: bool


def _project_classified(rec: Dict[str, Any]) -> Optional[RecentClassification]:
    from core.models.model_selection_config import COMPLEXITY_BY_TASK_TYPE  # noqa: PLC0415
    complexity_to_task = {v: k for k, v in COMPLEXITY_BY_TASK_TYPE.items()}
    details = rec.get("details") or {}
    task_type = complexity_to_task.get(details.get("complexity"))
    model = details.get("recommended_model")
    h = rec.get("hash")
    ts = rec.get("ts")
    if not task_type or not isinstance(model, str) or not model or not isinstance(h, str):
        return None
    if not isinstance(ts, (int, float)):
        return None
    conf = details.get("confidence")
    return RecentClassification(
        record_hash=h, ts=float(ts), task_type=task_type, model=model,
        confidence=float(conf) if isinstance(conf, (int, float)) else None,
    )


def _rated_path(tenant_id: str) -> Path:
    from core.learning import confidence_persistence as CP  # noqa: PLC0415
    return CP._corvin_home() / "tenants" / tenant_id / "global" / "model_feedback_rated.json"


def _load_rated(tenant_id: str) -> set:
    path = _rated_path(tenant_id)
    try:
        import json  # noqa: PLC0415
        data = json.loads(path.read_text("utf-8"))
        return set(data) if isinstance(data, list) else set()
    except FileNotFoundError:
        return set()
    except Exception:  # noqa: BLE001 — unreadable → treat as empty, never block the tab
        return set()


def _save_rated(tenant_id: str, rated: set) -> None:
    import json  # noqa: PLC0415,E401
    import os
    import tempfile
    path = _rated_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".rated.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sorted(rated), fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@router.get("/recent", response_model=RecentResponse)
async def get_recent_classifications(
    rec: session_auth.SessionRecord = Depends(require_session),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    """The tenant's newest ``skill.model_selector.classified`` records — what
    the Learning tab offers for rating. Pure read; walks the chain backwards
    under a line cap, never the whole file. Empty on a fresh chain, never a
    sample (ADR-0763)."""
    from .engine_api import _iter_classified  # noqa: PLC0415
    items: List[RecentClassification] = []
    for raw in _iter_classified(rec.tenant_id, newest_first=True, max_lines=RECENT_SCAN_LINES):
        row = _project_classified(raw)
        if row is None:
            continue
        items.append(row)
        if len(items) >= limit:
            break
    return {"tenant_id": rec.tenant_id, "windowed": False, "items": items}


@router.post("/feedback", response_model=FeedbackResponse)
async def post_feedback(
    body: FeedbackRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf_token: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Operator rating of ONE shadow classification → one learner sample.

    * 404 — the hash is not among the tenant's classified records within the
      scan cap (another tenant's hash is absent by construction: the chain
      file is per tenant) or is too old to rate.
    * 409 — already rated (replay guard, one operator sample per record).
    * 503 — the learner refused because the core chain did not commit
      (audit-first, ADR-0644 amendment); nothing was learned.
    The console attribution event is best-effort; the fail-closed record is
    the learner's ``confidence_updated``.
    """
    if body.rating not in ("good", "poor"):
        raise HTTPException(status_code=422, detail="rating must be 'good' or 'poor'")
    from .engine_api import _iter_classified  # noqa: PLC0415
    tenant_id = rec.tenant_id
    found: Optional[RecentClassification] = None
    for raw in _iter_classified(tenant_id, newest_first=True, max_lines=RECENT_SCAN_LINES):
        if raw.get("hash") == body.record_hash:
            found = _project_classified(raw)
            break
    if found is None:
        raise HTTPException(status_code=404, detail="no longer available to rate")

    rated = _load_rated(tenant_id)
    if body.record_hash in rated:
        raise HTTPException(status_code=409, detail="already rated")

    quality = 1.0 if body.rating == "good" else 0.0
    optimizer = get_optimizer()
    try:
        confidence, converged = optimizer.process_feedback(
            found.task_type, found.model, quality, tenant_id,
        )
    except RuntimeError as e:
        logger.error(f"feedback refused by the audit chain: {e}")
        raise HTTPException(status_code=503, detail="audit chain unavailable — not recorded")

    rated.add(body.record_hash)
    try:
        _save_rated(tenant_id, rated)
    except Exception as e:  # noqa: BLE001 — the sample IS learned and chained; log the guard failure
        logger.error(f"could not persist the rated-set: {e}")

    try:
        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="model_feedback",
            target_kind="model_selection",
            target_id=body.record_hash,
        )
    except Exception:  # noqa: BLE001 — attribution only
        pass

    stats = optimizer.get_stats(found.task_type, found.model, tenant_id)
    return {
        "record_hash": body.record_hash,
        "task_type": found.task_type,
        "model": found.model,
        "confidence": confidence,
        "n_samples": stats.n_samples,
        "is_converged": converged,
    }


@router.get("/model/{model_id}", response_model=ModelHistory)
async def get_model_history(
    model_id: str,
    task_type: str = Query("unknown"),
    limit: int = Query(100, ge=1, le=1000),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get model's confidence history for a task type.

    Returns recent samples (capped at limit).
    """
    optimizer = get_optimizer()
    tenant_id = rec.tenant_id

    try:
        key = (task_type, model_id, tenant_id)
        history = optimizer._confidence_history.get(key, [])

        # Return recent samples with timestamps (simplified)
        samples = [
            {
                "timestamp": (
                    datetime.now(timezone.utc).timestamp() - (len(history) - i) * 60
                ),
                "confidence": conf,
            }
            for i, conf in enumerate(history[-limit:])
        ]

        logger.info(f"Model history retrieved: {model_id}/{task_type}, {len(samples)} samples")

        return {
            "model": model_id,
            "task_type": task_type,
            "samples": samples,
        }

    except Exception as e:
        logger.error(f"Failed to get model history: {e}")
        raise HTTPException(status_code=500, detail="Failed to get model history")


@router.post("/reset", response_model=ResetResponse)
async def reset_learning(
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf_token: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Reset all learning data for this tenant.

    This is an operator-level action: deletes all confidence scores and
    sample history, starting fresh. Audit-logged.

    WARNING: This cannot be undone. Use only for debugging or recalibration.
    """
    tenant_id = rec.tenant_id

    try:
        # Attribute the reset action. console_audit is best-effort by design
        # (audit.py swallows every writer exception except AuditFieldNotAllowed),
        # so this is attribution, NOT a fail-closed record — the fail-closed
        # record of learning is the learner's own confidence_updated (ADR-0644).
        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="reset_learning",
            target_kind="model_selection",
            target_id="all",
        )
    except Exception as e:
        logger.error(f"Failed to audit reset: {e}")
        raise HTTPException(status_code=500, detail="Failed to audit reset action")

    # Reset optimizer
    optimizer = get_optimizer()
    try:
        optimizer.reset_learning(tenant_id=tenant_id)
        logger.info(f"Learning reset for tenant {tenant_id}")
    except Exception as e:
        logger.error(f"Failed to reset learning: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset learning")

    return {
        "status": "success",
        "message": f"Learning reset for tenant {tenant_id}",
        "reset_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/export")
async def export_weights(
    rec: session_auth.SessionRecord = Depends(require_session),
    format: str = Query("csv", regex="^(csv|json)$"),
) -> Dict[str, Any]:
    """Export confidence weights as CSV or JSON.

    Useful for analysis, debugging, and external tools.
    """
    optimizer = get_optimizer()
    tenant_id = rec.tenant_id

    try:
        lines = []
        if format == "csv":
            # CSV header
            lines.append("task_type,model,confidence,n_samples,mean_quality,variance,converged")

            for (task_type, model, tid), stats in optimizer._stats_cache.items():
                if tid != tenant_id:
                    continue
                if stats.n_samples == 0:
                    # The uniform prior _load_stats caches on a miss (also what
                    # a refused audit-first write leaves behind) — not learned.
                    continue

                is_converged = optimizer.is_converged(task_type, model, tenant_id)
                lines.append(
                    f"{task_type},{model},"
                    f"{stats.confidence_score:.4f},"
                    f"{stats.n_samples},"
                    f"{stats.mean_quality:.4f},"
                    f"{stats.variance:.6f},"
                    f"{is_converged}"
                )

            content = "\n".join(lines)
            content_type = "text/csv"
            filename = f"model-weights-{tenant_id}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"

        else:  # json
            data = {}
            for (task_type, model, tid), stats in optimizer._stats_cache.items():
                if tid != tenant_id:
                    continue
                if stats.n_samples == 0:
                    # The uniform prior _load_stats caches on a miss (also what
                    # a refused audit-first write leaves behind) — not learned.
                    continue

                key = f"{task_type}/{model}"
                data[key] = {
                    "confidence": stats.confidence_score,
                    "n_samples": stats.n_samples,
                    "mean_quality": stats.mean_quality,
                    "variance": stats.variance,
                    "converged": optimizer.is_converged(task_type, model, tenant_id),
                }

            import json
            content = json.dumps(data, indent=2)
            content_type = "application/json"
            filename = f"model-weights-{tenant_id}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"

        logger.info(f"Weights exported for tenant {tenant_id} as {format}")

        return {
            "content": content,
            "content_type": content_type,
            "filename": filename,
        }

    except Exception as e:
        logger.error(f"Failed to export weights: {e}")
        raise HTTPException(status_code=500, detail="Failed to export weights")


class HaikuTaskTypeStats(BaseModel):
    """Haiku success statistics for a task type."""
    task_type: str
    success_rate: float
    n_samples: int
    confidence_interval: Optional[List[float]] = None
    converged: bool
    model: str = "claude-haiku-4-5"


class HaikuStatsResponse(BaseModel):
    """Response with Haiku success stats by task type."""
    timestamp: str
    tenant_id: str
    task_types: List[HaikuTaskTypeStats]


@router.get("/haiku-stats", response_model=HaikuStatsResponse)
async def get_haiku_stats(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get Haiku success statistics by task type.

    Returns real learned success rates from the learning store (ADR-0314).
    Fallback to hardcoded defaults if learning data is not available.
    Tenant-scoped query (GDPR Art. 5).

    Returns:
        HaikuStatsResponse with per-task-type Haiku success rates and confidence intervals
    """
    tenant_id = rec.tenant_id

    try:
        from core.skills.os_skills.model_selector import ModelSelector

        selector = ModelSelector()
        stats = selector.get_haiku_stats_by_task_type(tenant_id=tenant_id)

        task_types_data = []
        for task_type, stat in stats.items():
            entry = HaikuTaskTypeStats(
                task_type=task_type,
                success_rate=stat["success_rate"],
                n_samples=stat["n_samples"],
                confidence_interval=stat["confidence_interval"],
                converged=stat["converged"],
                model=stat["model"],
            )
            task_types_data.append(entry)

        # Sort by success rate (descending)
        task_types_data.sort(key=lambda x: x.success_rate, reverse=True)

        logger.info(
            f"Haiku stats retrieved for tenant {tenant_id}: "
            f"{len(task_types_data)} task types"
        )

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "task_types": task_types_data,
        }

    except Exception as e:
        logger.error(f"Failed to get Haiku stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get Haiku stats")
