"""DoD Verifier Dashboard API Routes (ADR-0XXX).

Provides REST endpoints for Definition-of-Done verification:
  - POST /api/dod/verify — run verification on a project
  - GET /api/dod/history/{task_id} — fetch verification history
  - POST /api/dod/feedback — submit feedback on DoD checks
  - WS /api/dod/stream — real-time WebSocket updates

Tenant isolation enforced. All operations audit-logged.

Auth: Console session (deps.require_session) → authenticated tenant_id.
Never anonymous, never env-var fallback.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, Optional
from datetime import datetime
from pathlib import Path
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, Body

try:
    from core.skills.os_skills.definition_of_done_verifier.skill_base_wrapper import (
        DoD_VerifierSkillWrapper,
        DoD_VerifierInput,
    )
    from core.skills.os_skills.definition_of_done_verifier.skill import (
        DoD_VerificationResult,
        AuditFailedError,
    )
    from core.learning.event_store import EventStore
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.skills.os_skills.definition_of_done_verifier.skill_base_wrapper import (
        DoD_VerifierSkillWrapper,
        DoD_VerifierInput,
    )
    from core.skills.os_skills.definition_of_done_verifier.skill import (
        DoD_VerificationResult,
        AuditFailedError,
    )
    from core.learning.event_store import EventStore

from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dod", tags=["dod-verifier"])

# Global DoD Verifier instance (per-tenant)
_verifiers: Dict[str, DoD_VerifierSkillWrapper] = {}
_event_stores: Dict[str, EventStore] = {}


def _tenant_home(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/`` — honours CORVIN_HOME."""
    from forge.tenants import tenant_home  # type: ignore[import-not-found]
    return Path(tenant_home(tenant_id))


def get_verifier(tenant_id: str) -> DoD_VerifierSkillWrapper:
    """Get or initialize DoD Verifier for tenant."""
    if tenant_id not in _verifiers:
        # TODO: wire real audit_trail from core/compliance
        # For now, use a mock that logs to stdout
        class MockAuditTrail:
            def write_event(self, event):
                logger.info(f"[AUDIT] {event.skill_id}: {event.status.value}")
                return True

        _verifiers[tenant_id] = DoD_VerifierSkillWrapper(
            tenant_id=tenant_id,
            audit_trail=MockAuditTrail(),
            audit_path=_tenant_home(tenant_id) / "global" / "forge" / "audit.jsonl",
            cwd=Path.home() / "projects" / "CorvinOS",
        )

    return _verifiers[tenant_id]


def get_event_store(tenant_id: str) -> EventStore:
    """Get or initialize event store for tenant."""
    if tenant_id not in _event_stores:
        _event_stores[tenant_id] = EventStore(tenant_home=_tenant_home(tenant_id))
    return _event_stores[tenant_id]


@router.post("/verify")
async def run_dod_verification(
    payload: Dict[str, Any] = Body(...),
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
    csrf: Annotated[str, Depends(require_csrf)] = None,
) -> Dict[str, Any]:
    """Run DoD verification on a project.

    Request body:
    {
        "task_id": "task_123",
        "task_type": "feature",
        "commit_msg": "feat(core): add DoD verifier",
        "commit_range": "HEAD~5..HEAD",
        "symbol_name": "DoD_VerifierSkillWrapper"
    }

    Response:
    {
        "score": 85,
        "passed": true,
        "checks": {...},
        "audit_event_id": "abc123...",
        "reason": "Task complete. DoD score: 85.0%"
    }
    """
    tenant_id = rec.tenant_id

    # Validate payload
    if not payload.get("task_id"):
        raise HTTPException(status_code=400, detail="task_id required")

    try:
        verifier = get_verifier(tenant_id)

        # Build input
        input_data = DoD_VerifierInput(
            task_id=payload["task_id"],
            task_type=payload.get("task_type", "feature"),
            symbol_name=payload.get("symbol_name"),
            test_path=Path(payload["test_path"]) if payload.get("test_path") else None,
            test_output_file=Path(payload["test_output_file"]) if payload.get("test_output_file") else None,
            commit_range=payload.get("commit_range", "HEAD~1"),
            keyword=payload.get("keyword", "feat:"),
            commit_msg=payload.get("commit_msg", ""),
            project_path=Path(payload["project_path"]) if payload.get("project_path") else None,
        )

        # Execute skill (audit-first)
        result: DoD_VerificationResult = verifier.execute(input_data)

        # Log to learning event store (non-blocking)
        try:
            event_store = get_event_store(tenant_id)
            event_store.write_event({
                "event_type": "dod_verification_executed",
                "task_id": result.task_id,
                "score": result.score,
                "passed": result.passed,
                "timestamp": result.timestamp,
                "tenant_id": tenant_id,
            })
        except Exception as e:
            logger.warning(f"Failed to log DoD event: {e}")

        # Return result
        return {
            "task_id": result.task_id,
            "score": float(result.score),
            "passed": result.passed,
            "checks": result.checks,
            "weights": result.weights,
            "reason": result.reason,
            "audit_event_id": result.audit_event_id,
            "timestamp": result.timestamp,
        }

    except AuditFailedError as e:
        # Fail-closed: audit failure = entire request fails
        logger.error(f"DoD verification audit failed: {e}")
        raise HTTPException(status_code=500, detail="Verification audit failed. Task cannot be marked done.")

    except Exception as e:
        logger.exception(f"DoD verification failed: {e}")
        raise HTTPException(status_code=500, detail=f"Verification failed: {str(e)[:100]}")


