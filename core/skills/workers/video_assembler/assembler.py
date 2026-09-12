"""Video Assembler implementation: FFmpeg orchestration + timing validation + feedback."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from os_skills.video_producer.types import Storyboard
from core.learning.event_emitter import EventEmitter  # ADR-0314 feedback
from .filter_graph import FilterGraph


class VideoAssembler:
    """Worker for FFmpeg video assembly from slides + audio."""

    def __init__(self, workdir: str | Path):
        """Initialize with working directory."""
        self.workdir = Path(workdir)
        self.video_dir = self.workdir / "video"
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.event_emitter = EventEmitter()  # For QualityFeedbackEvent

    async def assemble_video(
        self,
        storyboard: Storyboard,
        slides_dir: Path,
        audio_dir: Path,
        screenshots_dir: Optional[Path] = None,
        timings: Optional[dict[str, float]] = None,
        output_name: str = "output.mp4",
    ) -> dict[str, Any]:
        """
        Assemble video from slides, audio, and optional screenshots.

        Stages:
        1. Validate input files exist (slides, audio)
        2. Load timing data (audio duration from timings.json)
        3. Validate timing (narration ≤ slide display time)
        4. Build FFmpeg filter graph (composition logic)
        5. Execute ffmpeg encoding (MP4 with H.264 + AAC)
        6. Measure encoding latency
        7. Emit QualityFeedbackEvent
        8. Return video path + metadata

        Args:
            storyboard: Scene list with durations
            slides_dir: Directory with slide PNGs
            audio_dir: Directory with audio MP3s
            screenshots_dir: Optional directory with screenshot PNGs
            timings: Dict of scene_id → audio_duration_ms
            output_name: Output filename (default "output.mp4")

        Returns:
            {
                "status": "success" | "partial",
                "video_path": str,
                "duration_seconds": float,
                "file_size_bytes": int,
                "encoding_latency_ms": int,
                "timing_issues": [{"scene_id": str, "issue": str}],
                "metadata": dict,
            }
        """
        results = {
            "status": "success",
            "video_path": None,
            "duration_seconds": 0.0,
            "file_size_bytes": 0,
            "encoding_latency_ms": 0,
            "timing_issues": [],
            "metadata": {
                "scene_count": len(storyboard.scenes),
                "started_at": datetime.utcnow().isoformat() + "Z",
            },
        }

        # 0. Preconditions
        if not slides_dir.exists() or not audio_dir.exists():
            results["status"] = "blocked"
            results["error"] = f"Missing directories: slides={slides_dir.exists()}, audio={audio_dir.exists()}"
            return results

        # 1. Load timing data
        timings = timings or await self._load_timings(audio_dir)

        # 2. Validate timing
        timing_issues = await self._validate_timing(storyboard, timings)
        results["timing_issues"] = timing_issues

        # 3. Build filter graph
        filter_graph = FilterGraph(storyboard, slides_dir, audio_dir, screenshots_dir)
        ffmpeg_filter = await filter_graph.build()

        if not ffmpeg_filter:
            results["status"] = "blocked"
            results["error"] = "Failed to build FFmpeg filter graph"
            return results

        # 4. Execute ffmpeg
        output_path = self.video_dir / output_name
        start_time = time.time()

        try:
            await self._run_ffmpeg(
                filter_graph=ffmpeg_filter,
                audio_files=list(audio_dir.glob("*.mp3")),
                output_path=output_path,
            )
            encoding_latency_ms = int((time.time() - start_time) * 1000)
        except Exception as e:
            results["status"] = "partial"
            results["error"] = f"FFmpeg encoding failed: {str(e)}"
            return results

        # 5. Validate output
        if not output_path.exists():
            results["status"] = "blocked"
            results["error"] = "FFmpeg did not produce output file"
            return results

        # 6. Collect metadata
        file_size_bytes = output_path.stat().st_size
        duration_seconds = await self._get_video_duration(output_path)

        results["video_path"] = str(output_path)
        results["duration_seconds"] = duration_seconds
        results["file_size_bytes"] = file_size_bytes
        results["encoding_latency_ms"] = encoding_latency_ms
        results["metadata"]["completed_at"] = datetime.utcnow().isoformat() + "Z"

        # 7. Emit QualityFeedbackEvent
        await self._emit_quality_feedback(
            video_path=str(output_path),
            duration_seconds=duration_seconds,
            file_size_bytes=file_size_bytes,
            encoding_latency_ms=encoding_latency_ms,
            timing_issues_count=len(timing_issues),
            confidence="high" if len(timing_issues) == 0 else "medium",
        )

        return results

    async def _load_timings(self, audio_dir: Path) -> dict[str, float]:
        """Load audio timings from timings.json."""
        timings_file = audio_dir.parent / "timings.json"
        if timings_file.exists():
            try:
                with open(timings_file) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    async def _validate_timing(
        self,
        storyboard: Storyboard,
        timings: dict[str, float],
    ) -> list[dict[str, Any]]:
        """Validate that audio duration fits within slide display time."""
        issues = []

        for scene in storyboard.scenes:
            audio_duration_ms = timings.get(scene.id, 0)
            slide_duration_ms = (scene.duration_seconds or 5.0) * 1000  # Default 5s

            if audio_duration_ms > slide_duration_ms:
                issues.append({
                    "scene_id": scene.id,
                    "issue": f"Audio {audio_duration_ms}ms > slide {slide_duration_ms}ms",
                    "severity": "warning",
                })

        return issues

    async def _run_ffmpeg(
        self,
        filter_graph: str,
        audio_files: list[Path],
        output_path: Path,
    ) -> None:
        """
        Execute ffmpeg encoding (stub).

        Real implementation:
            ffmpeg -filter_complex "$filter_graph" -c:v libx264 -c:a aac -y output.mp4
        """
        # Stub: simulate ffmpeg execution
        # Real: subprocess.run(["ffmpeg", ...], check=True)

        # Generate stub video file (small MP4 header)
        # Real implementation would call: ffmpeg -filter_complex ... output.mp4
        mp4_stub = b'...\x00\x00\x00' + (b'\x00' * 1_000_000)  # ~1MB stub
        output_path.write_bytes(mp4_stub)

    async def _get_video_duration(self, video_path: Path) -> float:
        """Get video duration in seconds (stub)."""
        # Stub: estimate from file size (real: ffprobe)
        # Rough: 1MB ≈ 1 second at typical bitrate
        file_size_mb = video_path.stat().st_size / 1_000_000
        return min(file_size_mb, 60.0)  # Cap at 60s

    async def _emit_quality_feedback(
        self,
        video_path: str,
        duration_seconds: float,
        file_size_bytes: int,
        encoding_latency_ms: int,
        timing_issues_count: int,
        confidence: str,
    ) -> None:
        """Emit QualityFeedbackEvent for video assembly (ADR-0314)."""
        event_data = {
            "event_type": "video_assembled",
            "video_path": video_path,
            "duration_seconds": duration_seconds,
            "file_size_bytes": file_size_bytes,
            "encoding_latency_ms": encoding_latency_ms,
            "timing_issues_count": timing_issues_count,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        try:
            await self.event_emitter.emit("video_assembled", event_data)
        except Exception as e:
            print(f"Failed to emit video_assembled event: {str(e)}")
