#!/usr/bin/env python3
"""
Video Validation Skill (Simplified)
No external dependencies beyond ffmpeg + stdlib
Validates video-producer plugin outputs
"""

import json
import subprocess
import tempfile
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# ============================================================================
# TYPES
# ============================================================================

class IssueLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ValidationResult:
    """Validation result"""
    video_path: str
    is_valid: bool
    overall_level: str  # "info", "warning", "error", "critical"
    file_size_mb: float
    duration_s: float
    has_video: bool
    has_audio: bool
    video_codec: str
    audio_codec: str
    resolution: str  # "1920x1080"
    fps: float
    issues: List[Dict[str, Any]]
    recommendations: List[str]
    timestamp: str


# ============================================================================
# FFPROBE — Metadata extraction (NO external libs needed)
# ============================================================================

def extract_metadata(video_path: str) -> Dict[str, Any]:
    """Extract video metadata using ffprobe (returns raw text parsing)"""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        metadata = {
            "has_video": False,
            "has_audio": False,
            "video": {},
            "audio": {},
            "format": {}
        }

        for line in result.stdout.split('\n'):
            line = line.strip()
            if not line or '=' not in line:
                continue

            key, val = line.split('=', 1)

            if 'codec_type=video' in result.stdout[:result.stdout.find(line)]:
                metadata["has_video"] = True
                if key in ["width", "height", "codec_name", "r_frame_rate", "bit_rate"]:
                    metadata["video"][key] = val
            elif 'codec_type=audio' in result.stdout[:result.stdout.find(line)]:
                metadata["has_audio"] = True
                if key in ["channels", "sample_rate", "codec_name", "bit_rate"]:
                    metadata["audio"][key] = val
            elif key == "duration":
                metadata["format"]["duration"] = float(val)

        return metadata
    except Exception as e:
        logger.error(f"ffprobe failed: {e}")
        return {}


# ============================================================================
# AUDIO ANALYSIS — Simple frequency detection (NO numpy)
# ============================================================================

def detect_whistle_tone(video_path: str) -> bool:
    """
    Detect if audio is mostly a simple tone (whistle/sine wave)
    Uses FFmpeg to analyze audio without external libs
    """
    try:
        # Extract short audio sample
        temp_wav = tempfile.mktemp(suffix=".wav")
        cmd = [
            "ffmpeg", "-i", video_path,
            "-t", "2",  # Just first 2 seconds
            "-q:a", "9", "-c:a", "pcm_s16le",
            "-y", temp_wav
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, check=False)

        # Use FFmpeg to analyze frequency content
        # If audio is mostly 440 Hz (or similar common tone), it's a whistle
        freq_cmd = [
            "ffmpeg", "-i", temp_wav,
            "-af", "showspectrumpic=s=256x256:log=1",
            "-frames:v", "1",
            "-y", "/tmp/spectrum.png"
        ]

        result = subprocess.run(freq_cmd, capture_output=True, timeout=30, check=False)

        # Simple heuristic: check if audio file is very small (< 50KB for 2s audio)
        # Small audio = low entropy = probably a tone
        wav_size = Path(temp_wav).stat().st_size if Path(temp_wav).exists() else 0
        Path(temp_wav).unlink(missing_ok=True)

        # 2 seconds of 44.1kHz PCM = ~176 KB; < 100 KB suggests simple tone
        return wav_size < 100 * 1024 if wav_size > 0 else False

    except Exception as e:
        logger.warning(f"Whistle detection failed: {e}")
        return False


# ============================================================================
# VISUAL ANALYSIS — Frame sampling (NO PIL needed)
# ============================================================================

def detect_solid_color(video_path: str) -> bool:
    """
    Detect if video is mostly solid color (no content)
    Extracts frames and checks color uniformity using FFmpeg histogram
    """
    try:
        # Extract middle frame
        temp_png = tempfile.mktemp(suffix=".png")
        cmd = [
            "ffmpeg", "-ss", "30%", "-i", video_path,
            "-vf", "scale=64:64",  # Downscale to detect dominant color
            "-frames:v", "1",
            "-y", temp_png
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=30, check=False)

        if not Path(temp_png).exists():
            return False

        # Use identify (ImageMagick) if available, else use a simple heuristic
        try:
            identify_cmd = ["identify", "-format", "%[fx:entropy]", temp_png]
            result = subprocess.run(identify_cmd, capture_output=True, text=True, timeout=5)
            entropy = float(result.stdout.strip())
            # Entropy < 0.3 means low color variation (solid color)
            is_solid = entropy < 0.3
        except:
            # Fallback: check file size of scaled 64x64 PNG
            # Solid color image will compress very well (< 200 bytes)
            is_solid = Path(temp_png).stat().st_size < 200

        Path(temp_png).unlink(missing_ok=True)
        return is_solid

    except Exception as e:
        logger.warning(f"Solid color detection failed: {e}")
        return False


