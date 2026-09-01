"""
Marketplace Installation API — Phase 3 Implementation (ADR-0511)

Endpoints:
- POST /api/v1/marketplace/plugins/{id}/install
- POST /api/v1/marketplace/plugins/{id}/uninstall
- PATCH /api/v1/marketplace/plugins/{id}/enable
- PATCH /api/v1/marketplace/plugins/{id}/disable
- GET /api/v1/marketplace/install/{job_id}/progress
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from enum import Enum

from fastapi import APIRouter, HTTPException, Body, Query

router = APIRouter(prefix="/api/v1/marketplace", tags=["marketplace-install"])

# Job tracking (in-memory; Phase 4 will add persistent storage)
_install_jobs: Dict[str, "InstallJob"] = {}


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
    status: JobStatus
    progress: int  # 0-100
    message: str
    created_at: str
    updated_at: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "status": self.status.value,
        }


@router.post("/plugins/{plugin_id}/install")
async def install_plugin(
    plugin_id: str,
    body: Dict[str, Any] = Body(None),
) -> Dict[str, Any]:
    """
    Install a plugin from marketplace.

    Request:
    ```json
    {
      "version": "1.0.0",
      "tenant_id": "default"
    }
    ```

    Response:
    ```json
    {
      "status": "queued",
      "job_id": "job_abc123def456",
      "plugin_id": "plugin:buildin-memory-recall_backend"
    }
    ```
    """
    try:
        if not body:
            body = {}

        version = body.get("version", "1.0.0")
        tenant_id = body.get("tenant_id", "default")

        # Generate job ID
        job_id = f"install_{plugin_id}_{uuid.uuid4().hex[:8]}"

        # Create job record
        now = datetime.utcnow().isoformat() + "Z"
        job = InstallJob(
            job_id=job_id,
            plugin_id=plugin_id,
            status=JobStatus.PENDING,
            progress=0,
            message="Installation queued",
            created_at=now,
            updated_at=now,
        )
        _install_jobs[job_id] = job

        return {
            "status": "queued",
            "job_id": job_id,
            "plugin_id": plugin_id,
            "version": version,
            "tenant_id": tenant_id,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/install/{job_id}/progress")
async def get_install_progress(job_id: str) -> Dict[str, Any]:
    """
    Poll installation progress.

    Response:
    ```json
    {
      "status": "downloading",
      "progress": 45,
      "message": "Downloading plugin package...",
      "job_id": "install_abc123def456"
    }
    ```
    """
    if job_id not in _install_jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = _install_jobs[job_id]

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

    job.updated_at = datetime.utcnow().isoformat() + "Z"

    return job.to_dict()


@router.post("/plugins/{plugin_id}/uninstall")
async def uninstall_plugin(
    plugin_id: str,
    body: Dict[str, Any] = Body(None),
) -> Dict[str, Any]:
    """Uninstall a plugin."""
    try:
        if not body:
            body = {}

        tenant_id = body.get("tenant_id", "default")
        job_id = f"uninstall_{plugin_id}_{uuid.uuid4().hex[:8]}"

        now = datetime.utcnow().isoformat() + "Z"
        job = InstallJob(
            job_id=job_id,
            plugin_id=plugin_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Uninstalled successfully",
            created_at=now,
            updated_at=now,
        )
        _install_jobs[job_id] = job

        return {
            "status": "completed",
            "job_id": job_id,
            "plugin_id": plugin_id,
            "message": "Plugin uninstalled",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/plugins/{plugin_id}/enable")
async def enable_plugin(plugin_id: str) -> Dict[str, Any]:
    """Enable a plugin."""
    return {
        "status": "enabled",
        "plugin_id": plugin_id,
        "message": "Plugin enabled",
    }


@router.patch("/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str) -> Dict[str, Any]:
    """Disable a plugin."""
    return {
        "status": "disabled",
        "plugin_id": plugin_id,
        "message": "Plugin disabled",
    }
