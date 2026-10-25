"""DoD Verifier API routes.

Provides REST endpoints for Definition-of-Done verification:
  - POST /api/dod/verify — run verification on a project
  - GET /api/dod/history/{task_id} — fetch verification history
  - POST /api/dod/feedback — submit feedback on DoD checks; it tunes the tenant's
    per-task-type weights (ADR-2284 decision 1, audit-first ``skill_config_updated``)

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
import threading

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
from core.learning.feedback_gate import FeedbackRejected
from core.skills.os_skills.audit_integration import emit_skill_config_updated_event
from core.skills.os_skills.definition_of_done_verifier.weight_optimizer import (
    CHECK_TO_WEIGHT,
    DoD_WeightOptimizer,
)
from core.skills.os_skills.definition_of_done_verifier.weight_persistence import (
    WeightPersistence,
)

from .. import auth as session_auth
from ..deps import require_csrf, require_session

_DOD_SKILL_ID = "os.definition_of_done_verifier"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dod", tags=["dod-verifier"])

# Global DoD Verifier instance (per-tenant)
_verifiers: Dict[str, DoD_VerifierSkillWrapper] = {}
_event_stores: Dict[str, EventStore] = {}
# One read-modify-write of a tenant's weight file at a time (feedback is rare; a lock per
# tenant is enough inside one process).
_weight_locks: Dict[str, threading.Lock] = {}
_weight_locks_guard = threading.Lock()

#: ADR-2284 decision 1: a verdict on one check -> the optimizer's signed delta.
#: ``accurate``: the check carried real signal -> its weight goes UP (negative delta,
#: "the skill was too lenient about it"). ``inaccurate``: it misjudged -> its weight goes DOWN.
#: ``not_applicable``: it should not count for this task type -> DOWN by half a step.
#: |delta| * LEARNING_RATE is capped at 0.05 per rating by the optimizer.
FEEDBACK_DELTA = {"accurate": -0.20, "inaccurate": 0.20, "not_applicable": 0.10}


def _weight_lock(tenant_id: str) -> threading.Lock:
    with _weight_locks_guard:
        return _weight_locks.setdefault(tenant_id, threading.Lock())


def _optimizer(tenant_id: str) -> DoD_WeightOptimizer:
    """The tenant's optimizer, restored from its persisted weights AND confidence."""
    return DoD_WeightOptimizer.from_state(WeightPersistence(tenant_id=tenant_id).load_state())


