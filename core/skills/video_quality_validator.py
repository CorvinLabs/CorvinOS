#!/usr/bin/env python3
"""
Video Quality Validator Skill (Production Ready)
Part of Video Producer Plugin — validates outputs
Zero external dependencies beyond stdlib + ffmpeg
"""

import json
import subprocess
import tempfile
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================================================
# SKILL MANIFEST (Skill Forge v2)
# ============================================================================

MANIFEST = {
    "id": "video-quality-validator",
    "version": "1.0.0",
    "name": "Video Quality Validator",
    "description": "Validates video-producer plugin outputs for known issues",
    "plugin": "video-producer",
    "entry_point": "validate",
    "config": {
        "check_solid_background": True,
        "check_whistle_tone": True,
        "check_metadata": True,
    }
}


# ============================================================================
# RESULT TYPES
# ============================================================================

@dataclass
class ValidationReport:
    """Validation report"""
    video_path: str
    is_valid: bool
    severity: str  # "ok", "warning", "error"

    # Technical info
    file_size_mb: float
    duration_s: float
    has_video: bool
    has_audio: bool
    resolution: str
    video_codec: str
    audio_codec: str
    fps: float

    # Issues found
    issues: list
    recommendations: list
    timestamp: str


# ============================================================================
# FFPROBE METADATA (Robust parsing)
# ============================================================================

def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """Extract metadata with robust parsing"""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        return data
    except json.JSONDecodeError:
        logger.warning("ffprobe JSON parsing failed, falling back to text")
        return _parse_ffprobe_text(video_path)
    except Exception as e:
        logger.error(f"ffprobe failed: {e}")
        return {}


def _parse_ffprobe_text(video_path: str) -> Dict[str, Any]:
    """Fallback text-based ffprobe parsing"""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        data = {"streams": [], "format": {}}
        current_stream = None

        for line in result.stdout.split('\n'):
            line = line.strip()
            if line == '[STREAM]':
                current_stream = {}
            elif line == '[/STREAM]' and current_stream:
                data["streams"].append(current_stream)
                current_stream = None
            elif '=' in line and current_stream is not None:
                key, val = line.split('=', 1)
                current_stream[key] = val
            elif '=' in line and line.startswith('[FORMAT]'):
                key, val = line.split('=', 1) if '=' in line else (line, '')
                data["format"][key] = val

        return data
    except Exception as e:
        logger.error(f"Text fallback failed: {e}")
        return {"streams": [], "format": {}}


# ============================================================================
# QUALITY CHECKS
# ============================================================================

def check_solid_background(video_path: str) -> tuple[bool, Optional[str]]:
    """Check if video is solid color (no content)"""
    try:
        temp_png = tempfile.mktemp(suffix=".png")
        # Extract frame at 30 seconds (middle), downscale
        cmd = [
            "ffmpeg", "-ss", "30", "-i", video_path,
            "-vf", "scale=64:64", "-frames:v", "1",
            "-y", temp_png
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, check=False)

        if Path(temp_png).exists():
            size = Path(temp_png).stat().st_size
            Path(temp_png).unlink(missing_ok=True)
            # Very small PNG = low entropy = solid color
            is_solid = size < 300
            return is_solid, "Video appears to be solid color" if is_solid else None

        return False, None
    except Exception as e:
        logger.warning(f"Solid background check failed: {e}")
        return False, None


def check_whistle_tone(video_path: str) -> tuple[bool, Optional[str]]:
    """Check if audio is a generated tone (not narration)"""
    try:
        temp_wav = tempfile.mktemp(suffix=".wav")
        cmd = [
            "ffmpeg", "-i", video_path, "-t", "1",
            "-c:a", "pcm_s16le", "-y", temp_wav
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, check=False)

        if Path(temp_wav).exists():
            size = Path(temp_wav).stat().st_size
            Path(temp_wav).unlink(missing_ok=True)
            # 1 second of 44.1kHz stereo PCM = ~176 KB
            # < 50 KB = simple tone, not narration
            is_tone = size < 50 * 1024
            return is_tone, "Audio is a simple tone (no real narration)" if is_tone else None

        return False, None
    except Exception as e:
        logger.warning(f"Whistle tone check failed: {e}")
        return False, None


