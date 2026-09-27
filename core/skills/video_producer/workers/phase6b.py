"""Phase 6b: Video Producer Workers — NOT IMPLEMENTED (fail-closed).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

This module used to live at ``core/skills/video_producer/workers.py``, where it
was shadowed by the ``workers/`` package and therefore unimportable. Worse, all
four workers were stubs that reported ``status="completed"`` with invented
artefacts: a ``/tmp/audio_<hash>.mp3`` that was never written, a screenshot
path that was never captured, an "encoded" video that ffmpeg never touched and
a ``https://youtube.com/watch?v=corvin_<hash>`` URL for an upload that never
happened. Every worker now FAILS with a ``not_implemented:`` error instead —
the real integrations live in the sibling modules (``openai_tts_worker``,
``screenshot_capturer``, ``video_assembler``, ``youtube_uploader``).

Four worker types for orchestration:
  - TTSWorker (text-to-speech synthesis)
  - ScreenshotWorker (screen capture)
  - FFmpegWorker (video encoding)
  - YouTubeWorker (upload to YouTube)

Ref: ADR-0206 Phase 6 Milestone 2
"""
from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime

#: Error prefix every stub worker fails with (callers match on it).
NOT_IMPLEMENTED = "not_implemented"


@dataclass
class WorkerResult:
    """Result from a worker execution (immutable)."""
    worker_id: str
    frame_id: str
    status: str  # "completed" | "failed" | "timeout"
    output: Optional[Dict] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()


class BaseWorker:
    """Base class for all workers (Phase 6b)."""

    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.execution_count = 0
        self.last_result: Optional[WorkerResult] = None

    def execute(self, worker_input: Dict) -> WorkerResult:
        """Execute worker task.

        Args:
            worker_input: Worker-specific input dict

        Returns:
            WorkerResult with status, output, and metadata
        """
        raise NotImplementedError

    def health_check(self) -> Dict:
        """Return health status of worker.

        Returns:
            Dict with status, execution_count, last_result timestamp
        """
        return {
            "worker_id": self.worker_id,
            # Nothing here can do real work, so health is not measured —
            # never report "healthy" for a stub.
            "status": "not_measured",
            "execution_count": self.execution_count,
            "last_execution": self.last_result.created_at if self.last_result else None,
        }


