"""
Base utilities for video generation pipeline
"""

import os
import subprocess
import logging
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class VideoFrame:
    """Represents a single frame"""
    path: str
    frame_number: int
    timestamp: float
    duration: float


@dataclass
class PhaseOutput:
    """Represents output from a phase"""
    phase_name: str
    video_file: str
    frame_count: int
    duration_seconds: float
    status: str  # "success", "error", "skipped"
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None


class BasePhase:
    """Base class for all video generation phases"""

    def __init__(self, config: Dict[str, Any], phase_name: str):
        self.config = config
        self.phase_name = phase_name
        self.output_dir = Path(config.get("output", {}).get("temp_dir", "/tmp/corvinos_video_gen"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(f"Phase_{phase_name}")

    def log(self, message: str, level: str = "info"):
        """Log a message"""
        getattr(self.logger, level)(message)

    def run_command(self, cmd: List[str], capture_output: bool = False) -> tuple:
        """Run a shell command safely"""
        try:
            self.log(f"Running: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                timeout=3600  # 1 hour timeout
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                self.log(f"Command failed: {error_msg}", "error")
                return False, error_msg

            return True, result.stdout
        except subprocess.TimeoutExpired:
            self.log("Command timed out", "error")
            return False, "Command timed out"
        except Exception as e:
            self.log(f"Command error: {str(e)}", "error")
            return False, str(e)

    def check_dependency(self, tool: str) -> bool:
        """Check if a tool is installed"""
        result = subprocess.run(
            ["which", tool],
            capture_output=True,
            text=True
        )
        return result.returncode == 0

    def ensure_directory(self, directory: str) -> Path:
        """Ensure directory exists"""
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_frame_count(self, duration_seconds: float, fps: int) -> int:
        """Calculate frame count from duration"""
        return int(duration_seconds * fps)

    def save_metadata(self, output_file: str, metadata: Dict[str, Any]):
        """Save metadata JSON for output"""
        metadata_file = f"{output_file}.metadata.json"
        try:
            with open(metadata_file, 'w') as f:
                json.dump({
                    "timestamp": datetime.now().isoformat(),
                    **metadata
                }, f, indent=2)
            self.log(f"Saved metadata: {metadata_file}")
        except Exception as e:
            self.log(f"Failed to save metadata: {str(e)}", "warning")

    def execute(self) -> PhaseOutput:
        """Override in subclass"""
        raise NotImplementedError(f"execute() not implemented in {self.__class__.__name__}")


class FFmpegHelper:
    """Helper class for FFmpeg operations"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("FFmpegHelper")
        self.check_ffmpeg()

    def check_ffmpeg(self):
        """Verify FFmpeg is installed"""
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True)
        if result.returncode != 0:
            raise RuntimeError("FFmpeg not found. Please install it: sudo apt-get install ffmpeg")

    def frames_to_video(
        self,
        frame_pattern: str,
        output_file: str,
        fps: int = 25,
        vcodec: str = "libx264",
        crf: int = 18,
        pix_fmt: str = "yuv420p"
    ) -> bool:
        """Convert frame sequence to video"""

        cmd = [
            "ffmpeg",
            "-framerate", str(fps),
            "-i", frame_pattern,
            "-c:v", vcodec,
            "-crf", str(crf),
            "-pix_fmt", pix_fmt,
            "-y",  # Overwrite output
            output_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            self.logger.error(f"Frame to video conversion failed: {result.stderr}")
            return False

        self.logger.info(f"Created video: {output_file}")
        return True

    def concatenate_videos(
        self,
        input_files: List[str],
        output_file: str,
        preserve_audio: bool = False
    ) -> bool:
        """Concatenate multiple video files"""

        # Create concat demuxer file
        concat_file = "/tmp/concat_list.txt"
        try:
            with open(concat_file, 'w') as f:
                for video_file in input_files:
                    f.write(f"file '{video_file}'\n")
        except Exception as e:
            self.logger.error(f"Failed to create concat file: {str(e)}")
            return False

        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",  # Copy without re-encoding
            "-y",
            output_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        os.remove(concat_file)

        if result.returncode != 0:
            self.logger.error(f"Concatenation failed: {result.stderr}")
            return False

        self.logger.info(f"Concatenated videos: {output_file}")
        return True

    def add_audio_to_video(
        self,
        video_file: str,
        audio_file: str,
        output_file: str,
        audio_bitrate: str = "192k"
    ) -> bool:
        """Add audio track to video"""

        cmd = [
            "ffmpeg",
            "-i", video_file,
            "-i", audio_file,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-shortest",  # Use shortest stream
            "-y",
            output_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            self.logger.error(f"Audio addition failed: {result.stderr}")
            return False

        self.logger.info(f"Added audio: {output_file}")
        return True

    def apply_filter(
        self,
        input_file: str,
        filter_string: str,
        output_file: str
    ) -> bool:
        """Apply FFmpeg filter to video"""

        cmd = [
            "ffmpeg",
            "-i", input_file,
            "-vf", filter_string,
            "-c:v", "libx264",
            "-crf", "18",
            "-y",
            output_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            self.logger.error(f"Filter application failed: {result.stderr}")
            return False

        self.logger.info(f"Applied filter: {output_file}")
        return True

    def get_video_info(self, video_file: str) -> Dict[str, Any]:
        """Get video information"""

        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,duration",
            "-of", "json",
            video_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                return data.get("streams", [{}])[0]
            except json.JSONDecodeError:
                return {}

        return {}


class VideoCompositor:
    """Helper for compositing multiple video layers"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger("VideoCompositor")
        self.ffmpeg = FFmpegHelper(config)

    def blend_videos(
        self,
        background_file: str,
        overlay_file: str,
        output_file: str,
        blend_mode: str = "lighten",
        opacity: float = 1.0
    ) -> bool:
        """Blend two videos using specified blend mode"""

        # Map blend modes to FFmpeg filter
        blend_filters = {
            "lighten": f"blend=lighten:opacity={opacity}",
            "screen": f"blend=screen:opacity={opacity}",
            "add": f"blend=addition:opacity={opacity}",
            "multiply": f"blend=multiply:opacity={opacity}",
        }

        filter_str = blend_filters.get(blend_mode, blend_filters["lighten"])

        # Scale overlay to match background resolution
        w = self.config.get("video", {}).get("width", 1920)
        h = self.config.get("video", {}).get("height", 1080)

        filter_complex = f"[0:v][1:v]{filter_str}[out]"

        cmd = [
            "ffmpeg",
            "-i", background_file,
            "-i", overlay_file,
            "-filter_complex", f"[0]scale={w}:{h}[v0];[1]scale={w}:{h}[v1];[v0][v1]{filter_str}[out]",
            "-map", "[out]",
            "-c:v", "libx264",
            "-crf", "18",
            "-y",
            output_file
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            self.logger.error(f"Blending failed: {result.stderr}")
            return False

        self.logger.info(f"Blended videos: {output_file}")
        return True


class ProgressTracker:
    """Track progress of video generation"""

    def __init__(self, total_phases: int):
        self.total_phases = total_phases
        self.completed_phases = 0
        self.phase_results: List[PhaseOutput] = []
        self.logger = logging.getLogger("ProgressTracker")

    def add_phase_result(self, result: PhaseOutput):
        """Record a phase result"""
        self.phase_results.append(result)
        if result.status == "success":
            self.completed_phases += 1

        progress = (self.completed_phases / self.total_phases) * 100
        self.logger.info(f"Progress: {progress:.1f}% ({self.completed_phases}/{self.total_phases})")

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all phases"""
        return {
            "total_phases": self.total_phases,
            "completed": self.completed_phases,
            "failed": len([r for r in self.phase_results if r.status == "error"]),
            "skipped": len([r for r in self.phase_results if r.status == "skipped"]),
            "phases": [
                {
                    "name": r.phase_name,
                    "status": r.status,
                    "duration": r.duration_seconds,
                    "error": r.error_message
                }
                for r in self.phase_results
            ]
        }


def setup_logging(config: Dict[str, Any]) -> logging.Logger:
    """Setup logging configuration"""

    log_config = config.get("logging", {})
    log_file = log_config.get("log_file", "./logs/video_generation.log")
    log_level = log_config.get("level", "INFO").upper()

    # Create logs directory
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level))

    # File handler
    fh = logging.FileHandler(log_file)
    fh.setLevel(getattr(logging, log_level))

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, log_level))

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    root_logger.addHandler(fh)
    root_logger.addHandler(ch)

    return root_logger
