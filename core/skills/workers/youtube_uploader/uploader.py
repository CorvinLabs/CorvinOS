"""YouTube Uploader implementation: Async video upload + metadata + captions."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.learning.learning_events import EventType
from core.skills.workers._learning_emit import build_event_emitter, emit_worker_event
from .youtube_api import YouTubeAPI


class YouTubeUploader:
    """Worker for async YouTube video upload."""

    def __init__(
        self,
        workdir: str | Path,
        oauth_token: Optional[str] = None,
        tenant_id: str = "_default",
    ):
        """Initialize with working directory and OAuth token."""
        self.workdir = Path(workdir)
        self.upload_dir = self.workdir / "youtube"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.tenant_id = tenant_id
        self.event_emitter = build_event_emitter(tenant_id)
        self.youtube_api = YouTubeAPI(oauth_token=oauth_token)
        self.active_uploads = {}  # task_id → upload_status
        # asyncio only holds a WEAK reference to a bare create_task() result --
        # without keeping a strong reference somewhere, the task can be
        # garbage-collected before it runs. Keyed by task_id so it can be
        # dropped once the upload finishes.
        self._background_tasks: dict[str, asyncio.Task] = {}

    async def enqueue_upload(
        self,
        video_path: str | Path,
        metadata: dict[str, Any],
        srt_path: Optional[str | Path] = None,
    ) -> dict[str, Any]:
        """
        Enqueue video for async YouTube upload (non-blocking).

        Stages:
        1. Validate video file exists and is readable
        2. Validate OAuth credentials
        3. Create Task object for tracking
        4. Enqueue upload in background
        5. Return immediately with task_id
        6. Emit UploadEnqueuedEvent (ADR-0314)

        Args:
            video_path: Path to MP4 video file
            metadata: Title, description, tags, privacy
            srt_path: Optional path to SRT subtitle file

        Returns:
            {
                "status": "queued" | "error",
                "task_id": str,
                "video_path": str,
                "metadata": dict,
                "expected_duration_minutes": float,
                "callback_url": str | None,
            }
        """
        video_path = Path(video_path)

        # 0. Preconditions
        if not video_path.exists():
            return {
                "status": "error",
                "error": f"Video file not found: {video_path}",
                "task_id": None,
            }

        if not await self.youtube_api.is_authenticated():
            return {
                "status": "error",
                "error": "YouTube API not authenticated",
                "task_id": None,
            }

        # 1. Create task ID
        task_id = str(uuid.uuid4())[:8]

        # 2. Validate file size (must be < 256GB for YouTube)
        file_size_gb = video_path.stat().st_size / (1024**3)
        if file_size_gb > 256:
            return {
                "status": "error",
                "error": f"Video too large: {file_size_gb:.1f}GB > 256GB",
                "task_id": None,
            }

        # 3. Estimate upload time
        # Assumed average effective upload throughput: 5 MB/minute.
        # (A stray extra "* 60" here used to divide the result by another 60x,
        # turning a real "10MB ~= 2 minutes" estimate into ~0.03 minutes.)
        throughput_mb_per_minute = 5.0
        file_size_mb = video_path.stat().st_size / (1024**2)
        estimated_minutes = file_size_mb / throughput_mb_per_minute

        # 4. Create upload tracking record
        upload_record = {
            "task_id": task_id,
            "video_path": str(video_path),
            "metadata": metadata,
            "srt_path": str(srt_path) if srt_path else None,
            "status": "queued",
            "progress_percent": 0,
            "started_at": None,
            "completed_at": None,
            "youtube_url": None,
            "error": None,
        }
        self.active_uploads[task_id] = upload_record

        # 5. Save upload record to disk for persistence
        record_path = self.upload_dir / f"{task_id}.json"
        with open(record_path, "w") as f:
            json.dump(upload_record, f, indent=2)

        # 6. Emit UploadEnqueuedEvent (ADR-0314)
        await self._emit_upload_enqueued(
            task_id=task_id,
            video_path=str(video_path),
            file_size_mb=file_size_mb,
            estimated_minutes=estimated_minutes,
        )

        # 7. Enqueue upload in the background (non-blocking)
        background_task = asyncio.create_task(self._upload_background(task_id))
        self._background_tasks[task_id] = background_task
        background_task.add_done_callback(lambda _t: self._background_tasks.pop(task_id, None))

        return {
            "status": "queued",
            "task_id": task_id,
            "video_path": str(video_path),
            "metadata": metadata,
            "expected_duration_minutes": estimated_minutes,
            "callback_url": None,  # In real: webhook for completion notification
        }

    async def get_upload_status(self, task_id: str) -> dict[str, Any]:
        """Get current status of an upload task."""
        if task_id not in self.active_uploads:
            # Try to load from disk
            record_path = self.upload_dir / f"{task_id}.json"
            if record_path.exists():
                with open(record_path) as f:
                    return json.load(f)
            return {"error": f"Task not found: {task_id}"}

        return self.active_uploads[task_id]

    async def _upload_background(self, task_id: str) -> None:
        """Background upload task (runs via asyncio.create_task or Task API worker)."""
        upload_record = self.active_uploads.get(task_id)
        if not upload_record:
            return

        try:
            upload_record["status"] = "uploading"
            upload_record["started_at"] = datetime.utcnow().isoformat() + "Z"

            video_path = Path(upload_record["video_path"])
            srt_path = upload_record.get("srt_path")
            metadata = upload_record["metadata"]

            await self._emit_upload_progress(task_id=task_id, progress_percent=10)
            upload_record["progress_percent"] = 10

            # Real call -- never fabricates a video id. `status` is one of
            # "uploaded" (real id), "not_configured" (no credentials -- the
            # local-only mode default), or "error" (a real API failure).
            api_result = await self.youtube_api.upload_video(
                video_path=str(video_path),
                title=metadata.get("title", ""),
                description=metadata.get("description", ""),
                tags=metadata.get("tags", []),
                privacy=metadata.get("privacy", "private"),
            )

            upload_record["progress_percent"] = 100
            upload_record["completed_at"] = datetime.utcnow().isoformat() + "Z"
            upload_record["youtube_video_id"] = api_result.get("video_id")
            upload_record["youtube_url"] = api_result.get("url")

            if api_result["status"] == "uploaded":
                if srt_path:
                    await self.youtube_api.upload_captions(api_result["video_id"], str(srt_path))
                upload_record["status"] = "completed"
                await self._emit_upload_completed(
                    task_id=task_id, youtube_url=upload_record["youtube_url"],
                )
            elif api_result["status"] == "not_configured":
                upload_record["status"] = "skipped"
                upload_record["error"] = api_result.get("reason")
                await self._emit_upload_error(
                    task_id=task_id, error=f"skipped: {api_result.get('reason')}",
                )
            else:
                upload_record["status"] = "failed"
                upload_record["error"] = api_result.get("reason")
                await self._emit_upload_error(task_id=task_id, error=str(api_result.get("reason")))

        except Exception as e:
            upload_record["status"] = "failed"
            upload_record["error"] = str(e)
            upload_record["completed_at"] = datetime.utcnow().isoformat() + "Z"

            # Emit error event
            await self._emit_upload_error(task_id=task_id, error=str(e))

        finally:
            # Save final state to disk
            record_path = self.upload_dir / f"{task_id}.json"
            with open(record_path, "w") as f:
                json.dump(upload_record, f, indent=2)

    async def validate_quality(
        self,
        video_metadata: dict[str, Any],
        scene_feedbacks: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """
        Validate video quality before upload (Phase 4a precondition).

        Checks:
        1. Overall quality_score ≥ 0.70
        2. Per-scene encoding_confidence ≥ 0.85
        3. No critical timing_issues

        Args:
            video_metadata: From video_assembler output
            scene_feedbacks: Optional per-scene feedback events

        Returns:
            {
                "valid": bool,
                "quality_score": float,
                "issues": [str],
                "per_scene_status": {scene_id: {confidence, status}}
            }
        """
        issues = []
        per_scene_status = {}

        # Extract quality score
        quality_score = video_metadata.get("quality_score", 0.5)
        if quality_score < 0.70:
            issues.append(f"Quality score too low: {quality_score:.2f} < 0.70")

        # Check scene-level confidence
        if scene_feedbacks:
            for feedback in scene_feedbacks:
                scene_id = feedback.get("scene_id", "unknown")
                confidence = feedback.get("confidence", 0.5)

                if confidence < 0.85:
                    issues.append(
                        f"Scene {scene_id} confidence too low: {confidence:.2f} < 0.85"
                    )
                per_scene_status[scene_id] = {
                    "confidence": confidence,
                    "status": "pass" if confidence >= 0.85 else "fail",
                }

        # Check timing issues
        timing_issues = video_metadata.get("timing_issues", [])
        if timing_issues:
            for issue in timing_issues:
                if issue.get("severity") == "error":
                    issues.append(f"Critical timing issue: {issue.get('issue', 'unknown')}")

        return {
            "valid": len(issues) == 0,
            "quality_score": quality_score,
            "issues": issues,
            "per_scene_status": per_scene_status,
        }

    async def _emit_upload_enqueued(
        self,
        task_id: str,
        video_path: str,
        file_size_mb: float,
        estimated_minutes: float,
    ) -> None:
        """Emit UploadEnqueuedEvent (ADR-0314)."""
        event_data = {
            "event_type": "upload_enqueued",
            "task_id": task_id,
            "video_path": video_path,
            "file_size_mb": file_size_mb,
            "estimated_minutes": estimated_minutes,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        emit_worker_event(
            self.event_emitter,
            event_type=EventType.UPLOAD_PROGRESS,
            skill_id="os.video_producer.youtube_uploader",
            tenant_id=self.tenant_id,
            signal={"status": "enqueued", **event_data},
        )

    async def _emit_upload_progress(
        self,
        task_id: str,
        progress_percent: int,
    ) -> None:
        """Emit UploadProgressEvent (ADR-0314)."""
        event_data = {
            "event_type": "upload_progress",
            "task_id": task_id,
            "progress_percent": progress_percent,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        emit_worker_event(
            self.event_emitter,
            event_type=EventType.UPLOAD_PROGRESS,
            skill_id="os.video_producer.youtube_uploader",
            tenant_id=self.tenant_id,
            signal={"status": "progress", **event_data},
        )

    async def _emit_upload_completed(
        self,
        task_id: str,
        youtube_url: str,
    ) -> None:
        """Emit UploadCompletedEvent (ADR-0314)."""
        event_data = {
            "event_type": "upload_completed",
            "task_id": task_id,
            "youtube_url": youtube_url,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        emit_worker_event(
            self.event_emitter,
            event_type=EventType.UPLOAD_PROGRESS,
            skill_id="os.video_producer.youtube_uploader",
            tenant_id=self.tenant_id,
            signal={"status": "completed", **event_data},
        )

    async def _emit_upload_error(
        self,
        task_id: str,
        error: str,
    ) -> None:
        """Emit UploadErrorEvent (ADR-0314)."""
        event_data = {
            "event_type": "upload_error",
            "task_id": task_id,
            "error": error,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        emit_worker_event(
            self.event_emitter,
            event_type=EventType.UPLOAD_PROGRESS,
            skill_id="os.video_producer.youtube_uploader",
            tenant_id=self.tenant_id,
            signal={"status": "error", **event_data},
        )