class TTSWorker(BaseWorker):
    """Text-to-Speech worker (Phase 6b).

    Converts text input to audio output (WAV/MP3).
    """

    def __init__(self):
        super().__init__("tts")

    def execute(self, worker_input: Dict) -> WorkerResult:
        """Execute TTS synthesis.

        Args:
            worker_input: {
                "text": "Voice content",
                "voice": "en-US-Neural2-C",  # Google Cloud voice
                "output_format": "mp3"  # mp3, wav, ogg
            }

        Returns:
            WorkerResult with audio_path in output
        """
        import time
        start_time = time.time()

        try:
            text = worker_input.get("text", "")

            if not text:
                return WorkerResult(
                    worker_id=self.worker_id,
                    frame_id=worker_input.get("frame_id", "unknown"),
                    status="failed",
                    error="Missing text input",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # No real integration exists in this module — fail closed rather
            # than report an artefact that was never produced.
            result = WorkerResult(
                worker_id=self.worker_id,
                frame_id=worker_input.get("frame_id", "unknown"),
                status="failed",
                error=f"{NOT_IMPLEMENTED}: {self.worker_id} worker has no backend in phase6b",
                duration_ms=(time.time() - start_time) * 1000,
            )

            self.execution_count += 1
            self.last_result = result
            return result

        except Exception as e:
            return WorkerResult(
                worker_id=self.worker_id,
                frame_id=worker_input.get("frame_id", "unknown"),
                status="failed",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )


class ScreenshotWorker(BaseWorker):
    """Screenshot worker (Phase 6b).

    Captures screen or webpage screenshot.
    """

    def __init__(self):
        super().__init__("screenshot")

    def execute(self, worker_input: Dict) -> WorkerResult:
        """Execute screenshot capture.

        Args:
            worker_input: {
                "url": "https://example.com",  # or empty for screen capture
                "format": "png",  # png, jpg
                "width": 1920,
                "height": 1080
            }

        Returns:
            WorkerResult with image_path in output
        """
        import time
        start_time = time.time()

        try:

            # No real integration exists in this module — fail closed rather
            # than report an artefact that was never produced.
            result = WorkerResult(
                worker_id=self.worker_id,
                frame_id=worker_input.get("frame_id", "unknown"),
                status="failed",
                error=f"{NOT_IMPLEMENTED}: {self.worker_id} worker has no backend in phase6b",
                duration_ms=(time.time() - start_time) * 1000,
            )

            self.execution_count += 1
            self.last_result = result
            return result

        except Exception as e:
            return WorkerResult(
                worker_id=self.worker_id,
                frame_id=worker_input.get("frame_id", "unknown"),
                status="failed",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )


class FFmpegWorker(BaseWorker):
    """FFmpeg worker (Phase 6b).

    Encodes video, muxes streams, applies filters.
    """

    def __init__(self):
        super().__init__("ffmpeg")

    def execute(self, worker_input: Dict) -> WorkerResult:
        """Execute FFmpeg encoding.

        Args:
            worker_input: {
                "input_paths": ["audio.mp3", "video.mp4"],
                "output_path": "final.mp4",
                "codec": "h264",  # h264, h265, vp9
                "bitrate": "2M"
            }

        Returns:
            WorkerResult with output_path in output
        """
        import time
        start_time = time.time()

        try:
            input_paths = worker_input.get("input_paths", [])

            if not input_paths:
                return WorkerResult(
                    worker_id=self.worker_id,
                    frame_id=worker_input.get("frame_id", "unknown"),
                    status="failed",
                    error="Missing input paths",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # No real integration exists in this module — fail closed rather
            # than report an artefact that was never produced.
            result = WorkerResult(
                worker_id=self.worker_id,
                frame_id=worker_input.get("frame_id", "unknown"),
                status="failed",
                error=f"{NOT_IMPLEMENTED}: {self.worker_id} worker has no backend in phase6b",
                duration_ms=(time.time() - start_time) * 1000,
            )

            self.execution_count += 1
            self.last_result = result
            return result

        except Exception as e:
            return WorkerResult(
                worker_id=self.worker_id,
                frame_id=worker_input.get("frame_id", "unknown"),
                status="failed",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )


class YouTubeWorker(BaseWorker):
    """YouTube upload worker (Phase 6b).

    Uploads video to YouTube channel.
    """

    def __init__(self):
        super().__init__("youtube")

    def execute(self, worker_input: Dict) -> WorkerResult:
        """Execute YouTube upload.

        Args:
            worker_input: {
                "video_path": "final.mp4",
                "title": "My Video",
                "description": "Video description",
                "tags": ["tag1", "tag2"],
                "visibility": "private"  # private, unlisted, public
            }

        Returns:
            WorkerResult with video_id in output
        """
        import time
        start_time = time.time()

        try:
            video_path = worker_input.get("video_path", "")

            if not video_path:
                return WorkerResult(
                    worker_id=self.worker_id,
                    frame_id=worker_input.get("frame_id", "unknown"),
                    status="failed",
                    error="Missing video path",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            # No real integration exists in this module — fail closed rather
            # than report an artefact that was never produced.
            result = WorkerResult(
                worker_id=self.worker_id,
                frame_id=worker_input.get("frame_id", "unknown"),
                status="failed",
                error=f"{NOT_IMPLEMENTED}: {self.worker_id} worker has no backend in phase6b",
                duration_ms=(time.time() - start_time) * 1000,
            )

            self.execution_count += 1
            self.last_result = result
            return result

        except Exception as e:
            return WorkerResult(
                worker_id=self.worker_id,
                frame_id=worker_input.get("frame_id", "unknown"),
                status="failed",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )


# Worker Registry (Phase 6b)
WORKERS = {
    "tts": TTSWorker,
    "screenshot": ScreenshotWorker,
    "ffmpeg": FFmpegWorker,
    "youtube": YouTubeWorker,
}


def get_worker(worker_type: str) -> BaseWorker:
    """Get worker instance by type.

    Args:
        worker_type: "tts" | "screenshot" | "ffmpeg" | "youtube"

    Returns:
        Initialized worker instance

    Raises:
        ValueError: If worker type not found
    """
    if worker_type not in WORKERS:
        raise ValueError(f"Unknown worker type: {worker_type}")
    return WORKERS[worker_type]()
