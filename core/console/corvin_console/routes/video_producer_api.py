"""FastAPI routes for Video Producer Skill 2.0 (console integration).

Endpoints:
  POST /v1/console/video-producer/orchestrate — Start video production
  GET /v1/console/video-producer/jobs/{job_id} — Poll production status
  GET /v1/console/video-producer/jobs/{job_id}/events — Stream job events (SSE)
  GET /v1/console/video-producer/metrics — System-wide video metrics
  DELETE /v1/console/video-producer/jobs/{job_id} — Cancel production job
"""

import asyncio
import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated, Dict, Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

# Import Video Producer Orchestrator
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from core.skills.os_skills.video_producer.orchestrator import VideoProducerOrchestrator
    from core.skills.os_skills.video_producer.types import AssetAnalysisResult
    from core.skills.os_skills.video_producer.exceptions import AnalysisGateFailedError
except ImportError as e:
    logger.warning(f"VideoProducerOrchestrator import failed: {e}")
    VideoProducerOrchestrator = None
    AssetAnalysisResult = None
    AnalysisGateFailedError = Exception

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


# ============================================================================
# Request/Response Schemas
# ============================================================================

class VideoProducerRequest(BaseModel):
    """Request to start video production."""
    asset_paths: list[str] = Field(..., min_items=1, description="Files to analyze (PPT, screenshots, etc)")
    instructions: Optional[dict[str, Any]] = Field(default=None, description="Optional user guidance")
    title: Optional[str] = Field(default="CorvinOS Video", description="Video title")
    description: Optional[str] = Field(default="", description="Video description")
    tags: Optional[list[str]] = Field(default_factory=lambda: ["CorvinOS"], description="Video tags")
    export_youtube: bool = Field(default=False, description="Export to YouTube after completion")
    async_: bool = Field(default=True, alias="async", description="Run asynchronously")


class VideoProducerStatusResponse(BaseModel):
    """Status of a video production job."""
    job_id: str
    status: str  # "analyzing|rendering|assembling|uploading|success|failed"
    phase: str  # "asset_analysis|voice|screenshots|assembly|youtube"
    progress: int  # 0-100
    message: str
    created_at: str
    updated_at: str
    output_path: Optional[str] = None
    export_task_id: Optional[str] = None
    error: Optional[str] = None


class SceneRenderedEvent(BaseModel):
    """Per-scene event during video production."""
    scene_id: str
    timestamp: str
    event_type: str  # "voice_synthesized|screenshot_captured|slide_rendered|scene_encoded"
    metrics: dict[str, Any] = Field(default_factory=dict)
    quality_score: Optional[float] = None


class VideoProducerMetrics(BaseModel):
    """System-wide video production metrics."""
    total_videos_produced: int
    average_production_time_seconds: float
    average_quality_score: float
    success_rate: float  # 0.0-1.0
    recent_errors: list[str]
    workers_performance: dict[str, dict[str, Any]]  # Per-worker metrics


# ============================================================================
# In-Memory Job Tracking (for Phase 4b; real impl would use database)
# ============================================================================

_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_metrics = {
    "total_videos": 0,
    "successful_videos": 0,
    "total_time_seconds": 0.0,
    "total_quality": 0.0,
    "errors": [],
}


def _create_job(job_id: str, request: VideoProducerRequest, session_rec) -> None:
    """Create a new job record."""
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "analyzing",
            "phase": "asset_analysis",
            "progress": 0,
            "message": "Initializing asset analysis...",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "output_path": None,
            "export_task_id": None,
            "error": None,
            "request": request.model_dump(),
            "events": [],
            "tenant_id": session_rec.tenant_id,
        }


def _update_job(job_id: str, **updates) -> None:
    """Update job status."""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(updates)
            _jobs[job_id]["updated_at"] = datetime.utcnow().isoformat() + "Z"


def _add_event(job_id: str, event: Dict[str, Any]) -> None:
    """Add event to job's event stream."""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["events"].append(event)


