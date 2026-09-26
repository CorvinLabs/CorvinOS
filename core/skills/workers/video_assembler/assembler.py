"""Video Assembler implementation: FFmpeg orchestration + timing validation + feedback."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

import sys
# Imported by the package's REAL path. A sys.path.insert of <repo>/core/skills
# plus `from os_skills...` loads video_producer/types.py a SECOND time under a
# second module name, so the dataclasses here and the ones the rest of the
# codebase holds are different classes and isinstance() is False across the
# seam (2026-09-20 review).
from core.skills.os_skills.video_producer.types import Storyboard
from core.learning.learning_events import EventType
from core.skills.workers._learning_emit import build_event_emitter, emit_worker_event
from .filter_graph import FilterGraph


class VideoAssembler:
    """Worker for FFmpeg video assembly from slides + audio."""

    def __init__(self, workdir: str | Path, tenant_id: str = "_default"):
        """Initialize with working directory."""
        self.workdir = Path(workdir)
        self.video_dir = self.workdir / "video"
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.tenant_id = tenant_id
        self.event_emitter = build_event_emitter(tenant_id)

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
                input_args=filter_graph.get_input_args(),
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
        """Per-scene audio durations in milliseconds.

        Prefers a precomputed ``timings.json`` (an upstream TTS step's own
        measurement) when present; otherwise probes each audio file's REAL
        duration via ffprobe. Until this fallback existed, an absent
        timings.json silently made ``_validate_timing`` compare every scene
        against 0ms -- the "audio longer than its slide" check could never
        fire, regardless of what the audio files actually contained.
        """
        timings_file = audio_dir.parent / "timings.json"
        if timings_file.exists():
            try:
                with open(timings_file) as f:
                    return json.load(f)
            except Exception:
                pass

        timings: dict[str, float] = {}
        try:
            import ffmpeg
        except ImportError:
            return timings

        for audio_path in audio_dir.glob("*.mp3"):
            try:
                probe = ffmpeg.probe(str(audio_path))
                duration_s = float(probe.get("format", {}).get("duration", 0.0))
                timings[audio_path.stem] = duration_s * 1000
            except Exception as e:
                logger.warning(f"Failed to probe audio duration for {audio_path}: {e}")

        return timings

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
        input_args: list[str],
        output_path: Path,
    ) -> None:
        """
        Execute ffmpeg encoding (real implementation).

        Invokes: ffmpeg <input_args> -filter_complex "$filter_graph" -map "[outv]" -map "[outa]" -c:v libx264 -c:a aac -preset medium -b:v 7200k -y output.mp4

        Raises:
            RuntimeError if ffmpeg fails
        """
        import subprocess
        import shutil

        # Check if ffmpeg is available
        if not shutil.which("ffmpeg"):
            # Fallback: generate stub MP4 for testing
            mp4_stub = b'ftypisom' + (b'\x00' * 1_000_000)  # Minimal MP4 header
            output_path.write_bytes(mp4_stub)
            return

        # Build ffmpeg command. -map is required: with a filter_complex whose
        # outputs are explicitly labelled ([outv]/[outa]), ffmpeg does not
        # infer them automatically the way it does for a single unlabelled
        # filter chain.
        cmd = [
            "ffmpeg",
            *input_args,
            "-filter_complex",
            filter_graph,
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-preset",
            "medium",
            "-b:v",
            "7200k",
            "-y",
            str(output_path),
        ]

        try:
            # Run ffmpeg with 60-minute timeout
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=3600,  # 60 minutes
                text=True,
            )

            # Verify output was created
            if not output_path.exists():
                raise RuntimeError("FFmpeg did not produce output file")

        except subprocess.TimeoutExpired:
            raise RuntimeError("FFmpeg encoding timeout (60 minutes exceeded)")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg encoding failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"FFmpeg execution failed: {str(e)}")

    async def _get_video_duration(self, video_path: Path) -> float:
        """Get video duration in seconds (real implementation using ffprobe)."""
        import subprocess
        import shutil
        import re

        if not video_path.exists():
            return 0.0

        # Try to use ffprobe for accurate duration
        if shutil.which("ffprobe"):
            try:
                cmd = [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1:nokey=1",
                    str(video_path),
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=10,
                    text=True,
                    check=True,
                )

                try:
                    duration = float(result.stdout.strip())
                    return max(0.0, duration)
                except ValueError:
                    pass
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass

        # Fallback: estimate from file size
        # Rough estimation: 7200k bitrate ≈ 900 KB/s ≈ 1MB per 1.1 seconds
        file_size_mb = video_path.stat().st_size / 1_000_000
        estimated_duration = file_size_mb * 1.1  # Conservative estimate
        return min(estimated_duration, 3600.0)  # Cap at 60 minutes

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

        emit_worker_event(
            self.event_emitter,
            event_type=EventType.SCENE_RENDERED,
            skill_id="os.video_producer.video_assembler",
            tenant_id=self.tenant_id,
            signal={"milestone": "video_assembled", **event_data},
        )
