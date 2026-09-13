"""FastAPI routes for Video Producer plugin."""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
import sys
import os
import logging
import importlib.util

logger = logging.getLogger(__name__)

# Try to import plugin modules from multiple locations
VideoJob = None
get_storage = None
get_runner = None

plugin_locations = [
    ("~/.corvin/plugins/video_producer/src", "installed"),
    ("../../../Corvin-Marketplace/plugins/contributor/video_producer/src", "dev-relative"),
    ("/home/shumway/projects/Corvin-Marketplace/plugins/contributor/video_producer/src", "dev-absolute"),
]

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


@router.post("/jobs")
async def create_video_job(req: CreateJobRequest, background_tasks: BackgroundTasks):
    """Create a new video job and start real production in the background (non-blocking)."""
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


@router.put("/settings")
async def update_settings(req: SettingsRequest):
    """Update plugin settings."""
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


@router.post("/jobs/{job_id}/youtube")
async def upload_to_youtube(job_id: str, metadata: Optional[dict] = None):
    """Enqueue video for YouTube upload (async, non-blocking)."""
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