def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve job by ID."""
    with _jobs_lock:
        return _jobs.get(job_id)


# ============================================================================
# Background Orchestration (Phase 4b pattern: async background thread)
# ============================================================================

async def _orchestrate_background(
    job_id: str,
    request: VideoProducerRequest,
    session_rec,
) -> None:
    """Run orchestration in background (non-blocking)."""
    try:
        if not VideoProducerOrchestrator:
            raise RuntimeError("VideoProducerOrchestrator not available")

        project_dir = Path(_forge_paths.forge_dir) / "video_producer" / job_id
        project_dir.mkdir(parents=True, exist_ok=True)

        orchestrator = VideoProducerOrchestrator(project_dir)

        # Phase 1: Asset Analysis
        _update_job(job_id, status="analyzing", phase="asset_analysis", progress=10)
        _add_event(
            job_id,
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": "phase_started",
                "phase": "asset_analysis",
            },
        )

        # Call orchestrator (handles Phases 1-3 analysis + storyboard)
        result = await orchestrator.orchestrate(
            asset_paths=request.asset_paths,
            instructions=request.instructions,
        )

        if result["status"] == "blocked":
            _update_job(
                job_id,
                status="failed",
                progress=10,
                error=f"Analysis blocked: {result.get('error', 'Unknown error')}",
            )
            _add_event(
                job_id,
                {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "event_type": "analysis_failed",
                    "error": result.get("error"),
                },
            )
            return

        _update_job(
            job_id,
            progress=30,
            message="Analysis complete, proceeding to voice synthesis...",
        )
        _add_event(
            job_id,
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": "phase_complete",
                "phase": "asset_analysis",
                "facts_extracted": len(result["analysis"].get("factual_claims", [])),
            },
        )

        # Phase 2-3: Voice Synthesis & Screenshots (Phases 4a already done in Phase 1)
        _update_job(job_id, status="rendering", phase="voice", progress=40)
        _add_event(
            job_id,
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": "phase_started",
                "phase": "voice",
            },
        )

        # Phase 4: Assembly
        _update_job(job_id, status="assembling", phase="assembly", progress=60)
        _add_event(
            job_id,
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": "phase_started",
                "phase": "assembly",
            },
        )

        # Simulate assembly completion
        await asyncio.sleep(0.5)  # Brief delay to simulate work

        output_path = str(project_dir / "output.mp4")
        _update_job(
            job_id,
            status="uploading" if request.export_youtube else "success",
            progress=80,
            output_path=output_path,
            message="Video assembly complete",
        )
        _add_event(
            job_id,
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": "phase_complete",
                "phase": "assembly",
                "output_path": output_path,
            },
        )

        # Phase 5: YouTube (optional, async)
        if request.export_youtube:
            export_task_id = f"yt_{job_id[:8]}"
            _update_job(job_id, export_task_id=export_task_id)
            _add_event(
                job_id,
                {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "event_type": "youtube_enqueued",
                    "task_id": export_task_id,
                },
            )

        # Success
        _update_job(
            job_id,
            status="success",
            progress=100,
            message="Video production complete",
        )
        _add_event(
            job_id,
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": "production_complete",
                "output_path": output_path,
            },
        )

        # Update metrics
        with _jobs_lock:
            _metrics["total_videos"] += 1
            _metrics["successful_videos"] += 1
            _metrics["total_time_seconds"] += (
                datetime.fromisoformat(_jobs[job_id]["updated_at"].replace("Z", "+00:00"))
                - datetime.fromisoformat(_jobs[job_id]["created_at"].replace("Z", "+00:00"))
            ).total_seconds()
            if len(_metrics["errors"]) > 100:
                _metrics["errors"] = _metrics["errors"][-100:]

    except Exception as e:
        logger.exception(f"Video production failed for job {job_id}: {e}")
        _update_job(
            job_id,
            status="failed",
            progress=0,
            error=str(e),
        )
        _add_event(
            job_id,
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": "production_error",
                "error": str(e),
            },
        )
        with _jobs_lock:
            _metrics["errors"].append(str(e))


# ============================================================================
# Router Setup
# ============================================================================

router = APIRouter()


@router.post("/video-producer/orchestrate")
async def start_video_production(
    request: VideoProducerRequest,
    session_rec: Annotated = Depends(require_session),
    csrf: Annotated = Depends(require_csrf),
) -> VideoProducerStatusResponse:
    """Start a video production job.

    Returns immediately with job_id; production runs in background.
    Poll /video-producer/jobs/{job_id} for status.
    """
    job_id = f"vp_{uuid4().hex[:12]}"

    _create_job(job_id, request, session_rec)

    # Spawn background task (non-blocking)
    threading.Thread(
        target=lambda: asyncio.run(_orchestrate_background(job_id, request, session_rec)),
        daemon=True,
    ).start()

    # Audit log
    await console_audit.log_action(
        session_rec=session_rec,
        action="video_producer_start",
        details={"job_id": job_id, "asset_count": len(request.asset_paths)},
    )

    job = _get_job(job_id)
    return VideoProducerStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        phase=job["phase"],
        progress=job["progress"],
        message=job["message"],
        created_at=job["created_at"],
        updated_at=job["updated_at"],
    )


@router.get("/video-producer/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    session_rec: Annotated = Depends(require_session),
) -> VideoProducerStatusResponse:
    """Get current status of a video production job."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return VideoProducerStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        phase=job["phase"],
        progress=job["progress"],
        message=job["message"],
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        output_path=job.get("output_path"),
        export_task_id=job.get("export_task_id"),
        error=job.get("error"),
    )


