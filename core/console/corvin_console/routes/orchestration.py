"""Orchestration routes — Phase status, approval gates, monitoring dashboard.

Remediation Cycle 2: End-to-end orchestration state management + approval wiring.

Routes:
- GET /v1/console/orchestration/status — Phase status + blocking reasons + metrics
- POST /v1/console/orchestration/approve — Approve phase transition
- GET /v1/console/orchestration/history — Phase transition history

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — the
console app does not mount this router, and the SPA pages that fetch it are
themselves unreachable (they render "not available on this build" on 404).

Defused 2026-09-27. The module did not import (``corvin_console.router`` and
``corvin_console.auth.require_session`` do not exist, and ``require_session``
was used as a plain default value, not ``Depends``). Its data was invented:
the "phase state" file is written by nothing but ``/approve`` itself, so
``/status`` reported ``outcome_count: 0, avg_confidence: 0.0`` as measured
metrics; ``/approve`` had no CSRF, changed state BEFORE auditing and treated
an audit failure as non-fatal; no history was ever written. Now: session on
every route, CSRF on ``/approve``; ``/status`` and ``/approve`` answer 501
``not_implemented`` (there is no real phase source to report or advance);
``/history`` returns what is on disk (empty on every install).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field

from ..auth import SessionRecord
from ..deps import require_csrf, require_session

router = APIRouter()
Session = Annotated[SessionRecord, Depends(require_session)]
Mutation = Annotated[SessionRecord, Depends(require_csrf)]

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


def _not_implemented() -> HTTPException:
    return HTTPException(
        status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
        detail={"status": "not_implemented",
                "reason": "no orchestration phase source exists on this build"},
    )


@router.get("/orchestration/status", response_model=PhaseStatus)
def get_orchestration_status(rec: Session) -> PhaseStatus:
    """NOT IMPLEMENTED (501): no phase/metrics source to report from."""
    raise _not_implemented()


@router.post("/orchestration/approve", response_model=dict[str, Any])
def approve_orchestration(req: ApprovalRequest, rec: Mutation) -> dict[str, Any]:
    """NOT IMPLEMENTED (501): approving a phase nothing tracks would record a
    decision with no subject."""
    raise _not_implemented()


@router.get("/orchestration/history")
def get_orchestration_history(
    rec: Session, limit: int = Query(default=100, ge=1, le=1000)
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
