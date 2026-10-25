"""Compute job records (ADR-0124 M3) - listing and removal only.

Submission is NOT available: ``POST /compute/jobs`` answers 501 and queues,
charges and audits nothing. The queue was never consumed - no code in
``core/compute`` or ``corvin_operator`` reads ``compute/jobs/`` - so a submitted
job stayed ``queued`` forever while ``enforce_compute_quota`` still spent a daily
compute unit (release-readiness gap, 2026-10-24). A job also carried no tool or
parameter grid to run. Real submission is the L25 worker path: MCP ``compute_run``
/ ``compute_submit`` and ``POST /compute/runs`` (both quota-gated).

Routes:
  GET    /compute/jobs             list records written by earlier builds
  POST   /compute/jobs             501 (see above)
  DELETE /compute/jobs/{job_id}    remove such a record
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

router = APIRouter()

_JOB_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ── Storage ───────────────────────────────────────────────────────────────────

def _jobs_dir(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "compute" / "jobs"


def _job_path(tid: str, job_id: str) -> Path:
    return _jobs_dir(tid) / f"{job_id}.json"


def _list_jobs(tid: str) -> list[dict[str, Any]]:
    d = _jobs_dir(tid)
    if not d.is_dir():
        return []
    results = []
    for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                results.append(data)
        except (OSError, json.JSONDecodeError):
            pass
    return results


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/compute/jobs")
def list_compute_jobs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    jobs = _list_jobs(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(jobs), "jobs": jobs}


SUBMIT_DISABLED_DETAIL = (
    "Compute job submission is not available on this build: no executor reads the "
    "job queue, so a submitted job would stay 'queued' forever and still use up "
    "a daily compute unit. Start a real run instead: ask the assistant to run "
    "compute_run / compute_submit on a forged tool, or POST /v1/console/compute/runs."
)


@router.post("/compute/jobs", status_code=http_status.HTTP_501_NOT_IMPLEMENTED)
def submit_compute_job(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Not implemented: 501, no quota charge, nothing queued, nothing audited.

    The body is deliberately not parsed: the answer does not depend on it.
    """
    raise HTTPException(http_status.HTTP_501_NOT_IMPLEMENTED, SUBMIT_DISABLED_DETAIL)


@router.delete("/compute/jobs/{job_id}")
def cancel_compute_job(
    job_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid job_id format")
    p = _job_path(rec.tenant_id, job_id)
    if not p.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"job {job_id!r} not found")
    try:
        p.unlink()
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.job_deleted",
        target_kind="compute_job",
        target_id=job_id,
    )
    return {"ok": True, "job_id": job_id}
