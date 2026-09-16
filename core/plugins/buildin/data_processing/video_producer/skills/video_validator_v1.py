#!/usr/bin/env python3
"""
Video Validator Skill v1.0
Skill Forge v2 Integration
Part of Video Producer Plugin
"""

import json
import subprocess
import tempfile
import logging
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# SKILL MANIFEST (Skill Forge v2 compliant)
# ============================================================================

SKILL_MANIFEST = {
    "id": "video-producer:video-validator",
    "version": "1.0.0",
    "name": "Video Validator",
    "description": "Validates video-producer plugin outputs for quality issues (visual & audio)",
    "plugin_id": "video-producer",
    "author": "CorvinOS Vibe Engineering",
    "boot_layer": "bundled",
    "capabilities": [
        "validate_visual_quality",
        "detect_solid_background",
        "detect_simple_audio",
        "metadata_check"
    ],
    "config": {
        "enabled": True,
        "check_solid_background": True,
        "check_simple_audio": True,
        "frame_scale": 64,
        "solid_color_threshold": 300,  # PNG size threshold in bytes
    },
    "required_checks": [
        "metadata_integrity",
        "video_stream_present",
        "audio_quality_check"
    ]
}


# ============================================================================
# RESULT TYPES
# ============================================================================

@dataclass
class VideoValidationResult:
    """Output of video validation"""
    video_path: str
    is_valid: bool
    severity: str  # "ok", "warning", "error"
    file_size_mb: float
    duration_s: float
    resolution: str
    fps: float
    codecs: Dict[str, str]  # {"video": "h264", "audio": "aac"}
    issues: list
    recommendations: list
    timestamp: str


# ============================================================================
# FFPROBE INTEGRATION
# ============================================================================

def get_ffprobe_json(video_path: str) -> Dict[str, Any]:
    """Get ffprobe output as JSON"""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed, returning empty")
        return {"streams": [], "format": {}}
    except Exception as e:
        logger.error(f"ffprobe failed: {e}")
        return {"streams": [], "format": {}}


# ============================================================================
# QUALITY CHECKS
# ============================================================================

def check_solid_background(video_path: str) -> tuple[bool, Optional[str]]:
    """Detect if video is solid color (no visual content)"""
    try:
        temp_png = tempfile.mktemp(suffix=".png")
        cmd = [
            "ffmpeg", "-ss", "30", "-i", video_path,
            "-vf", f"scale={SKILL_MANIFEST['config']['frame_scale']}:{SKILL_MANIFEST['config']['frame_scale']}",
            "-frames:v", "1", "-y", temp_png
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, check=False)

        if Path(temp_png).exists():
            size = Path(temp_png).stat().st_size
            Path(temp_png).unlink(missing_ok=True)
            threshold = SKILL_MANIFEST['config']['solid_color_threshold']
            is_solid = size < threshold
            return is_solid, f"Video is mostly solid color ({size} bytes < {threshold} threshold)" if is_solid else None

        return False, None
    except Exception as e:
        logger.warning(f"Background check failed: {e}")
        return False, None


def check_audio_quality(video_path: str) -> tuple[bool, Optional[str]]:
    """Detect if audio is just a simple generated tone"""
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
            # 1s of 44.1kHz stereo PCM = ~176 KB; < 40 KB = simple tone
            is_simple = size < 40 * 1024
            return is_simple, "Audio is a simple generated tone (not real narration)" if is_simple else None

        return False, None
    except Exception as e:
        logger.warning(f"Audio check failed: {e}")
        return False, None


# ============================================================================
# MAIN VALIDATION ENTRY POINT
# ============================================================================

