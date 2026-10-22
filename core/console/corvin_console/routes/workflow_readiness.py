"""Workflow readiness routes (ADR-2275 / PLAN-0962).

  GET  /workflows/{wid}/readiness       compute the readiness report (synthetic stub run) and return it
  PUT  /workflows/{wid}/acceptance      store the acceptance case the synthetic run is judged against
  POST /workflows/{wid}/ready-override  mark a draft ready past a failing report (audited, reason required)

"Ready" is a measured report, not a keyword heuristic: the design chat refuses the move to
``ready`` until every line of this report passes (``routes/workflows.py::_design_turn``). The
report is SYNTHETIC - a real ``DAGRunner`` run against a deterministic stub that never reaches a
channel - and says so. The tenant comes only from the session record. Writes touch exactly one
thing, ``meta["acceptance"]`` / ``meta["phase"]``, under the workflow's own YAML lock.

Helpers of ``routes/workflows.py`` are imported lazily inside the handlers: this module must stay
importable even when that large module is not, because an import error in a console route module
removes the whole console.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http_status

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

_WORKFLOWS_PKG = Path(__file__).resolve().parents[3] / "workflows"
if _WORKFLOWS_PKG.is_dir() and str(_WORKFLOWS_PKG) not in sys.path:
    sys.path.append(str(_WORKFLOWS_PKG))

_log = logging.getLogger(__name__)

router = APIRouter()

ACCEPTANCE_MAX_BYTES = 64 * 1024
OVERRIDE_REASON_MAX = 300
# A check is CPU work on the request's behalf; more than a few at once is a misuse, not a feature.
_CHECK_SLOTS = threading.BoundedSemaphore(4)


def _wf():
    from . import workflows as _w  # lazy: see module docstring

    return _w


def _readiness_mod():
    try:
        from corvin_workflows import readiness as _r
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE, "workflow engine not available") from exc
    return _r


def max_design_repairs(tenant_id: str) -> int:
    """Ceiling of designer repairs per draft: ``spec.workflows.max_design_repairs`` of the tenant
    YAML, else the module default (3)."""
    r = _readiness_mod()
    try:
        spec = (_wf()._tenant_yaml(tenant_id).get("spec") or {})
        val = (spec.get("workflows") or {}).get("max_design_repairs")
        if isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= 50:
            return val
    except Exception:  # noqa: BLE001 - a broken tenant file must not break the report
        pass
    return r.DEFAULT_MAX_REPAIRS


def compute_report(tenant_id: str, wid: str, *, meta: dict[str, Any] | None = None,
                   yaml_text: str | None = None):
    """The one place a report is computed (the route and the design-turn gate both call it)."""
    w, r = _wf(), _readiness_mod()
    meta = meta if meta is not None else (w._read_json(w._meta_path(tenant_id, wid)) or {})
    if yaml_text is None:
        yp = w._yaml_path(tenant_id, wid)
        yaml_text = yp.read_text(encoding="utf-8") if yp.exists() else ""
    acceptance = meta.get("acceptance")
    repairs = meta.get("repairs")
    return r.check_readiness(
        yaml_text,
        acceptance=acceptance if isinstance(acceptance, dict) else None,
        repairs=repairs if isinstance(repairs, int) and not isinstance(repairs, bool) else 0,
        max_repairs=max_design_repairs(tenant_id),
    )


def audit_report(rec: session_auth.SessionRecord, wid: str, report) -> None:
    console_audit.workflow_readiness_checked(
        tenant_id=rec.tenant_id, wid=wid, ready=report.ready,
        failing=sum(1 for s in report.signals if s.status == "fail"),
        unmeasured=sum(1 for s in report.signals if s.status == "unmeasured"),
        elapsed_ms=report.elapsed_ms, sid_fingerprint=rec.sid_fingerprint,
    )


@router.get("/workflows/{wid}/readiness")
def get_readiness(
    wid: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    w = _wf()
    w._validate_wid(wid)
    meta = w._require_workflow(rec.tenant_id, wid)
    if not _CHECK_SLOTS.acquire(blocking=False):
        raise HTTPException(http_status.HTTP_429_TOO_MANY_REQUESTS, "too many readiness checks running - try again")
    try:
        report = compute_report(rec.tenant_id, wid, meta=meta)
    finally:
        _CHECK_SLOTS.release()
    audit_report(rec, wid, report)
    out = report.as_dict()
    out["phase"] = str(meta.get("phase", "discovering"))
    out["acceptance"] = meta.get("acceptance") if isinstance(meta.get("acceptance"), dict) else None
    out["notice"] = ("Synthetic run: checks structure and the acceptance case, not real execution "
                     "(no model, channel or credential is used).")
    return out


@router.put("/workflows/{wid}/acceptance")
async def put_acceptance(
    wid: str,
    request: Request,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    w, r = _wf(), _readiness_mod()
    w._validate_wid(wid)
    w._require_workflow(rec.tenant_id, wid)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > ACCEPTANCE_MAX_BYTES:
        raise HTTPException(http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "acceptance case too large (max 64 KiB)")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > ACCEPTANCE_MAX_BYTES:
            raise HTTPException(http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "acceptance case too large (max 64 KiB)")
    try:
        case = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "acceptance case must be a JSON object") from exc
    problem = r.validate_acceptance(case)
    if problem:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, problem)

    def write() -> None:
        with w._yaml_lock(rec.tenant_id, wid):
            meta = w._read_json(w._meta_path(rec.tenant_id, wid)) or {}
            meta["acceptance"] = case
            meta["updated_at"] = time.time()
            w._write_atomic(w._meta_path(rec.tenant_id, wid), meta)

    try:
        await anyio.to_thread.run_sync(write)
    except w.WorkflowLockBusy as exc:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "the workflow is being edited elsewhere - try again") from exc
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="workflow.acceptance_saved", target_kind="workflow", target_id=wid,
    )
    return {"ok": True}


@router.post("/workflows/{wid}/ready-override")
async def post_ready_override(
    wid: str,
    body: dict[str, Any],
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    w = _wf()
    w._validate_wid(wid)
    w._require_workflow(rec.tenant_id, wid)
    if set(body) - {"reason"}:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "only 'reason' is accepted")
    reason = body.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not 1 <= len(reason) <= OVERRIDE_REASON_MAX:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            f"a reason of 1 to {OVERRIDE_REASON_MAX} characters is required")

    def measure():
        return compute_report(rec.tenant_id, wid)  # the 10 s check runs outside the YAML lock

    report = await anyio.to_thread.run_sync(measure)
    validator = next(s for s in report.signals if s.name == "validator")
    if validator.status != "pass":
        # The gate guards against self-deception, not against the operator - but a draft that
        # does not validate cannot run at all, so no override reaches past that line.
        raise HTTPException(http_status.HTTP_409_CONFLICT, "the draft does not validate and cannot be marked ready")
    failing_now = sum(1 for s in report.signals if s.status in ("fail", "unmeasured"))

    def apply() -> int:
        with w._yaml_lock(rec.tenant_id, wid):
            meta = w._read_json(w._meta_path(rec.tenant_id, wid)) or {}
            meta["phase"] = "ready"
            meta["ready_override"] = {"at": time.time(), "reason": reason, "failing": failing_now}
            meta["updated_at"] = time.time()
            w._write_atomic(w._meta_path(rec.tenant_id, wid), meta)
            return failing_now

    try:
        failing = await anyio.to_thread.run_sync(apply)
    except w.WorkflowLockBusy as exc:
        raise HTTPException(http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            "the workflow is being edited elsewhere - try again") from exc
    console_audit.workflow_ready_overridden(
        tenant_id=rec.tenant_id, wid=wid, reason_len=len(reason), failing=failing,
        sid_fingerprint=rec.sid_fingerprint,
    )
    return {"ok": True, "phase": "ready"}