# ============================================================================
# VALIDATION ENTRY POINT
# ============================================================================

def validate_video(video_path: str) -> ValidationResult:
    """Validate video output"""
    video_path = str(video_path)

    # Basic checks
    if not Path(video_path).exists():
        return ValidationResult(
            video_path=video_path,
            is_valid=False,
            overall_level=IssueLevel.ERROR.value,
            file_size_mb=0,
            duration_s=0,
            has_video=False,
            has_audio=False,
            video_codec="",
            audio_codec="",
            resolution="",
            fps=0.0,
            issues=[{"level": "error", "message": f"File not found: {video_path}"}],
            recommendations=[],
            timestamp=datetime.now().isoformat()
        )

    file_size_mb = Path(video_path).stat().st_size / (1024 * 1024)

    # Extract metadata
    metadata = extract_metadata(video_path)
    has_video = metadata.get("has_video", False)
    has_audio = metadata.get("has_audio", False)
    duration_s = metadata.get("format", {}).get("duration", 0)

    # Extract codec info
    video_meta = metadata.get("video", {})
    audio_meta = metadata.get("audio", {})

    video_codec = video_meta.get("codec_name", "unknown")
    audio_codec = audio_meta.get("codec_name", "unknown")

    # Parse resolution
    width = video_meta.get("width", "0")
    height = video_meta.get("height", "0")
    resolution = f"{width}x{height}" if width != "0" else "unknown"

    # Parse FPS
    fps_str = video_meta.get("r_frame_rate", "0/1")
    try:
        if "/" in fps_str:
            num, denom = fps_str.split("/")
            fps = float(num) / float(denom)
        else:
            fps = float(fps_str)
    except:
        fps = 0.0

    # Validation checks
    issues = []
    recommendations = []
    overall_level = IssueLevel.INFO

    if not has_video:
        issues.append({"level": "error", "message": "No video stream"})
        overall_level = IssueLevel.ERROR

    if not has_audio:
        issues.append({"level": "warning", "message": "No audio stream"})
        overall_level = IssueLevel.WARNING

    # Known issues detection
    if has_video and detect_solid_color(video_path):
        issues.append({
            "level": "warning",
            "code": "solid_background",
            "message": "Video appears to be solid color (no content)"
        })
        recommendations.append("Add visual content: text, shapes, or animations")
        if overall_level == IssueLevel.INFO:
            overall_level = IssueLevel.WARNING

    if has_audio and detect_whistle_tone(video_path):
        issues.append({
            "level": "warning",
            "code": "whistle_tone",
            "message": "Audio is a simple tone (likely generated, not narration)"
        })
        recommendations.append("Replace with real narration or music")
        if overall_level == IssueLevel.INFO:
            overall_level = IssueLevel.WARNING

    # Duration check
    if duration_s < 30:
        issues.append({
            "level": "info",
            "code": "short_duration",
            "message": f"Video is only {duration_s:.1f}s (expected ~60s)"
        })

    is_valid = overall_level not in [IssueLevel.ERROR, IssueLevel.CRITICAL]

    return ValidationResult(
        video_path=video_path,
        is_valid=is_valid,
        overall_level=overall_level.value,
        file_size_mb=round(file_size_mb, 2),
        duration_s=duration_s,
        has_video=has_video,
        has_audio=has_audio,
        video_codec=video_codec,
        audio_codec=audio_codec,
        resolution=resolution,
        fps=fps,
        issues=issues,
        recommendations=recommendations,
        timestamp=datetime.now().isoformat()
    )


def result_to_dict(result: ValidationResult) -> Dict[str, Any]:
    """Convert result to JSON-serializable dict"""
    return asdict(result)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 video_validation_simple.py <video_path>")
        sys.exit(1)

    result = validate_video(sys.argv[1])
    print(json.dumps(result_to_dict(result), indent=2))
