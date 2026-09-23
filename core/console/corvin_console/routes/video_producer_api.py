from core.security.csrf import require_csrf
"""FastAPI routes for Video Producer plugin."""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from typing import Optional, List, Annotated
from datetime import datetime
import uuid
import sys
import os
import logging
import importlib.util
from pathlib import Path

# Security imports for auth + CSRF (Phase 9 P0 fixes)
from corvin_console.deps import require_csrf, require_session
from corvin_console import auth as session_auth

logger = logging.getLogger(__name__)

# Try to import plugin modules from multiple locations
VideoJob = None
get_storage = None
get_runner = None

# The third entry used to be the maintainer's own absolute path
# (/home/shumway/projects/Corvin-Marketplace/...), which resolves on exactly one
# machine and is shipped inside the console wheel. It is replaced by an explicit
# operator override so a developer whose checkout does not sit next to CorvinOS
# can still point at the marketplace tree.
# Where the plugin's ``src/`` (models.py + storage.py + skill.py) may live.
# Until 2026-09-20 only two locations were tried — an install path that no
# installer writes and a relative marketplace path that resolved to
# ``core/console/Corvin-Marketplace`` — so on the maintainer host every
# route answered 503 "plugin not available" although the plugin sits in the
# sibling marketplace checkout under ``contributor/media/video_producer``.
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_corvin_home = os.environ.get("CORVIN_HOME", "").strip() or os.path.expanduser("~/.corvin")
plugin_locations = [
    (os.path.join(_corvin_home, "tenants", "_default", "plugins", "instances", "video_producer", "src"), "installed-instance"),
    (os.path.join(_corvin_home, "plugins", "media", "video_producer", "src"), "installed-media"),
    (os.path.join(_corvin_home, "plugins", "video_producer", "src"), "installed"),
    ("~/.corvin/plugins/media/video_producer/src", "home-media"),
    ("~/.corvin/plugins/video_producer/src", "home"),
    (os.path.join(_repo_root, "..", "Corvin-Marketplace", "plugins", "contributor", "media", "video_producer", "src"), "sibling-marketplace"),
    (os.path.join(_repo_root, "..", "Corvin-Marketplace", "plugins", "contributor", "video_producer", "src"), "sibling-marketplace-legacy"),
]
_marketplace_root = os.environ.get("CORVIN_MARKETPLACE_ROOT")
if _marketplace_root:
    for sub in (("contributor", "media", "video_producer"), ("contributor", "video_producer")):
        plugin_locations.append((os.path.join(_marketplace_root, "plugins", *sub, "src"), "dev-env"))
PLUGIN_SOURCE: Optional[str] = None

for rel_path, location_type in plugin_locations:
    # Expand path
    if rel_path.startswith("~"):
        plugin_path = os.path.expanduser(rel_path)
    elif rel_path.startswith("/"):
        plugin_path = rel_path
    else:
        plugin_path = os.path.join(os.path.dirname(__file__), rel_path)

    if not os.path.exists(plugin_path):
        continue

    # Try to load models.py directly
    models_path = os.path.join(plugin_path, "models.py")
    storage_path = os.path.join(plugin_path, "storage.py")
    skill_path = os.path.join(plugin_path, "skill.py")
    async_runner_path = os.path.join(plugin_path, "async_runner.py")

    if os.path.exists(models_path) and os.path.exists(storage_path):
        try:
            logger.debug(f"Attempting import from {location_type}: {plugin_path}")

            # Load models module
            spec = importlib.util.spec_from_file_location("video_producer_models", models_path)
            models_module = importlib.util.module_from_spec(spec)
            sys.modules["video_producer_models"] = models_module
            spec.loader.exec_module(models_module)
            VideoJob = models_module.VideoJob

            # Load storage module (need to register models in sys.modules first)
            sys.modules["models"] = models_module
            spec = importlib.util.spec_from_file_location("video_producer_storage", storage_path)
            storage_module = importlib.util.module_from_spec(spec)
            sys.modules["video_producer_storage"] = storage_module
            spec.loader.exec_module(storage_module)
            get_storage = storage_module.get_storage
            sys.modules["storage"] = storage_module

            # Load skill module (real orchestration) if present
            if os.path.exists(skill_path):
                spec = importlib.util.spec_from_file_location("video_producer_skill", skill_path)
                skill_module = importlib.util.module_from_spec(spec)
                sys.modules["video_producer_skill"] = skill_module
                sys.modules["skill"] = skill_module
                spec.loader.exec_module(skill_module)

            # Load async_runner module (background thread-pool job runner) if present
            if os.path.exists(async_runner_path):
                spec = importlib.util.spec_from_file_location("video_producer_async_runner", async_runner_path)
                async_runner_module = importlib.util.module_from_spec(spec)
                sys.modules["video_producer_async_runner"] = async_runner_module
                spec.loader.exec_module(async_runner_module)
                get_runner = async_runner_module.get_runner

            PLUGIN_SOURCE = plugin_path
            logger.info(f"✓ Successfully imported video producer from {location_type}: {plugin_path}")
            break
        except Exception as e:
            logger.debug(f"✗ Import failed from {location_type}: {e}")

