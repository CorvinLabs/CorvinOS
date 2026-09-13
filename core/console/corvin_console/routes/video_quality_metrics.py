"""Video Quality Metrics API endpoint."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
from pathlib import Path

router = APIRouter(prefix="/video", tags=["video-quality"])

# In-memory store (Phase 1: simple storage)
_quality_metrics: Dict[str, Dict[str, Any]] = {}


class QualityMetrics(BaseModel):
    """Quality metrics for a job."""
    job_id: str
    status: str
    validation: Dict[str, Any]
    encoding: Dict[str, Any]
    color: Dict[str, Any]
    per_scene: List[Dict[str, Any]] = []


@router.get("/jobs/{job_id}/quality-metrics")
async def get_quality_metrics(job_id: str) -> QualityMetrics:
    """Get quality metrics for a job."""

    # Check if we have cached metrics
    if job_id in _quality_metrics:
        return QualityMetrics(**_quality_metrics[job_id])

    # For Phase 1: return mock metrics (proof of concept)
    return QualityMetrics(
        job_id=job_id,
        status="complete",
        validation={
            "passed": 8,
            "warned": 0,
            "failed": 0,
        },
        encoding={
            "codec": "h264",
            "resolution": "1080p",
            "bitrate": "7200k",
            "ffmpeg_preset": "medium",
        },
        color={
            "input_space": "sRGB",
            "output_space": "BT.709",
        },
        per_scene=[
            {
                "scene_id": "s1",
                "validation_status": "pass",
                "validation_confidence": 0.95,
                "encoding_codec": "h264",
                "encoding_bitrate": "6000k",
            },
            {
                "scene_id": "s2",
                "validation_status": "pass",
                "validation_confidence": 0.87,
                "encoding_codec": "h264",
                "encoding_bitrate": "8000k",
            },
            {
                "scene_id": "s3",
                "validation_status": "warn",
                "validation_confidence": 0.75,
                "encoding_codec": "h265",
                "encoding_bitrate": "7000k",
            },
        ]
    )


@router.post("/jobs/{job_id}/quality-metrics")
async def store_quality_metrics(job_id: str, metrics: QualityMetrics) -> Dict[str, str]:
    """Store quality metrics for a job."""
    _quality_metrics[job_id] = metrics.dict()
    return {"status": "ok", "job_id": job_id}
