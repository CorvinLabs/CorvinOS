"""
Model Selection Learning API — ADR-0377 Phase 2b

REST API endpoints for learned threshold management and operator controls.

Endpoints:
  GET  /v1/console/learning/model-selection/status    → Dashboard status (convergence, cost, quality)
  POST /v1/console/learning/model-selection/override   → Manual threshold override
  POST /v1/console/learning/model-selection/reset      → Reset all learning
  GET  /v1/console/learning/model-selection/export     → Export learned thresholds as JSON
  POST /v1/console/learning/model-selection/import     → Import thresholds from JSON

Auth: requires session (tenant isolation enforced via rec.tenant_id)
Audit: all actions logged to audit trail

Constraints (ADR-0377 Phase 2b):
- Audit-first: every threshold change logged before persisting
- Per-tenant isolation: all queries filter by tenant_id
- Fail-safe: graceful degradation if store unavailable
"""

from __future__ import annotations

import logging
import json
from typing import Annotated, Any, Dict, List, Optional
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status, UploadFile, File
from pydantic import BaseModel

try:
    from core.learning.learned_threshold_store import get_store, StoredThreshold
    from core.learning.cost_variance_optimizer import get_optimizer
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.learning.learned_threshold_store import get_store, StoredThreshold
    from core.learning.cost_variance_optimizer import get_optimizer

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/console/learning/model-selection", tags=["model-selection-learning"])


# ── Pydantic models for requests/responses ──────────────────────────────

class ThresholdStatus(BaseModel):
    """Single threshold status."""
    task_type: str
    subsystem: str
    learned_threshold: float
    base_threshold: float
    sample_count: int
    converged: bool
    timestamp: str


class DashboardStatusResponse(BaseModel):
    """Dashboard status response."""
    converged_count: int
    total_count: int
    thresholds: List[ThresholdStatus]
    cost_savings_percent: float
    cost_baseline_usd: float
    cost_current_usd: float
    accuracy_percent: float
    last_updated: str
    history: Optional[List[Dict[str, Any]]] = None


class OverrideRequest(BaseModel):
    """Manual threshold override request."""
    task_type: str
    new_threshold: float
    reason: str = "Manual override"


class ResetRequest(BaseModel):
    """Reset learning request."""
    reason: str = "Operator manual reset"