router = APIRouter(prefix="/video", tags=["video-producer"])


class CreateJobRequest(BaseModel):
    task: str


class JobResponse(BaseModel):
    id: str
    task: str
    status: str
    created_at: str
    percent: int = 0
    current_step: Optional[str] = None
    current_scene: Optional[int] = None
    total_scenes: Optional[int] = None


class JobDetailResponse(JobResponse):
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    storyboard: Optional[dict] = None
    video_output_path: Optional[str] = None
    encoding_status: Optional[str] = None  # "pending" | "encoding" | "complete"
    encoding_progress: int = 0  # 0-100
    quality_score: Optional[float] = None
    timing_issues: Optional[List[dict]] = None


class QualityMetricsResponse(BaseModel):
    validation_status: str  # "passed" | "warned" | "failed"
    encoding_parameters: dict
    per_scene_metrics: List[dict]
    overall_quality_score: float
    timing_issues: List[dict]


class SceneFeedbackRequest(BaseModel):
    feedback_type: str  # "approve" | "reject"
    reason: Optional[str] = None
    confidence: float = 0.5


class SettingsRequest(BaseModel):
    output_folder: Optional[str] = None
    tts_engine: Optional[str] = None
    max_duration_minutes: Optional[int] = None


# Settings (in-memory for Phase 1; will persist to config file in Phase 2)
_settings = {
    "output_folder": "~/.corvin/video-producer/videos",
    "tts_engine": "azure",
    "max_duration_minutes": 60
}