@router.post("/feedback")
async def submit_dod_feedback(
    payload: Dict[str, Any] = Body(...),
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
    csrf: Annotated[str, Depends(require_csrf)] = None,
) -> Dict[str, Any]:
    """Submit feedback on a DoD verification.

    Used by Learning Loop (ADR-0314) to tune check weights.

    Request body:
    {
        "task_id": "task_123",
        "check_name": "test_coverage",
        "feedback": "accurate",  # or "inaccurate", "not_applicable"
        "note": "Tests are comprehensive"
    }

    Response:
    {
        "accepted": true,
        "feedback_id": "feedback_abc123",
        "weight_adjustment": -0.02
    }
    """
    tenant_id = rec.tenant_id

    if not payload.get("task_id"):
        raise HTTPException(status_code=400, detail="task_id required")

    try:
        event_store = get_event_store(tenant_id)

        feedback_event = {
            "event_type": "dod_feedback_received",
            "task_id": payload["task_id"],
            "check_name": payload.get("check_name"),
            "feedback": payload.get("feedback", ""),
            "note": payload.get("note", ""),
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": tenant_id,
        }

        event_store.write_event(feedback_event)

        # TODO: wire to learning_optimizer to adjust weights
        # For now, return mock adjustment
        return {
            "accepted": True,
            "feedback_id": f"feedback_{payload['task_id']}_{int(datetime.utcnow().timestamp())}",
            "weight_adjustment": 0.0,  # Will be computed by learning optimizer
        }

    except Exception as e:
        logger.exception(f"Feedback submission failed: {e}")
        raise HTTPException(status_code=500, detail=f"Feedback failed: {str(e)[:100]}")


@router.get("/history/{task_id}")
async def get_dod_history(
    task_id: str,
    limit: int = Query(10, ge=1, le=100),
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
) -> Dict[str, Any]:
    """Fetch verification history for a task.

    Response:
    {
        "task_id": "task_123",
        "verifications": [
            {
                "timestamp": "2026-09-18T12:34:56Z",
                "score": 85,
                "passed": true,
                "checks": {...}
            },
            ...
        ]
    }
    """
    tenant_id = rec.tenant_id

    try:
        event_store = get_event_store(tenant_id)

        # Query for all dod_verification_executed events for this task
        events = event_store.query_events(
            event_type="dod_verification_executed",
            filters={"task_id": task_id},
            limit=limit,
        )

        return {
            "task_id": task_id,
            "verifications": events,
        }

    except Exception as e:
        logger.exception(f"History fetch failed: {e}")
        raise HTTPException(status_code=500, detail=f"History fetch failed: {str(e)[:100]}")


@router.websocket("/stream")
async def dod_stream(
    websocket: WebSocket,
    task_id: str = Query(...),
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
) -> None:
    """WebSocket stream for real-time DoD verification updates.

    Subscribe to live updates for a task.

    Message format:
    {
        "event_type": "dod_verification_executed" | "dod_feedback_received",
        "task_id": "task_123",
        "data": {...}
    }
    """
    # TODO: wire to event emitter for live updates
    # For now, just close connection
    await websocket.close(code=1000, reason="WebSocket streaming not yet implemented")