def validate(video_path: str, **kwargs) -> Dict[str, Any]:
    """
    Validate video output from Video Producer Plugin
    Entry point for Skill Forge v2

    Returns:
        dict: Serialized ValidationResult
    """
    video_path = str(video_path)

    # File existence check
    if not Path(video_path).exists():
        return asdict(VideoValidationResult(
            video_path=video_path,
            is_valid=False,
            severity="error",
            file_size_mb=0,
            duration_s=0,
            resolution="unknown",
            fps=0.0,
            codecs={},
            issues=[{"level": "error", "message": f"File not found: {video_path}"}],
            recommendations=["Check video output path"],
            timestamp=datetime.now().isoformat()
        ))

    file_size_mb = Path(video_path).stat().st_size / (1024**2)

    # Get metadata
    metadata = get_ffprobe_json(video_path)
    streams = metadata.get("streams", [])
    fmt = metadata.get("format", {})

    # Parse streams
    has_video = False
    has_audio = False
    codecs = {}
    resolution = "unknown"
    fps = 0.0

    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            has_video = True
            codecs["video"] = stream.get("codec_name", "unknown")
            try:
                w = stream.get("width", 0)
                h = stream.get("height", 0)
                resolution = f"{w}x{h}"
            except:
                pass
            try:
                fps_str = stream.get("r_frame_rate", "0/1")
                if "/" in fps_str:
                    num, denom = map(float, fps_str.split("/"))
                    fps = num / denom
            except:
                pass
        elif codec_type == "audio":
            has_audio = True
            codecs["audio"] = stream.get("codec_name", "unknown")

    # Duration
    try:
        duration_s = float(fmt.get("duration", 0))
    except:
        duration_s = 0

    # Run quality checks
    issues = []
    recommendations = []
    severity = "ok"

    # Metadata checks
    if not has_video:
        issues.append({"level": "error", "message": "No video stream"})
        severity = "error"

    if not has_audio:
        issues.append({"level": "warning", "message": "No audio stream"})
        if severity != "error":
            severity = "warning"

    # Visual quality
    if has_video and SKILL_MANIFEST['config']['check_solid_background']:
        is_solid, msg = check_solid_background(video_path)
        if is_solid:
            issues.append({"level": "warning", "code": "solid_background", "message": msg})
            recommendations.append("Add visual content: text, shapes, or animations")
            if severity == "ok":
                severity = "warning"

    # Audio quality
    if has_audio and SKILL_MANIFEST['config']['check_simple_audio']:
        is_simple, msg = check_audio_quality(video_path)
        if is_simple:
            issues.append({"level": "warning", "code": "simple_audio", "message": msg})
            recommendations.append("Add real narration or quality music")
            if severity == "ok":
                severity = "warning"

    # Duration check
    if duration_s < 30:
        issues.append({"level": "info", "message": f"Short video: {duration_s:.1f}s"})

    is_valid = severity != "error"

    return asdict(VideoValidationResult(
        video_path=video_path,
        is_valid=is_valid,
        severity=severity,
        file_size_mb=round(file_size_mb, 2),
        duration_s=duration_s,
        resolution=resolution,
        fps=fps,
        codecs=codecs,
        issues=issues,
        recommendations=recommendations,
        timestamp=datetime.now().isoformat()
    ))


# ============================================================================
# SKILL INTERFACE (for Video Producer Plugin integration)
# ============================================================================

class VideoValidatorSkill:
    """Skill Forge v2 interface for Video Producer Plugin"""

    def __init__(self):
        self.manifest = SKILL_MANIFEST
        logger.info(f"Loaded {self.manifest['name']} v{self.manifest['version']}")

    def execute(self, video_path: str, **config_overrides) -> Dict[str, Any]:
        """Execute validation"""
        # Apply config overrides
        config = self.manifest['config'].copy()
        config.update(config_overrides)

        result = validate(video_path)
        result['manifest_version'] = self.manifest['version']
        result['skill_id'] = self.manifest['id']
        return result


# CLI for standalone testing
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 video_validator_v1.py <video_path>")
        sys.exit(1)

    skill = VideoValidatorSkill()
    result = skill.execute(sys.argv[1])
    print(json.dumps(result, indent=2))
