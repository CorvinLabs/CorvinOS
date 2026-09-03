"""
Marketplace Installation API — Phase 3 Implementation (ADR-0511)

Mounted UNDER ``routes/marketplace.py``'s router, whose prefix is
``/api/v1/marketplace`` — so this router carries NO prefix of its own. (Both
routers used to declare the prefix, which doubled it to
``/api/v1/marketplace/api/v1/marketplace/...`` — unreachable for the SPA,
reachable for anyone else. Adversarial review E-03, 2026-09-03.)

Effective paths (under ``/v1/console``):
- POST  /api/v1/marketplace/plugins/{id}/install
- POST  /api/v1/marketplace/plugins/{id}/uninstall
- PATCH /api/v1/marketplace/plugins/{id}/enable
- PATCH /api/v1/marketplace/plugins/{id}/disable
- GET   /api/v1/marketplace/install/{job_id}/progress

Security contract:
* mutations → ``require_csrf``; the progress read → ``require_session``.
* tenant ONLY from ``rec.tenant_id`` — never from the request body.
* every mutation is audited (``console.action_performed``).
* ``_install_jobs`` is BOUNDED (``_MAX_JOBS``, oldest evicted) and every job
  is bound to the tenant that created it — another tenant polling the job id
  gets 404.
* ``plugin_id`` is validated against a closed character class so nothing
  path-shaped can ever reach a future ``deploy_plugin`` wiring.
"""
from __future__ import annotations

import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

router = APIRouter(tags=["marketplace-install"])

# Job tracking (in-memory, bounded; Phase 4 will add persistent storage)
_MAX_JOBS = 256
_install_jobs: "OrderedDict[str, InstallJob]" = OrderedDict()
_jobs_lock = threading.Lock()

_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class InstallJob:
    """Tracks plugin installation progress."""
    job_id: str
    plugin_id: str
    tenant_id: str
    status: JobStatus
    progress: int  # 0-100
    message: str
    created_at: str
    updated_at: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_plugin_id(plugin_id: str) -> str:
    if not _PLUGIN_ID_RE.match(plugin_id or ""):
        raise HTTPException(status_code=400, detail="invalid plugin id")
    return plugin_id


def _remember(job: InstallJob) -> None:
    with _jobs_lock:
        _install_jobs[job.job_id] = job
        while len(_install_jobs) > _MAX_JOBS:
            _install_jobs.popitem(last=False)  # evict oldest


def _audit(rec: session_auth.SessionRecord, action: str, plugin_id: str) -> None:
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="marketplace_plugin",
        target_id=plugin_id,
    )


@router.post("/plugins/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: Optional[Dict[str, Any]] = Body(None),
) -> Dict[str, Any]:
    """
    Install a plugin from marketplace.

    Request: ``{"version": "1.0.0"}`` (a ``tenant_id`` in the body is ignored —
    the tenant is the authenticated session's).

    Response: ``{"status": "queued", "job_id": ..., "plugin_id": ...}``
    """
    plugin_id = _validate_plugin_id(plugin_id)
    body = body or {}
    version = str(body.get("version", "1.0.0"))[:64]

    job_id = f"install_{uuid.uuid4().hex[:12]}"
    now = _now()
    job = InstallJob(
        job_id=job_id,
        plugin_id=plugin_id,
        tenant_id=rec.tenant_id,
        status=JobStatus.PENDING,
        progress=0,
        message="Installation queued",
        created_at=now,
        updated_at=now,
    )
    _remember(job)
    _audit(rec, "marketplace.install_queued", plugin_id)

    return {
        "status": "queued",
        "job_id": job_id,
        "plugin_id": plugin_id,
        "version": version,
        "tenant_id": rec.tenant_id,
    }


@router.get("/install/{job_id}/progress")
async def get_install_progress(
    job_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    """Poll installation progress (tenant-bound: another tenant's job is 404)."""
    with _jobs_lock:
        job = _install_jobs.get(job_id)
    if job is None or job.tenant_id != rec.tenant_id:
        raise HTTPException(status_code=404, detail="Job not found")

    # Simulate progress (Phase 3 stub; Phase 4 will have real implementation)
    if job.status == JobStatus.PENDING:
        job.status = JobStatus.DOWNLOADING
        job.progress = 30
        job.message = "Downloading plugin package..."
    elif job.status == JobStatus.DOWNLOADING and job.progress < 100:
        job.progress = min(job.progress + 20, 100)
        if job.progress == 100:
            job.status = JobStatus.INSTALLING
            job.message = "Installing plugin..."
        else:
            job.message = f"Downloading... {job.progress}%"
    elif job.status == JobStatus.INSTALLING:
        job.status = JobStatus.COMPLETED
        job.progress = 100
        job.message = "Installation completed"

    job.updated_at = _now()
    return job.to_dict()


@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: Optional[Dict[str, Any]] = Body(None),
) -> Dict[str, Any]:
    """Uninstall a plugin."""
    plugin_id = _validate_plugin_id(plugin_id)
    job_id = f"uninstall_{uuid.uuid4().hex[:12]}"
    now = _now()
    job = InstallJob(
        job_id=job_id,
        plugin_id=plugin_id,
        tenant_id=rec.tenant_id,
        status=JobStatus.COMPLETED,
        progress=100,
        message="Uninstalled successfully",
        created_at=now,
        updated_at=now,
    )
    _remember(job)
    _audit(rec, "marketplace.uninstall", plugin_id)

    return {
        "status": "completed",
        "job_id": job_id,
        "plugin_id": plugin_id,
        "message": "Plugin uninstalled",
    }


@router.patch("/plugins/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Enable a plugin."""
    plugin_id = _validate_plugin_id(plugin_id)
    _audit(rec, "marketplace.enable", plugin_id)
    return {"status": "enabled", "plugin_id": plugin_id, "message": "Plugin enabled"}


@router.patch("/plugins/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> Dict[str, Any]:
    """Disable a plugin."""
    plugin_id = _validate_plugin_id(plugin_id)
    _audit(rec, "marketplace.disable", plugin_id)
    return {"status": "disabled", "plugin_id": plugin_id, "message": "Plugin disabled"}