def check_metadata(metadata: Dict[str, Any]) -> list:
    """Check metadata for obvious issues"""
    issues = []

    streams = metadata.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    if not has_video:
        issues.append(("error", "No video stream found"))

    if not has_audio:
        issues.append(("warning", "No audio stream found"))

    # Check duration
    fmt = metadata.get("format", {})
    try:
        duration = float(fmt.get("duration", 0))
        if duration < 30:
            issues.append(("info", f"Short duration: {duration:.1f}s (expected ~60s)"))
    except:
        pass

    return issues


# ============================================================================
# MAIN VALIDATION
# ============================================================================

def validate(video_path: str, **kwargs) -> Dict[str, Any]:
    """
    Main validation entry point
    Returns dict suitable for JSON serialization
    """
    video_path = str(video_path)

    # Check file exists
    if not Path(video_path).exists():
        report = ValidationReport(
            video_path=video_path,
            is_valid=False,
            severity="error",
            file_size_mb=0,
            duration_s=0,
            has_video=False,
            has_audio=False,
            resolution="",
            video_codec="",
            audio_codec="",
            fps=0.0,
            issues=[("error", f"File not found: {video_path}")],
            recommendations=[],
            timestamp=datetime.now().isoformat()
        )
        return asdict(report)

    file_size_mb = Path(video_path).stat().st_size / (1024**2)

    # Get metadata
    metadata = get_video_metadata(video_path)
    streams = metadata.get("streams", [])
    fmt = metadata.get("format", {})

    # Extract stream info
    has_video = False
    has_audio = False
    video_codec = "unknown"
    audio_codec = "unknown"
    resolution = "unknown"
    fps = 0.0

    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            has_video = True
            video_codec = stream.get("codec_name", "unknown")
            try:
                w = stream.get("width", "?")
                h = stream.get("height", "?")
                resolution = f"{w}x{h}"
            except:
                pass
            try:
                fps_str = stream.get("r_frame_rate", "0/1")
                if "/" in fps_str:
                    num, denom = fps_str.split("/")
                    fps = float(num) / float(denom)
            except:
                pass
        elif codec_type == "audio":
            has_audio = True
            audio_codec = stream.get("codec_name", "unknown")

    # Duration
    try:
        duration_s = float(fmt.get("duration", 0))
    except:
        duration_s = 0

    # Run checks
    issues = []
    recommendations = []
    severity = "ok"

    # Metadata checks
    meta_issues = check_metadata(metadata)
    for level, msg in meta_issues:
        issues.append({"level": level, "message": msg})
        if level == "error" and severity == "ok":
            severity = "error"
        elif level == "warning" and severity != "error":
            severity = "warning"

    # Solid background check
    if has_video:
        is_solid, msg = check_solid_background(video_path)
        if is_solid:
            issues.append({"level": "warning", "code": "solid_background", "message": msg})
            recommendations.append("Add visual content (text, shapes, animations)")
            if severity == "ok":
                severity = "warning"

    # Whistle tone check
    if has_audio:
        is_tone, msg = check_whistle_tone(video_path)
        if is_tone:
            issues.append({"level": "warning", "code": "whistle_tone", "message": msg})
            recommendations.append("Add real narration or music")
            if severity == "ok":
                severity = "warning"

    is_valid = severity != "error"

    report = ValidationReport(
        video_path=video_path,
        is_valid=is_valid,
        severity=severity,
        file_size_mb=round(file_size_mb, 2),
        duration_s=duration_s,
        has_video=has_video,
        has_audio=has_audio,
        resolution=resolution,
        video_codec=video_codec,
        audio_codec=audio_codec,
        fps=fps,
        issues=issues,
        recommendations=recommendations,
        timestamp=datetime.now().isoformat()
    )

    return asdict(report)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 video_quality_validator.py <video_path>")
        sys.exit(1)

    result = validate(sys.argv[1])
    print(json.dumps(result, indent=2))
