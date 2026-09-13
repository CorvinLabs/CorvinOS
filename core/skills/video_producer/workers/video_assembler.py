"""Video Assembler Worker: Phase 3 Video Composition and Encoding

Assembles video from audio + screenshots using FFmpeg:
1. Create frame composition (narration + screenshot)
2. Sync audio with video
3. Add subtitles (SRT format)
4. Encode with H.264 + AAC (broadcast quality)
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
import subprocess
import json
import os


@dataclass
class VideoResult:
    """Video assembly result"""
    video_path: str
    duration_seconds: float
    bitrate_kbps: int
    codec: str
    quality_score: float
    success: bool = True


class VideoAssemblerWorker:
    """Worker Skill: Assemble video from audio + screenshots

    Supports:
    - FFmpeg-based composition
    - Multiple video codecs (H.264, VP9, AV1)
    - Subtitle embedding (SRT)
    - Resolution options (720p, 1080p, 4K)
    - Quality presets (medium, high, very-high)
    """

    def __init__(self, codec: str = "h264", preset: str = "medium"):
        self.name = "video_assembler"
        self.version = "2.0.0"
        self.codec = codec
        self.preset = preset

    def execute(self, job, voice_result=None, screenshot_result=None) -> VideoResult:
        """Execute video assembly phase

        Args:
            job: VideoJob instance
            voice_result: VoiceResult from Voice Synthesizer
            screenshot_result: ScreenshotResult from Screenshot Capturer

        Returns:
            VideoResult with video path and metadata
        """

        output_path = f"/tmp/{job.job_id}_final.mp4"

        # Get components from job results (fallback if not passed)
        if voice_result is None:
            voice_result = job.voice_result or {}
        if screenshot_result is None:
            screenshot_result = job.screenshots_result or {}

        # Extract data from VoiceResult/ScreenshotResult objects or dicts
        if isinstance(voice_result, dict):
            audio_files = voice_result.get("audio_files", [])
            voice_duration = voice_result.get("total_duration_seconds", 60)
        else:
            audio_files = voice_result.audio_files if voice_result else []
            voice_duration = voice_result.total_duration_seconds if voice_result else 60

        if isinstance(screenshot_result, dict):
            screenshot_files = screenshot_result.get("screenshots", [])
        else:
            screenshot_files = screenshot_result.screenshots if screenshot_result else []

        # Build FFmpeg command
        ffmpeg_cmd = self._build_ffmpeg_command(
            audio_files=audio_files,
            screenshot_files=screenshot_files,
            output_path=output_path,
            job=job,
        )

        # Phase 3: Mock (don't actually execute FFmpeg)
        # In production: subprocess.run(ffmpeg_cmd, check=True)

        # Create mock output file
        with open(output_path, "w") as f:
            json.dump(
                {
                    "video_path": output_path,
                    "duration": voice_duration,
                    "codec": self.codec,
                    "bitrate_kbps": 2500,
                },
                f,
            )

        return VideoResult(
            video_path=output_path,
            duration_seconds=voice_duration,
            bitrate_kbps=2500,
            codec=self.codec,
            quality_score=0.89,
        )

    def _build_ffmpeg_command(
        self,
        audio_files: List[str],
        screenshot_files: List[str],
        output_path: str,
        job,
    ) -> List[str]:
        """Build FFmpeg command for video assembly

        Phase 3: Complex FFmpeg filter for multi-file composition

        Args:
            audio_files: List of audio file paths
            screenshot_files: List of screenshot file paths
            output_path: Output video path
            job: VideoJob instance

        Returns:
            FFmpeg command as list of arguments
        """

        # Base FFmpeg command
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
        ]

        # Add inputs (audio + screenshots)
        if audio_files:
            cmd.extend(["-i", audio_files[0]])
        if screenshot_files:
            cmd.extend(["-i", screenshot_files[0]])

        # Video codec settings
        bitrate_preset = {
            "low": "1000k",
            "medium": "2500k",
            "high": "5000k",
            "very-high": "10000k",
        }

        codec_opts = {
            "h264": ["-vcodec", "libx264", "-crf", "18", "-preset", "slow"],
            "vp9": ["-vcodec", "libvpx-vp9", "-crf", "30", "-b:v", "2500k"],
            "av1": ["-vcodec", "libaom-av1", "-crf", "30", "-b:v", "2500k"],
        }

        cmd.extend(codec_opts.get(self.codec, codec_opts["h264"]))

        # Audio codec: AAC @ 128 kbps (broadcast standard)
        cmd.extend(["-acodec", "aac", "-ab", "128k"])

        # Output options
        cmd.extend(["-movflags", "+faststart"])  # Enable streaming
        cmd.append(output_path)

        return cmd

    def _embed_subtitles(self, video_path: str, subtitle_path: str) -> str:
        """Embed SRT subtitles into video

        Phase 3: Use FFmpeg subtitle filter

        Args:
            video_path: Path to video file
            subtitle_path: Path to SRT subtitle file

        Returns:
            Path to video with embedded subtitles
        """

        output_path = video_path.replace(".mp4", "_subtitled.mp4")

        cmd = [
            "ffmpeg",
            "-i",
            video_path,
            "-vf",
            f"subtitles={subtitle_path}",
            "-c:a",
            "copy",
            output_path,
        ]

        # In production: subprocess.run(cmd, check=True)

        return output_path

    def _get_video_metadata(self, video_path: str) -> Dict[str, str]:
        """Extract video metadata using ffprobe

        Phase 3: Parse ffprobe JSON output

        Args:
            video_path: Path to video file

        Returns:
            Dict with video metadata (duration, codec, bitrate, etc.)
        """

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-print_json",
            video_path,
        ]

        # In production: result = subprocess.run(cmd, capture_output=True, text=True)
        # return json.loads(result.stdout)

        return {
            "duration": "60.0",
            "codec_name": self.codec,
            "bit_rate": "2500000",
        }
