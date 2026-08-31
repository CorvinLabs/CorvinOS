"""Compute Job Creator (ADR-0124 M3).

Allows submitting compute jobs (grid / pipeline / batch) from the console.
The actual execution is handled by the existing L25 compute worker — this
route only writes the job manifest; the worker picks it up.

Routes:
  GET    /compute/jobs             list submitted jobs
  POST   /compute/jobs             submit a new job
  DELETE /compute/jobs/{job_id}    cancel / remove a job
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

router = APIRouter()

_VALID_TYPES = frozenset({"grid", "pipeline", "batch"})
_VALID_STRATEGIES = frozenset({"grid", "random", "bayesian"})
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


def _write_job(tid: str, job_id: str, data: dict[str, Any]) -> None:
    p = _job_path(tid, job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, str(p))
        os.chmod(str(p), 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Models ────────────────────────────────────────────────────────────────────

class JobSubmitRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    job_type: str = Field("grid", description="grid | pipeline | batch")
    strategy: str = Field("grid", description="grid | random | bayesian")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Parameter grid or pipeline config",
    )
    dataset_path: str | None = Field(None, description="Path to input dataset (optional)")
    max_trials: int = Field(10, ge=1, le=10_000)
    description: str = Field("", max_length=1000)
    model_config = {"extra": "forbid"}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/compute/jobs")
def list_compute_jobs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    jobs = _list_jobs(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(jobs), "jobs": jobs}


@router.post("/compute/jobs")
def submit_compute_job(
    body: JobSubmitRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if body.job_type not in _VALID_TYPES:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"job_type must be one of {sorted(_VALID_TYPES)}",
        )
    if body.strategy not in _VALID_STRATEGIES:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"strategy must be one of {sorted(_VALID_STRATEGIES)}",
        )

    # ADR-0146 CON-JOBS-01: this queue is a parallel compute-submission surface;
    # gate it with the shared fail-closed compute quota so it cannot become an
    # ungated bypass once an executor is wired to compute/jobs/ (defence in depth
    # — the manifest persists a "queued" job that a future worker would run).
    from ._compute_license_gate import enforce_compute_quota  # noqa: PLC0415

    enforce_compute_quota(
        rec.tenant_id, rec.sid_fingerprint, audit_action="compute.job_submitted",
    )

    job_id = str(uuid.uuid4())
    manifest: dict[str, Any] = {
        "job_id": job_id,
        "name": body.name,
        "job_type": body.job_type,
        "strategy": body.strategy,
        "parameters": body.parameters,
        "dataset_path": body.dataset_path,
        "max_trials": body.max_trials,
        "description": body.description,
        "status": "queued",
        "created_at": time.time(),
        "updated_at": time.time(),
    }

    try:
        _write_job(rec.tenant_id, job_id, manifest)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="compute.job_submitted",
        target_kind="compute_job",
        target_id=job_id,
    )
    return {"ok": True, "job_id": job_id, "status": "queued"}


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
