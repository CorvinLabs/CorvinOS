"""HTTP Routes for LLM Video Synthesis — Phase 3

POST /v1/video/llm-generate — Generate video from brief
GET /v1/video/status/<job_id> — Check job status
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uuid

from ..llm_synthesis import SpecGenerator, validate_and_raise
from ..executor import execute_video_spec

logger = logging.getLogger(__name__)

# Job tracking
_jobs = {}

class VideoGenerateRequest(BaseModel):
    brief: str
    duration_sec: int = 60
    language: str = "de"
    audience: str = "general"
    tone: str = "professional"

class VideoGenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str

class VideoStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    output_file: str = None
    error: str = None

router = APIRouter(prefix="/v1/video", tags=["video-producer"])

@router.post("/llm-generate", response_model=VideoGenerateResponse)
async def generate_video(request: VideoGenerateRequest, background_tasks: BackgroundTasks):
    """Generate video from natural language brief

    Returns immediately with job_id, executes in background.
    Query /v1/video/status/<job_id> for status.
    """
    job_id = str(uuid.uuid4())[:8]

    logger.info(f"🎬 Video generation requested: {job_id} ({request.brief[:50]}...)")

    # Track job
    _jobs[job_id] = {
        "status": "PENDING",
        "progress": 0.0,
        "output_file": None,
        "error": None,
    }

    # Execute in background
    background_tasks.add_task(
        _execute_video_generation,
        job_id,
        request,
    )

    return VideoGenerateResponse(
        job_id=job_id,
        status="PENDING",
        message=f"Video generation started. Query /v1/video/status/{job_id} to check progress.",
    )

@router.get("/status/{job_id}", response_model=VideoStatusResponse)
async def video_status(job_id: str):
    """Check video generation job status"""

    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = _jobs[job_id]

    return VideoStatusResponse(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        output_file=job["output_file"],
        error=job["error"],
    )

async def _execute_video_generation(job_id: str, request: VideoGenerateRequest):
    """Background task: generate video from spec"""

    try:
        _jobs[job_id]["status"] = "GENERATING_SPEC"
        _jobs[job_id]["progress"] = 0.1

        # Phase 1: Generate spec
        generator = SpecGenerator()
        spec = generator.generate_spec(
            brief=request.brief,
            duration_sec=request.duration_sec,
            audience=request.audience,
            language=request.language,
            tone=request.tone,
        )

        _jobs[job_id]["progress"] = 0.3
        _jobs[job_id]["status"] = "VALIDATING_SPEC"

        # Validate
        spec = validate_and_raise(spec)

        _jobs[job_id]["progress"] = 0.4
        _jobs[job_id]["status"] = "RENDERING"

        # Phase 2: Execute pipeline
        output_mp4, stats = execute_video_spec(spec)

        _jobs[job_id]["progress"] = 0.9
        _jobs[job_id]["output_file"] = output_mp4
        _jobs[job_id]["status"] = "SUCCESS"

        logger.info(f"✅ Video generation complete: {job_id}")

    except Exception as e:
        logger.error(f"❌ Video generation failed: {job_id} — {e}")
        _jobs[job_id]["status"] = "ERROR"
        _jobs[job_id]["error"] = str(e)
        _jobs[job_id]["progress"] = -1.0