def _applied_weights(tenant_id: str, task_type: str) -> Dict[str, float]:
    """What the verifier scores ``task_type`` with: learned weights only past the ADR-0723
    confidence gate (> 0.5), clamped to [0.05, 0.6], summing to 1; else the defaults."""
    return _optimizer(tenant_id).recommend_next_weights(task_type)


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
            weights_for=lambda task_type, _t=tenant_id: _applied_weights(_t, task_type),
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
        #
        # ADR-2284: the OUTCOME carries the task type, which /feedback looks up by
        # task_id. The SKILL_EXECUTED event is the execution the ADR-0534 reality check
        # requires before it admits feedback on this skill (without it every DoD rating
        # was refused). Ids, enums and numbers only.
        try:
            event_store = get_event_store(tenant_id)
            event_store.write_event(LearningEvent.create(
                EventType.SKILL_EXECUTED, _DOD_SKILL_ID, tenant_id,
                signal={
                    "kind": "dod_verification",
                    "task_id": result.task_id,
                    "task_type": input_data.task_type,
                    "status": "success",
                    "output": {"score": float(result.score), "passed": bool(result.passed)},
                },
                lom=f"{__name__}:run_dod_verification",
            ))
            event_store.write_event(LearningEvent.create(
                EventType.OUTCOME, _DOD_SKILL_ID, tenant_id,
                signal={
                    "kind": "dod_verification",
                    "task_id": result.task_id,
                    "task_type": input_data.task_type,
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
            "weights_source": (
                "default" if result.weights == DoD_WeightOptimizer.DEFAULT_WEIGHTS else "learned"),
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
    """Submit feedback on one check of a recorded DoD verification (ADR-2284 decision 1).

    Request body:
    {
        "task_id": "task_123",
        "check_name": "reproducibility",   # a live check key
        "feedback": "accurate",            # or "inaccurate", "not_applicable"
        "note": "..."                      # never persisted
    }

    Order: the verification is looked up by ``task_id`` (its recorded task type; unknown
    task / unlearnable type / unknown check -> 422) -> FEEDBACK through
    ``EventStore.write_event`` (the ADR-0534 gate: throttled -> 429, refused -> 422) ->
    ``DoD_WeightOptimizer.observe_feedback`` -> ``skill_config_updated`` chained FIRST ->
    the tenant's weight file. A change that cannot be chained is not persisted (500).
    """
    tenant_id = rec.tenant_id

    if not payload.get("task_id"):
        raise HTTPException(status_code=400, detail="task_id required")
    task_id = str(payload["task_id"])[:200]

    feedback = str(payload.get("feedback", ""))
    if feedback not in FEEDBACK_DELTA:
        raise HTTPException(status_code=400, detail="feedback must be accurate|inaccurate|not_applicable")
    check_name = str(payload.get("check_name") or "")
    if check_name not in CHECK_TO_WEIGHT:
        raise HTTPException(status_code=422,
                            detail=f"check_name must be one of {sorted(CHECK_TO_WEIGHT)}")

    try:
        event_store = get_event_store(tenant_id)
        verifications = [
            e for e in event_store.query_events(
                tenant_id, event_type=EventType.OUTCOME, skill_id=_DOD_SKILL_ID)
            if (e.signal or {}).get("kind") == "dod_verification"
            and (e.signal or {}).get("task_id") == task_id
        ]
    except Exception:
        logger.exception("DoD verification lookup failed")
        raise HTTPException(status_code=500, detail="Feedback failed")
    if not verifications:
        raise HTTPException(status_code=422, detail="no recorded verification for this task_id")
    task_type = str((verifications[-1].signal or {}).get("task_type") or "")
    if not DoD_WeightOptimizer.is_learnable(task_type):
        raise HTTPException(
            status_code=422,
            detail=f"task_type of this verification is not learnable "
                   f"(one of {DoD_WeightOptimizer.TASK_TYPES})")

    # FEEDBACK through the ADR-0534 gate (throttle -> reality check -> trust weight). The
    # free-text ``note`` is deliberately NOT persisted — only the verdict.
    try:
        event = LearningEvent.create(
            EventType.FEEDBACK, _DOD_SKILL_ID, tenant_id,
            signal={
                "kind": "dod_feedback",
                "task_id": task_id,
                "task_type": task_type,
                "check_name": check_name,
                "feedback": feedback,
            },
            lom=f"{__name__}:submit_dod_feedback",
        )
        event_store.write_event(event)
    except FeedbackRejected as rej:
        code = 429 if rej.reason == "rate_limit_exceeded" else 422
        raise HTTPException(status_code=code, detail=f"feedback rejected: {rej.reason}")
    except Exception:
        logger.exception("DoD feedback submission failed")
        raise HTTPException(status_code=500, detail="Feedback failed")

    with _weight_lock(tenant_id):
        persistence = WeightPersistence(tenant_id=tenant_id)
        optimizer = DoD_WeightOptimizer.from_state(persistence.load_state())
        before_conf = dict(optimizer.confidence[task_type])
        result = optimizer.observe_feedback(task_type, FEEDBACK_DELTA[feedback], [check_name])
        changed = (result.new_weights != result.old_weights
                   or result.confidence != before_conf)
        if changed:
            # Audit-first: no chain record -> nothing persisted. Metadata only: the task
            # type, the two weight vectors and the per-weight confidence (numbers).
            ok = emit_skill_config_updated_event(
                skill_id=_DOD_SKILL_ID,
                tenant_id=tenant_id,
                param_name=f"weights.{task_type}",
                old_value=None,
                new_value=None,
                reason="dod_feedback",
                details={
                    "task_type": task_type,
                    "old_weights": {k: round(v, 6) for k, v in result.old_weights.items()},
                    "new_weights": {k: round(v, 6) for k, v in result.new_weights.items()},
                    "confidence": {k: round(v, 6) for k, v in result.confidence.items()},
                    "lom": f"{__name__}:submit_dod_feedback",
                },
            )
            if not ok:
                logger.error("DoD weight change was not chained; not persisted")
                raise HTTPException(status_code=500, detail="Weight change audit failed")
            if not persistence.save_weights(optimizer.weights, optimizer.confidence,
                                            optimizer.sample_count):
                raise HTTPException(status_code=500, detail="Weight change could not be stored")

    return {
        "accepted": True,
        "feedback_id": event.event_id,
        "task_type": task_type,
        "weights_changed": changed,
        "old_weights": result.old_weights,
        "new_weights": result.new_weights,
        "confidence": result.confidence,
        # what the next verification of this task type scores with (confidence gate applied)
        "applied_weights": optimizer.recommend_next_weights(task_type),
    }


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