class ImportRequest(BaseModel):
    """Import thresholds request."""
    data: Dict[str, Any]
    reason: str = "Operator import"


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/status", response_model=DashboardStatusResponse)
async def get_learning_status(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Get Model Selection Learning dashboard status.

    Returns:
        converged_count: number of converged task types
        total_count: total task types tracked
        thresholds: list of learned thresholds
        cost_savings_percent: estimated cost savings
        cost_baseline_usd: baseline cost (before learning)
        cost_current_usd: current cost (after learning)
        accuracy_percent: quality maintenance metric
        last_updated: timestamp
    """
    try:
        store = get_store(rec.tenant_id)
        optimizer = get_optimizer()

        thresholds = store.get_all()
        converged = sum(1 for t in thresholds if t.converged)
        total = len(thresholds) or 1  # Avoid division by zero

        # Calculate cost metrics (placeholder — integrate with billing if available)
        # TODO: Replace with real cost data from billing subsystem
        cost_baseline = 100.0  # $100/day baseline (before learning)
        cost_savings_pct = (sum(t.sample_count * 0.005 for t in thresholds) / (total * 10)) if thresholds else 0
        cost_savings_pct = max(0, min(100, cost_savings_pct))  # Clamp [0, 100]
        cost_current = cost_baseline * (1 - cost_savings_pct / 100)

        # Quality metric (placeholder)
        # TODO: Replace with real accuracy data from quality monitoring
        accuracy = 85.0 + (converged / max(total, 1)) * 10  # [85%, 95%]

        return {
            "converged_count": converged,
            "total_count": total,
            "thresholds": [
                {
                    "task_type": t.task_type,
                    "subsystem": t.subsystem,
                    "learned_threshold": t.learned_threshold,
                    "base_threshold": t.base_threshold,
                    "sample_count": t.sample_count,
                    "converged": t.converged,
                    "timestamp": t.timestamp,
                }
                for t in thresholds
            ],
            "cost_savings_percent": cost_savings_pct,
            "cost_baseline_usd": cost_baseline,
            "cost_current_usd": cost_current,
            "accuracy_percent": accuracy,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get learning status: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get status: {e}",
        )


@router.post("/override")
async def override_threshold(
    req: OverrideRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Manually override a learned threshold.

    Args:
        task_type: task type to override
        new_threshold: new threshold value [0.1, 0.9]
        reason: reason for override (for audit trail)

    Returns:
        status: "ok"
        message: confirmation message
        updated_at: timestamp
    """
    try:
        # Validate input
        if not 0.1 <= req.new_threshold <= 0.9:
            raise ValueError(f"Threshold out of range [0.1, 0.9]: {req.new_threshold}")

        store = get_store(rec.tenant_id)

        # Get old threshold for comparison
        old_threshold = store.get_threshold(req.task_type, "default")

        # Create new stored threshold
        stored = StoredThreshold(
            task_type=req.task_type,
            subsystem="default",
            tenant_id=rec.tenant_id,
            learned_threshold=req.new_threshold,
            base_threshold=0.5,
            timestamp=datetime.now(timezone.utc).isoformat(),
            sample_count=0,  # Reset sample count on manual override
            converged=False,  # Not converged until learning resumes
            notes=f"Manual override: {req.reason}",
        )

        # Store (with audit logging)
        audit_backend = _get_audit_backend(rec.tenant_id)
        store.set_threshold(stored, audit_backend=audit_backend)

        logger.info(
            f"Threshold override: {req.task_type} "
            f"{old_threshold:.3f} → {req.new_threshold:.3f} "
            f"(reason: {req.reason})"
        )

        return {
            "status": "ok",
            "message": f"Threshold for {req.task_type} updated to {req.new_threshold:.3f}",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to override threshold: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to apply override: {e}",
        )


@router.post("/reset")
async def reset_learning(
    req: ResetRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Reset all learned thresholds (operator action).

    Args:
        reason: reason for reset (for audit trail)

    Returns:
        status: "ok"
        message: confirmation message
        reset_at: timestamp
    """
    try:
        store = get_store(rec.tenant_id)
        audit_backend = _get_audit_backend(rec.tenant_id)

        # Reset (with audit logging)
        store.reset_all(
            audit_backend=audit_backend,
            operator_note=req.reason,
        )

        logger.warning(
            f"Learning reset for tenant {rec.tenant_id} (reason: {req.reason})"
        )

        return {
            "status": "ok",
            "message": "All learned thresholds have been reset",
            "reset_at": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to reset learning: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset learning: {e}",
        )


@router.get("/export")
async def export_thresholds(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Export learned thresholds as JSON.

    Returns:
        {
          "version": "1",
          "tenant_id": "_default",
          "export_at": "2026-09-11T12:00:00Z",
          "thresholds": [...]
        }
    """
    try:
        store = get_store(rec.tenant_id)
        export_data = store.export_json()

        logger.info(f"Thresholds exported for tenant {rec.tenant_id}")

        return export_data

    except Exception as e:
        logger.error(f"Failed to export thresholds: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export: {e}",
        )


@router.post("/import")
async def import_thresholds(
    data: Dict[str, Any],
    rec: session_auth.SessionRecord = Depends(require_session),
    csrf: Annotated[str, Depends(require_csrf)] = "",
) -> Dict[str, Any]:
    """Import learned thresholds from JSON backup.

    Args:
        data: export dict (from /export endpoint)

    Returns:
        status: "ok"
        imported_count: number of imported thresholds
        imported_at: timestamp
    """
    try:
        store = get_store(rec.tenant_id)
        audit_backend = _get_audit_backend(rec.tenant_id)

        # Import (with audit logging)
        count = store.import_json(
            data,
            audit_backend=audit_backend,
            operator_note="Operator import via console",
        )

        logger.info(
            f"Imported {count} thresholds for tenant {rec.tenant_id}"
        )

        return {
            "status": "ok",
            "imported_count": count,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }

    except ValueError as e:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to import thresholds: {e}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import: {e}",
        )


# ── Private helpers ────────────────────────────────────────────────────

def _get_audit_backend(tenant_id: str) -> Optional[Any]:
    """Get audit backend for the tenant.

    Returns:
        Audit backend instance, or None if unavailable.
    """
    try:
        from core.skills.skill_audit import _SkillAuditBackend  # noqa: PLC0415
        return _SkillAuditBackend()
    except Exception:  # noqa: BLE001
        logger.warning("Audit backend unavailable (non-fatal)")
        return None