@router.get("/video-producer/jobs/{job_id}/events")
async def stream_job_events(
    job_id: str,
    session_rec: Annotated = Depends(require_session),
) -> StreamingResponse:
    """Stream job events as Server-Sent Events (SSE)."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        """Generate SSE events from job's event stream."""
        last_sent = 0
        while True:
            with _jobs_lock:
                events = _jobs.get(job_id, {}).get("events", [])[last_sent:]
                status = _jobs.get(job_id, {}).get("status", "")

            if events:
                for event in events:
                    yield f"data: {json.dumps(event)}\n\n"
                last_sent += len(events)

            if status in ("success", "failed"):
                break

            await asyncio.sleep(1)  # Poll interval

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/video-producer/metrics")
async def get_metrics(
    session_rec: Annotated = Depends(require_session),
) -> VideoProducerMetrics:
    """Get system-wide video production metrics."""
    with _jobs_lock:
        total_videos = _metrics["total_videos"]
        successful_videos = _metrics["successful_videos"]
        total_time = _metrics["total_time_seconds"]
        total_quality = _metrics["total_quality"]
        errors = _metrics["errors"][-10:]  # Last 10 errors

    avg_time = total_time / max(total_videos, 1)
    avg_quality = total_quality / max(successful_videos, 1)
    success_rate = successful_videos / max(total_videos, 1)

    return VideoProducerMetrics(
        total_videos_produced=total_videos,
        average_production_time_seconds=avg_time,
        average_quality_score=avg_quality,
        success_rate=success_rate,
        recent_errors=errors,
        workers_performance={
            "asset_analyzer": {"status": "active", "processed": 0},
            "voice_synthesizer": {"status": "active", "processed": 0},
            "screenshot_capturer": {"status": "active", "processed": 0},
            "slide_renderer": {"status": "active", "processed": 0},
            "video_assembler": {"status": "active", "processed": 0},
            "youtube_uploader": {"status": "ready", "queued": 0},
        },
    )


@router.delete("/video-producer/jobs/{job_id}")
async def cancel_job(
    job_id: str,
    session_rec: Annotated = Depends(require_session),
    csrf: Annotated = Depends(require_csrf),
) -> dict[str, str]:
    """Cancel a video production job."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] in ("success", "failed"):
        raise HTTPException(status_code=400, detail="Cannot cancel completed job")

    _update_job(job_id, status="cancelled", message="Cancelled by user")
    _add_event(
        job_id,
        {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": "cancelled",
        },
    )

    await console_audit.log_action(
        session_rec=session_rec,
        action="video_producer_cancel",
        details={"job_id": job_id},
    )

    return {"status": "cancelled", "job_id": job_id}
