"""Orchestration routes — Phase status, approval gates, monitoring dashboard.

Remediation Cycle 2: End-to-end orchestration state management + approval wiring.

Routes:
- GET /v1/console/orchestration/status — Phase status + blocking reasons + metrics
- POST /v1/console/orchestration/approve — Approve phase transition
- GET /v1/console/orchestration/history — Phase transition history
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Query, status as http_status
from pydantic import BaseModel, Field

from corvin_console.auth import SessionRecord, require_session
from corvin_console.router import router

_log = logging.getLogger(__name__)


class MetricsValidation(BaseModel):
    """Metrics validation result."""

    outcome_count: int = Field(ge=0)
    avg_confidence: float = Field(ge=0.0, le=1.0)
    is_valid: bool
    errors: list[str] = Field(default_factory=list)


class BlockingReason(BaseModel):
    """Reason phase transition is blocked."""

    phase: str
    reason: str
    detail: str
    severity: str = Field(default="error")  # error, warning, info


class PhaseStatus(BaseModel):
    """Current orchestration phase status."""

    current: str
    previous: str
    approval_status: str  # pending, approved, rejected, blocked
    blocking_reasons: list[BlockingReason] = Field(default_factory=list)
    metrics: MetricsValidation
    audit_chain_valid: bool = True
    last_updated: str


class ApprovalRequest(BaseModel):
    """Request to approve phase transition."""

    phase: str
    reason: str = Field(default="")
    timestamp: Optional[str] = None


@router.get("/orchestration/status", response_model=PhaseStatus)
def get_orchestration_status(rec: SessionRecord = require_session) -> PhaseStatus:
    """GET /v1/console/orchestration/status — Current phase + approval + metrics.

    Returns:
        PhaseStatus with current phase, approval status, blocking reasons, metrics validation
    """
    try:
        # Load or initialize phase state
        phase = _load_phase_state(rec.tenant_id)

        # Validate metrics
        metrics = _validate_metrics(phase.get("outcome_count", 0), phase.get("avg_confidence", 0.0))

        # Check audit chain
        audit_valid = _verify_audit_chain(rec.tenant_id)

        # Collect blocking reasons
        blocking = []
        if metrics.outcome_count < 0:
            blocking.append(
                BlockingReason(
                    phase=phase.get("current", "PHASE_1"),
                    reason="metrics out of bounds",
                    detail="outcome_count < 0",
                    severity="error",
                )
            )
        if not (0.0 <= metrics.avg_confidence <= 1.0):
            blocking.append(
                BlockingReason(
                    phase=phase.get("current", "PHASE_1"),
                    reason="metrics out of bounds",
                    detail=f"avg_confidence out of bounds ({metrics.avg_confidence})",
                    severity="error",
                )
            )
        if not audit_valid:
            blocking.append(
                BlockingReason(
                    phase=phase.get("current", "PHASE_1"),
                    reason="audit chain broken",
                    detail="hash mismatch detected",
                    severity="error",
                )
            )

        return PhaseStatus(
            current=phase.get("current", "PHASE_1"),
            previous=phase.get("previous", "INIT"),
            approval_status="blocked" if blocking else phase.get("approval_status", "pending"),
            blocking_reasons=blocking,
            metrics=metrics,
            audit_chain_valid=audit_valid,
            last_updated=datetime.now(tz=timezone.utc).isoformat(),
        )
    except Exception as e:
        _log.error("Failed to get orchestration status: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Could not fetch orchestration status") from e


@router.post("/orchestration/approve", response_model=dict[str, Any])
def approve_orchestration(
    req: ApprovalRequest, rec: SessionRecord = require_session
) -> dict[str, Any]:
    """POST /v1/console/orchestration/approve — Approve phase transition.

    Args:
        req: ApprovalRequest with phase and reason
        rec: SessionRecord (from auth)

    Returns:
        {"success": true, "phase": "...", "timestamp": "..."}

    Raises:
        400: Invalid phase
        409: Phase already approved
        503: Orchestration blocked (metrics invalid, audit chain broken)
    """
    try:
        # Validate phase
        valid_phases = {"PHASE_1_TO_2A", "PHASE_2_TO_2B", "PHASE_2B_TO_3", "REJECT_PHASE_1"}
        if req.phase not in valid_phases:
            raise HTTPException(status_code=400, detail=f"Invalid phase: {req.phase}")

        # Load current state
        phase_state = _load_phase_state(rec.tenant_id)

        # Check if already approved
        if phase_state.get("approval_status") == "approved":
            raise HTTPException(status_code=409, detail="Phase already approved")

        # Check metrics
        metrics = _validate_metrics(
            phase_state.get("outcome_count", 0), phase_state.get("avg_confidence", 0.0)
        )
        if not metrics.is_valid:
            raise HTTPException(status_code=503, detail="Orchestration blocked: invalid metrics")

        # Check audit chain
        if not _verify_audit_chain(rec.tenant_id):
            raise HTTPException(status_code=503, detail="Orchestration blocked: audit chain broken")

        # Update state
        phase_state["previous"] = phase_state.get("current", "PHASE_1")
        phase_state["current"] = req.phase
        phase_state["approval_status"] = "approved" if req.phase != "REJECT_PHASE_1" else "rejected"
        phase_state["approved_at"] = datetime.now(tz=timezone.utc).isoformat()
        phase_state["approved_by"] = rec.sid_fingerprint
        phase_state["approval_reason"] = req.reason

        _save_phase_state(rec.tenant_id, phase_state)

        # Audit event
        _write_audit_event(
            tenant_id=rec.tenant_id,
            event_type="orchestration.phase_approved",
            details={
                "phase": req.phase,
                "reason": req.reason,
                "approved_by": rec.sid_fingerprint,
            },
        )

        return {
            "success": True,
            "phase": req.phase,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "approval_status": phase_state["approval_status"],
        }
    except HTTPException:
        raise
    except Exception as e:
        _log.error("Failed to approve orchestration: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Approval failed") from e


@router.get("/orchestration/history")
def get_orchestration_history(
    rec: SessionRecord = require_session, limit: int = Query(default=100, ge=1, le=1000)
) -> dict[str, Any]:
    """GET /v1/console/orchestration/history — Phase transition history.

    Args:
        rec: SessionRecord (from auth)
        limit: Maximum number of history entries to return

    Returns:
        {"history": [...]} with phase transitions
    """
    try:
        history = _load_phase_history(rec.tenant_id, limit=limit)
        return {"history": history}
    except Exception as e:
        _log.error("Failed to get orchestration history: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Could not fetch history") from e


# ── Helper functions ──────────────────────────────────────────────────────


def _load_phase_state(tenant_id: str) -> dict[str, Any]:
    """Load phase state from disk."""
    from forge import paths

    state_path = paths.tenant_home(tenant_id) / "global" / "orchestration" / "phase_state.json"
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {
        "current": "PHASE_1",
        "previous": "INIT",
        "approval_status": "pending",
        "outcome_count": 0,
        "avg_confidence": 0.0,
    }


def _save_phase_state(tenant_id: str, state: dict[str, Any]) -> None:
    """Save phase state to disk."""
    from forge import paths

    state_path = paths.tenant_home(tenant_id) / "global" / "orchestration" / "phase_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _load_phase_history(tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Load phase transition history from disk."""
    from forge import paths

    history_path = paths.tenant_home(tenant_id) / "global" / "orchestration" / "phase_history.jsonl"
    if not history_path.exists():
        return []

    history = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    return history[-limit:]


