"""DoD Verifier API routes.

Provides REST endpoints for Definition-of-Done verification:
  - POST /api/dod/verify — run verification on a project
  - GET /api/dod/history/{task_id} — fetch verification history
  - POST /api/dod/feedback — submit feedback on DoD checks

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

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Body

try:
    from core.skills.os_skills.definition_of_done_verifier.skill_base_wrapper import (
        DoD_VerifierSkillWrapper,
        DoD_VerifierInput,
    )
    from core.skills.os_skills.definition_of_done_verifier.skill import (
        REPO_ROOT,
        _default_audit_path,
        DoD_VerificationResult,
        AuditFailedError,
    )
    from core.learning.event_store import EventStore
    from core.skills.os_skills.audit_integration import emit_skill_executed_event
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
        REPO_ROOT,
        _default_audit_path,
        DoD_VerificationResult,
        AuditFailedError,
    )
    from core.learning.event_store import EventStore
    from core.skills.os_skills.audit_integration import emit_skill_executed_event

from core.learning.learning_events import EventType, LearningEvent

from .. import auth as session_auth
from ..deps import require_csrf, require_session

_DOD_SKILL_ID = "os.definition_of_done_verifier"

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
    """Get or initialize DoD Verifier for tenant.

    Wired to real audit backend (ADR-0232 audit chain).
    """
    if tenant_id not in _verifiers:
        # Real audit trail backend (ADR-0232 compliance, GDPR Art. 30)
        class RealAuditTrail:
            """Emit audit events to core/compliance audit backend (hash-chained, immutable)."""

            def __init__(self, tenant_id: str):
                self.tenant_id = tenant_id

            def write_event(self, event):
                """Emit audit event to real audit chain (fail-closed).

                Args:
                    event: DoD audit event (skill_id, status, input_hash, output_hash, etc.)

                Returns:
                    True if audit emit succeeded, False on error (fail-closed, logged).
                """
                # A False return makes the wrapper raise AuditFailedError, so an
                # unrecorded verification never reaches the caller.
                try:
                    ok = emit_skill_executed_event(
                        skill_id=_DOD_SKILL_ID,
                        tenant_id=self.tenant_id,
                        input_data=event.input_hash,
                        output_data=event.output_hash or None,
                        latency_ms=event.latency_ms,
                        line_of_moral_responsibility=event.lom,
                        error="VerificationError" if event.error_message else None,
                    )
                except Exception:
                    logger.exception("DoD audit event was not written to the chain")
                    return False
                if not ok:
                    logger.error("DoD audit event was not written to the chain")
                return bool(ok)

        _verifiers[tenant_id] = DoD_VerifierSkillWrapper(
            tenant_id=tenant_id,
            audit_trail=RealAuditTrail(tenant_id),
            audit_path=_default_audit_path(tenant_id),
            cwd=REPO_ROOT,
        )

    return _verifiers[tenant_id]


# The body reaches ``git diff`` and ``git grep`` as arguments and names files to
# read: refuse anything that could be read as an option or leaves the checkout.
_REV = r"[A-Za-z0-9_][A-Za-z0-9_./~^@{}-]{0,99}"
_COMMIT_RANGE = re.compile(rf"^{_REV}(\.\.\.?{_REV})?$")
_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,199}$")


def _validated_inputs(payload: Dict[str, Any]) -> Dict[str, Any]:
    commit_range = str(payload.get("commit_range") or "HEAD~1")
    if not _COMMIT_RANGE.match(commit_range):
        raise HTTPException(status_code=400, detail="commit_range must be a revision or A..B range")
    symbol = payload.get("symbol_name")
    if symbol is not None and not _SYMBOL.match(str(symbol)):
        raise HTTPException(status_code=400, detail="symbol_name must be an identifier")
    files: Dict[str, Optional[Path]] = {}
    for key in ("test_path", "test_output_file"):
        raw = payload.get(key)
        if not raw:
            files[key] = None
            continue
        p = Path(str(raw))
        p = (p if p.is_absolute() else REPO_ROOT / p).resolve()
        if not p.is_relative_to(REPO_ROOT):
            raise HTTPException(status_code=400, detail=f"{key} must be inside the project")
        files[key] = p
    return {"commit_range": commit_range, "symbol_name": symbol, **files}


def get_event_store(tenant_id: str) -> EventStore:
    """Get or initialize event store for tenant."""
    if tenant_id not in _event_stores:
        _event_stores[tenant_id] = EventStore(
            tenant_home=_tenant_home(tenant_id), tenant_id=tenant_id,
        )
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

    checked = _validated_inputs(payload)

    try:
        verifier = get_verifier(tenant_id)
        input_data = DoD_VerifierInput(
            task_id=str(payload["task_id"])[:200],
            task_type=str(payload.get("task_type", "feature"))[:50],
            symbol_name=checked["symbol_name"],
            test_path=checked["test_path"],
            test_output_file=checked["test_output_file"],
            commit_range=checked["commit_range"],
            keyword=str(payload.get("keyword", "feat:"))[:100],
            commit_msg=str(payload.get("commit_msg", ""))[:4000],
        )

        # The wrapper chains the result before returning it (fail-closed).
        result: DoD_VerificationResult = verifier.execute(input_data)

        # Log to learning event store (non-blocking, supplemental). The store
        # takes a LearningEvent: the dict passed here before raised
        # AttributeError on every call, so no verification was ever recorded
        # and /history could never return one.
        try:
            event_store = get_event_store(tenant_id)
            event_store.write_event(LearningEvent.create(
                EventType.OUTCOME, _DOD_SKILL_ID, tenant_id,
                signal={
                    "kind": "dod_verification",
                    "task_id": result.task_id,
                    "score": float(result.score),
                    "passed": bool(result.passed),
                },
                lom=f"{__name__}:run_dod_verification",
            ))
        except Exception as e:
            logger.warning(f"Failed to log DoD event to learning store: {e}")

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

    except Exception:
        logger.exception("DoD verification failed")
        raise HTTPException(status_code=500, detail="Verification failed")


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

    feedback = str(payload.get("feedback", ""))
    if feedback not in {"accurate", "inaccurate", "not_applicable"}:
        raise HTTPException(status_code=400, detail="feedback must be accurate|inaccurate|not_applicable")

    try:
        event_store = get_event_store(tenant_id)
        # A LearningEvent (the dict passed before made every call a 500). The
        # free-text ``note`` is deliberately NOT persisted — only the verdict.
        event = LearningEvent.create(
            EventType.FEEDBACK, _DOD_SKILL_ID, tenant_id,
            signal={
                "kind": "dod_feedback",
                "task_id": str(payload["task_id"])[:200],
                "check_name": str(payload.get("check_name") or "")[:100],
                "feedback": feedback,
            },
            lom=f"{__name__}:submit_dod_feedback",
        )
        event_store.write_event(event)
    except Exception:
        logger.exception("DoD feedback submission failed")
        raise HTTPException(status_code=500, detail="Feedback failed")

    # No optimizer consumes DoD feedback yet: say so instead of reporting a
    # "weight_adjustment".
    return {"accepted": True, "feedback_id": event.event_id}


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
        # query_events takes the tenant and has no generic ``filters``: the call
        # made here before raised TypeError on every request.
        events = event_store.query_events(
            tenant_id, event_type=EventType.OUTCOME, skill_id=_DOD_SKILL_ID,
        )
    except Exception:
        logger.exception("DoD history fetch failed")
        raise HTTPException(status_code=500, detail="History fetch failed")

    verifications = [
        {"timestamp": e.timestamp, **{k: v for k, v in (e.signal or {}).items() if k != "kind"}}
        for e in events
        if (e.signal or {}).get("kind") == "dod_verification"
        and (e.signal or {}).get("task_id") == task_id
    ][-limit:]
    return {"task_id": task_id, "verifications": verifications}