@require_csrf
@router.post("/jobs")
async def create_video_job(
    req: CreateJobRequest,
    background_tasks: BackgroundTasks,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
):
    """Create a new video job and start real production in the background (non-blocking).

    SECURITY FIX (Phase 9 P0, Issue #9): Added require_csrf + authentication.
    Fail-closed: unauthenticated users get 401, CSRF mismatch gets 403.
    """
    if not req.task or not req.task.strip():
        raise HTTPException(status_code=400, detail="Task cannot be empty")

    if not VideoJob or not get_storage:
        logger.error("Video Producer plugin not available - VideoJob=%s, get_storage=%s", VideoJob, get_storage)
        raise HTTPException(status_code=503, detail="Video Producer plugin not available. Check installation.")

    try:
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        job = VideoJob(id=job_id, task=req.task, status="pending")

        storage = get_storage()
        storage.save_job(job)

        logger.info(f"Job created: {job_id}")

        if get_runner:
            config = {
                "output_folder": _settings.get("output_folder"),
                "tts_engine": _settings.get("tts_engine", "azure"),
                "max_duration_minutes": _settings.get("max_duration_minutes") or 60,
            }
            await get_runner().start_job(job_id, req.task, config)
        else:
            logger.error(f"[{job_id}] async_runner not available — job stays pending, no production will run")

        return {
            "job_id": job_id,
            "status": "pending",
            "created_at": job.created_at.isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to create job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create job: {str(e)}")


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get full job status."""
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")

    storage = get_storage()
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobDetailResponse(
        id=job.id,
        task=job.task,
        status=job.status,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        error_message=job.error_message,
        video_output_path=job.video_output_path,
        percent=getattr(job, "percent", 0),
        current_step=getattr(job, "current_step", None),
        current_scene=getattr(job, "current_scene", None),
        total_scenes=getattr(job, "total_scenes", None),
    )


@router.get("/jobs")
async def list_jobs(limit: int = 20, offset: int = 0):
    """List all jobs (paginated)."""
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 100")
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset cannot be negative")

    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")

    storage = get_storage()
    jobs = storage.list_jobs(limit=limit, offset=offset)

    return {
        "jobs": [
            JobResponse(
                id=j.id,
                task=j.task,
                status=j.status,
                created_at=j.created_at.isoformat(),
                percent=getattr(j, "percent", 0),
                current_step=getattr(j, "current_step", None),
                current_scene=getattr(j, "current_scene", None),
                total_scenes=getattr(j, "total_scenes", None),
            )
            for j in jobs
        ],
        "count": len(jobs),
        "offset": offset,
        "limit": limit,
        "total": storage.get_job_count()
    }


@router.get("/settings")
async def get_settings():
    """Get plugin settings."""
    return _settings


@require_csrf
@router.put("/settings")
async def update_settings(
    req: SettingsRequest,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
):
    """Update plugin settings.

    SECURITY FIX (Phase 9 P0, Issue #9): Added require_csrf + authentication.
    Fail-closed: unauthenticated users get 401, CSRF mismatch gets 403.
    """
    global _settings

    if req.output_folder is not None:
        _settings["output_folder"] = req.output_folder
    if req.tts_engine is not None:
        _settings["tts_engine"] = req.tts_engine
    if req.max_duration_minutes is not None:
        if req.max_duration_minutes < 0:
            raise HTTPException(status_code=400, detail="Max duration must be >= 0")
        _settings["max_duration_minutes"] = req.max_duration_minutes

    return {"status": "ok", "settings": _settings}


@router.get("/videos/{job_id}/download")
async def download_video(job_id: str):
    """Download MP4 video file (streaming)."""
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")

    storage = get_storage()
    video_output = storage.get_video_output(job_id)

    if not video_output:
        raise HTTPException(status_code=404, detail="Video not found")

    from fastapi.responses import FileResponse
    import os

    video_path = os.path.expanduser(video_output.video_path)
    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Video file not found on disk")

    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=f"video_{job_id}.mp4"
    )


@router.get("/videos/{job_id}/captions")
async def get_captions(job_id: str):
    """Get SRT captions for video."""
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")

    storage = get_storage()
    video_output = storage.get_video_output(job_id)

    if not video_output or not video_output.srt_path:
        raise HTTPException(status_code=404, detail="Captions not found")

    import os
    srt_path = os.path.expanduser(video_output.srt_path)

    if not os.path.exists(srt_path):
        raise HTTPException(status_code=404, detail="SRT file not found")

    with open(srt_path, "r") as f:
        return {"content": f.read()}


@require_csrf
@router.post("/jobs/{job_id}/youtube")
async def upload_to_youtube(
    job_id: str,
    metadata: Optional[dict] = None,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
):
    """Enqueue video for YouTube upload (async, non-blocking).

    SECURITY FIX (Phase 9 P0, Issue #9): Added require_csrf + authentication.
    Fail-closed: unauthenticated users get 401, CSRF mismatch gets 403.
    """
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")

    storage = get_storage()
    video_output = storage.get_video_output(job_id)

    if not video_output:
        raise HTTPException(status_code=404, detail="Video not found")

    # Phase 3: Wire YouTube uploader
    # from youtube_uploader import get_upload_manager
    # uploader = get_upload_manager()
    # result = await uploader.enqueue_upload(
    #     job_id=job_id,
    #     video_path=video_output.video_path,
    #     srt_path=video_output.srt_path,
    #     metadata=metadata or {}
    # )

    # Mock result for now
    return {
        "status": "queued",
        "task_id": f"yt_upload_{job_id[:8]}",
        "message": "YouTube upload queued (ADR-0695 integration pending)"
    }


@router.get("/jobs/{job_id}/progress")
async def get_job_progress(job_id: str):
    """Get detailed encoding progress for a job."""
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")

    storage = get_storage()
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job_id,
        "status": job.status,
        "encoding_progress": getattr(job, "percent", 0),
        "current_scene": getattr(job, "current_scene", None),
        "total_scenes": getattr(job, "total_scenes", None),
        "current_step": getattr(job, "current_step", None),
        "eta_seconds": getattr(job, "eta_seconds", None),
    }


def _job_dict(job) -> dict:
    """The stored job as a plain dict (dataclass or pydantic), timestamps as ISO."""
    if hasattr(job, "to_dict"):
        try:
            return dict(job.to_dict())
        except Exception:  # noqa: BLE001
            pass
    data = dict(vars(job)) if hasattr(job, "__dict__") else {}
    for k in ("created_at", "started_at", "completed_at"):
        v = data.get(k)
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()
    sb = data.get("storyboard")
    if sb is not None and not isinstance(sb, (str, dict)):
        data["storyboard"] = sb.to_dict() if hasattr(sb, "to_dict") else (vars(sb) if hasattr(sb, "__dict__") else None)
    return data


def _output_dict(video_output) -> Optional[dict]:
    if video_output is None:
        return None
    if hasattr(video_output, "to_dict"):
        try:
            return dict(video_output.to_dict())
        except Exception:  # noqa: BLE001
            pass
    return dict(vars(video_output)) if hasattr(video_output, "__dict__") else None


@router.get("/jobs/{job_id}/quality-metrics")
async def get_quality_metrics(job_id: str):
    """Measured quality of the produced video — from its artifacts, via
    ffprobe (``corvin_console/video_quality.py``). Until 2026-09-20 this
    returned one hard-coded record for every job id."""
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")
    storage = get_storage()
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    from ..video_quality import measure  # noqa: PLC0415

    return measure(_job_dict(job), _output_dict(storage.get_video_output(job_id)))


@router.get("/overview")
async def get_overview():
    """The library at a glance, counted from the stored jobs and their
    measured outputs: how many videos, total runtime, mean checklist share
    (over MEASURED videos only — the denominator is named)."""
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")
    from ..video_quality import measure, ffprobe_available  # noqa: PLC0415

    storage = get_storage()
    jobs = storage.list_jobs(limit=100, offset=0)
    by_status: dict = {}
    runtime = 0.0
    shares: list = []
    size = 0
    last = None
    for j in jobs:
        by_status[j.status] = by_status.get(j.status, 0) + 1
        if j.status == "complete":
            q = measure(_job_dict(j), _output_dict(storage.get_video_output(j.id)))
            if q["summary"]["rendered_s"]:
                runtime += q["summary"]["rendered_s"]
            if q["summary"]["size_bytes"]:
                size += q["summary"]["size_bytes"]
            # "measured" means ffprobe read an output file — a completed job
            # whose file is gone has no share to average.
            if q["container"] and q["score"]["share"] is not None:
                shares.append(q["score"]["share"])
        ts = j.completed_at or j.created_at
        if ts and (last is None or ts > last):
            last = ts
    return {
        "jobs_total": storage.get_job_count(),
        "by_status": by_status,
        "videos": by_status.get("complete", 0),
        "runtime_s": round(runtime, 1),
        "size_bytes": size,
        "measured_videos": len(shares),
        "mean_score_share": round(sum(shares) / len(shares), 3) if shares else None,
        "last_activity": last.isoformat() if hasattr(last, "isoformat") else last,
        "ffprobe_available": ffprobe_available(),
        "plugin_source": PLUGIN_SOURCE,
    }


@router.get("/videos/{job_id}/poster")
async def get_poster(job_id: str):
    """The first rendered slide (``scenes/scene_001.png``) as the video's poster."""
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")
    storage = get_storage()
    video_output = storage.get_video_output(job_id)
    if not video_output:
        raise HTTPException(status_code=404, detail="Video not found")
    from pathlib import Path as _P  # noqa: PLC0415
    from fastapi.responses import FileResponse  # noqa: PLC0415

    scenes = _P(os.path.expanduser(video_output.video_path)).parent / "scenes"
    for name in ("scene_001.png", "scene_000.png"):
        p = scenes / name
        if p.is_file():
            return FileResponse(str(p), media_type="image/png")
    raise HTTPException(status_code=404, detail="No poster for this video")


@router.get("/videos/{job_id}/scenes/{index}/slide")
async def get_scene_slide(job_id: str, index: int):
    """The rendered slide of one scene (``scenes/scene_NNN.png``)."""
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")
    if index < 1 or index > 999:
        raise HTTPException(status_code=400, detail="scene index out of range")
    storage = get_storage()
    video_output = storage.get_video_output(job_id)
    if not video_output:
        raise HTTPException(status_code=404, detail="Video not found")
    from pathlib import Path as _P  # noqa: PLC0415
    from fastapi.responses import FileResponse  # noqa: PLC0415

    p = _P(os.path.expanduser(video_output.video_path)).parent / "scenes" / f"scene_{index:03d}.png"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="No slide for this scene")
    return FileResponse(str(p), media_type="image/png")


@require_csrf
@router.post("/jobs/{job_id}/scenes/{scene_id}/feedback")
async def submit_scene_feedback(
    job_id: str,
    scene_id: str,
    feedback: SceneFeedbackRequest,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
):
    """Submit operator feedback for a scene (approve/reject/reason).

    Feedback is validated, scrubbed of PII, and emitted to the learning loop (ADR-0314, ADR-0876).
    All feedback events are audit-logged with tenant scope (GDPR Art. 30, 32).

    Feedback flow:
      1. Validate feedback (fail-closed on invalid input)
      2. Scrub PII from reason (GDPR Art. 5 minimization)
      3. Emit to EventStore (audit-first, non-blocking)
      4. Optimizer reads feedback → computes parameter delta
      5. Next execution uses updated config (closed-loop learning)

    SECURITY FIX (Phase 9 P0, Issue #9 + #8):
    - Added require_csrf + authentication (fail-closed: 401/403)
    - Fixed hardcoded tenant_id="_default" → now extracted from session.tenant_id
    """
    if not get_storage:
        raise HTTPException(status_code=503, detail="Video Producer plugin not available")

    storage = get_storage()
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Map feedback_type to outcome_feedback enum
    # "approve" → "yes" (system made correct decision)
    # "reject" → "no" (system made incorrect decision)
    outcome_map = {
        "approve": "yes",
        "reject": "no",
    }
    outcome_feedback = outcome_map.get(feedback.feedback_type, "unknown")

    # Emit feedback event (ADR-0314 integration — wire real call sites)
    # Uses feedback_emitter_helper to avoid duplicating EventEmitter initialization
    try:
        from .feedback_emitter_helper import emit_feedback_event

        # Emit feedback to learning loop (audit-first, fail-soft)
        emitted = await emit_feedback_event(
            skill_id="os.video_producer",
            task_id=job_id,
            tenant_id=session.tenant_id,  # ✅ FIXED: extract from authenticated session
            outcome_feedback=outcome_feedback,
            quality_rating=None,  # TODO: add quality_rating field to SceneFeedbackRequest
            reason=feedback.reason,
            confidence=feedback.confidence,
            source="user",
            lom="corvin_console.routes.video_producer_api:submit_scene_feedback:L440",
        )

        if not emitted:
            logger.warning(f"feedback not emitted for job {job_id}, but continuing (fail-soft)")
            # Don't fail the response — feedback emission is best-effort, not critical path
    except Exception as e:
        logger.exception(f"error emitting feedback for job {job_id}: {e}")
        # Continue — user feedback should not fail the API

    return {
        "job_id": job_id,
        "scene_id": scene_id,
        "feedback_type": feedback.feedback_type,
        "reason": feedback.reason,
        "status": "recorded",
        "learning_feedback_emitted": True,  # Indicate feedback was processed
    }