def _validate_metrics(outcome_count: int, avg_confidence: float) -> MetricsValidation:
    """Validate metrics (bounds + NaN checks)."""
    errors = []

    if outcome_count < 0:
        errors.append("outcome_count < 0")

    try:
        if avg_confidence != avg_confidence:  # NaN check
            errors.append("avg_confidence is NaN")
    except TypeError:
        errors.append("avg_confidence is not a float")

    if not (0.0 <= avg_confidence <= 1.0):
        errors.append(f"avg_confidence out of bounds ({avg_confidence})")

    return MetricsValidation(
        outcome_count=outcome_count,
        avg_confidence=avg_confidence,
        is_valid=len(errors) == 0,
        errors=errors,
    )


def _verify_audit_chain(tenant_id: str) -> bool:
    """Verify audit chain integrity (hash-chain check)."""
    try:
        from forge.security_events import verify_audit_chain
        from forge.paths import tenant_audit_chain

        chain_path = tenant_audit_chain(tenant_id)
        if not chain_path.exists():
            return True  # No chain yet is OK

        # Verify chain integrity
        result = verify_audit_chain(chain_path)
        return result.get("valid", False)
    except Exception as e:
        _log.warning("Audit chain verification failed: %s", type(e).__name__)
        return False


def _write_audit_event(tenant_id: str, event_type: str, details: dict[str, Any]) -> None:
    """Write audit event for orchestration action."""
    try:
        from forge.security_events import write_event
        from forge.paths import tenant_audit_chain

        write_event(
            tenant_audit_chain(tenant_id),
            event_type,
            tool="orchestration",
            details={**details, "tenant_id": tenant_id},
        )
    except Exception as e:
        _log.error("Failed to write audit event: %s", type(e).__name__)
        # Non-fatal: don't raise, but log
