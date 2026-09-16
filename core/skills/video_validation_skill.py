#!/usr/bin/env python3
"""
Video Validation Skill (Skill Forge v2)
Validates video-producer plugin outputs for visual & audio quality
Detects known issues: whistle tones, blue background, missing narration
Integrates with Video Producer Plugin to improve output reliability
"""

import json
import subprocess
import tempfile
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any
from enum import Enum

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================================
# MANIFEST & METADATA (Skill Forge v2)
# ============================================================================

SKILL_MANIFEST = {
    "id": "video-validation",
    "version": "1.0.0",
    "name": "Video Validation Skill",
    "description": "Validates video outputs for quality issues (visual & audio analysis)",
    "author": "CorvinOS Vibe Engineering",
    "plugin": "video-producer",
    "entry_point": "validate_video",
    "config": {
        "enable_visual_analysis": True,
        "enable_audio_analysis": True,
        "enable_known_issues_detection": True,
        "sampling_points": 5,  # Number of frames to sample
        "audio_sample_rate": 44100,
    },
    "required_checks": ["blue_background_check", "audio_whistle_check", "metadata_check"],
}

# ============================================================================
# TYPES & ENUMS
# ============================================================================

class IssueLevel(Enum):
    """Issue severity"""
    INFO = "info"      # Informational, no action needed
    WARNING = "warning"  # Potential issue, verify intent
    ERROR = "error"    # Definite issue, requires fixing
    CRITICAL = "critical"  # Cannot proceed without fix


@dataclass
class AudioAnalysisResult:
    """Audio analysis findings"""
    has_audio: bool
    channels: int = 0
    sample_rate: int = 0
    bitrate_kbps: int = 0
    duration_s: float = 0.0
    codec: str = ""
    spectral_peaks: List[float] = field(default_factory=list)  # Dominant frequencies
    is_mono: bool = False
    is_silent: bool = False
    is_simple_tone: bool = False  # True if mostly one or few frequencies
    issues: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class VisualAnalysisResult:
    """Visual analysis findings"""
    has_video: bool
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    codec: str = ""
    fps: float = 0.0
    bitrate_kbps: int = 0
    dominant_colors: List[Dict[str, Any]] = field(default_factory=list)  # [{"color": "0066cc", "percentage": 95}]
    is_mostly_one_color: bool = False
    color_variance: float = 0.0  # 0.0 = solid color, 1.0 = highly varied
    has_text_content: bool = False
    issues: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Complete validation report"""
    video_path: str
    is_valid: bool
    overall_level: IssueLevel
    audio_analysis: Optional[AudioAnalysisResult]
    visual_analysis: Optional[VisualAnalysisResult]
    known_issues: List[Dict[str, Any]] = field(default_factory=list)
    metadata_issues: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    timestamp: str = ""


# ============================================================================
# FFPROBE WRAPPER — Extract metadata
# ============================================================================

def extract_video_metadata(video_path: str) -> Dict[str, Any]:
    """Extract video metadata using ffprobe"""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_format", "-show_streams",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        metadata = {
            "has_video": False,
            "has_audio": False,
            "video": {},
            "audio": {},
            "format": {}
        }

        lines = result.stdout.strip().split('\n')
        current_stream_type = None

        for line in lines:
            if line.startswith('[STREAM]'):
                current_stream_type = None
            elif line.startswith('[/STREAM]'):
                current_stream_type = None
            elif 'codec_type=video' in line:
                current_stream_type = 'video'
                metadata['has_video'] = True
            elif 'codec_type=audio' in line:
                current_stream_type = 'audio'
                metadata['has_audio'] = True
            elif '=' in line:
                key, val = line.split('=', 1)
                key = key.strip()
                val = val.strip()

                if current_stream_type == 'video' and key in ['width', 'height', 'codec_name', 'r_frame_rate', 'bit_rate']:
                    metadata['video'][key] = val
                elif current_stream_type == 'audio' and key in ['channels', 'sample_rate', 'codec_name', 'bit_rate']:
                    metadata['audio'][key] = val
                elif key == 'duration':
                    metadata['format']['duration'] = val

        return metadata
    except Exception as e:
        logger.error(f"ffprobe failed: {e}")
        return {}


# ============================================================================
# VISUAL ANALYSIS — Frame sampling & color analysis
# ============================================================================

def extract_frames(video_path: str, num_frames: int = 5) -> List[str]:
    """Extract sample frames from video"""
    frame_paths = []
    temp_dir = tempfile.mkdtemp()

    try:
        # Calculate frame indices evenly spaced
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"fps=1/({30//num_frames})",  # Sample evenly
            "-frames:v", str(num_frames),
            "-y", f"{temp_dir}/frame_%03d.png"
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=60)

        # Collect generated frames
        for i in range(1, num_frames + 1):
            frame_path = f"{temp_dir}/frame_{i:03d}.png"
            if Path(frame_path).exists():
                frame_paths.append(frame_path)

        return frame_paths
    except Exception as e:
        logger.error(f"Frame extraction failed: {e}")
        return []


def analyze_color_distribution(frame_paths: List[str]) -> Dict[str, Any]:
    """Analyze dominant colors in frames"""
    try:
        from PIL import Image
        from collections import Counter

        color_counts = Counter()
        total_pixels = 0

        for frame_path in frame_paths:
            try:
                img = Image.open(frame_path)
                pixels = list(img.getdata())

                # Group similar colors (quantize to reduce noise)
                for pixel in pixels:
                    # Convert RGB to hex
                    if isinstance(pixel, tuple) and len(pixel) >= 3:
                        hex_color = f"{pixel[0]:02x}{pixel[1]:02x}{pixel[2]:02x}"
                        color_counts[hex_color] += 1
                        total_pixels += 1
            except Exception as e:
                logger.warning(f"Failed to analyze {frame_path}: {e}")
                continue

        if not color_counts:
            return {"error": "No valid frames to analyze"}

        # Get top 5 colors
        dominant_colors = []
        for color, count in color_counts.most_common(5):
            percentage = (count / total_pixels * 100) if total_pixels > 0 else 0
            dominant_colors.append({
                "color": color.upper(),
                "percentage": round(percentage, 1),
                "pixel_count": count
            })

        # Check if mostly one color (>80% is one color = solid)
        is_mostly_one_color = dominant_colors[0]["percentage"] > 80 if dominant_colors else False

        # Color variance (0=solid, 1=varied)
        color_variance = 1.0 - (dominant_colors[0]["percentage"] / 100) if dominant_colors else 0.5

        return {
            "dominant_colors": dominant_colors,
            "is_mostly_one_color": is_mostly_one_color,
            "color_variance": color_variance,
            "total_unique_colors": len(color_counts),
            "total_pixels_analyzed": total_pixels
        }
    except ImportError:
        logger.warning("PIL not available; color analysis skipped")
        return {"error": "PIL not installed"}
    except Exception as e:
        logger.error(f"Color analysis failed: {e}")
        return {"error": str(e)}


def analyze_visual(video_path: str) -> VisualAnalysisResult:
    """Complete visual analysis"""
    metadata = extract_video_metadata(video_path)

    result = VisualAnalysisResult(has_video=metadata.get("has_video", False))

    if not result.has_video:
        result.issues.append({
            "level": IssueLevel.ERROR.value,
            "message": "No video stream found"
        })
        return result

    # Extract metadata
    video_meta = metadata.get("video", {})
    result.width = int(video_meta.get("width", 0))
    result.height = int(video_meta.get("height", 0))
    result.codec = video_meta.get("codec_name", "unknown")
    result.duration_s = float(metadata.get("format", {}).get("duration", 0))

    try:
        fps_str = video_meta.get("r_frame_rate", "0/1")
        if "/" in fps_str:
            num, denom = fps_str.split("/")
            result.fps = float(num) / float(denom)
    except:
        result.fps = 0.0

    try:
        result.bitrate_kbps = int(video_meta.get("bit_rate", 0)) // 1000
    except:
        result.bitrate_kbps = 0

    # Frame sampling & color analysis
    frames = extract_frames(video_path, num_frames=5)
    if frames:
        color_analysis = analyze_color_distribution(frames)
        result.dominant_colors = color_analysis.get("dominant_colors", [])
        result.is_mostly_one_color = color_analysis.get("is_mostly_one_color", False)
        result.color_variance = color_analysis.get("color_variance", 0.0)

    # Known issues detection (visual)
    if result.is_mostly_one_color and result.dominant_colors:
        top_color = result.dominant_colors[0]["color"]
        percentage = result.dominant_colors[0]["percentage"]

        # Check if mostly blue (known issue)
        if top_color.upper().startswith("00") or top_color.lower() in ["0066cc", "0099ff", "00d9ff"]:
            result.issues.append({
                "level": IssueLevel.WARNING.value,
                "code": "mostly_blue_background",
                "message": f"Video is {percentage}% blue — likely solid color background, no content",
                "color": top_color,
                "percentage": percentage
            })

    return result


# ============================================================================
# AUDIO ANALYSIS — Spectral & frequency analysis
# ============================================================================

def analyze_audio_spectrum(video_path: str) -> Dict[str, Any]:
    """Extract audio and analyze frequency spectrum"""
    try:
        import soundfile as sf
        import librosa

        # Extract audio to WAV
        temp_wav = tempfile.mktemp(suffix=".wav")
        cmd = [
            "ffmpeg", "-i", video_path,
            "-q:a", "9",
            "-c:a", "pcm_s16le",
            "-y", temp_wav
        ]

        subprocess.run(cmd, capture_output=True, timeout=60, check=True)

        # Load audio
        y, sr = librosa.load(temp_wav, sr=44100)

        # Compute FFT
        D = librosa.stft(y)
        magnitude = np.abs(D)
        freqs = librosa.fft_frequencies(sr=sr)

        # Find dominant frequencies
        power = np.mean(magnitude ** 2, axis=1)
        top_indices = np.argsort(power)[-5:][::-1]
        peaks = [freqs[i] for i in top_indices if power[i] > np.mean(power)]

        # Check for simple tone (1-2 dominant frequencies)
        is_simple_tone = len([p for p in peaks if p > 50]) <= 2  # Ignore low frequencies

        # Check for silence
        is_silent = np.mean(np.abs(y)) < 0.01

        Path(temp_wav).unlink(missing_ok=True)

        return {
            "spectral_peaks": sorted(peaks)[:5],
            "is_simple_tone": is_simple_tone,
            "is_silent": is_silent,
            "mean_power": float(np.mean(power))
        }
    except ImportError:
        logger.warning("librosa/soundfile not available; spectral analysis skipped")
        return {"error": "librosa not installed"}
    except Exception as e:
        logger.error(f"Audio spectrum analysis failed: {e}")
        return {"error": str(e)}


def analyze_audio(video_path: str) -> AudioAnalysisResult:
    """Complete audio analysis"""
    metadata = extract_video_metadata(video_path)

    result = AudioAnalysisResult(has_audio=metadata.get("has_audio", False))

    if not result.has_audio:
        result.issues.append({
            "level": IssueLevel.ERROR.value,
            "message": "No audio stream found"
        })
        return result

    # Extract metadata
    audio_meta = metadata.get("audio", {})
    result.channels = int(audio_meta.get("channels", 0))
    result.sample_rate = int(audio_meta.get("sample_rate", 0))
    result.codec = audio_meta.get("codec_name", "unknown")
    result.is_mono = result.channels <= 1
    result.duration_s = float(metadata.get("format", {}).get("duration", 0))

    try:
        result.bitrate_kbps = int(audio_meta.get("bit_rate", 0)) // 1000
    except:
        result.bitrate_kbps = 0

    # Spectral analysis
    spectrum = analyze_audio_spectrum(video_path)
    if "error" not in spectrum:
        result.spectral_peaks = spectrum.get("spectral_peaks", [])
        result.is_simple_tone = spectrum.get("is_simple_tone", False)
        result.is_silent = spectrum.get("is_silent", False)

    # Known issues detection (audio)
    if result.is_mono and result.bitrate_kbps < 100:
        result.issues.append({
            "level": IssueLevel.WARNING.value,
            "code": "low_quality_mono_audio",
            "message": f"Audio is mono {result.bitrate_kbps}kbps — possibly generated fallback",
            "bitrate": result.bitrate_kbps,
            "channels": result.channels
        })

    if result.is_simple_tone and len(result.spectral_peaks) <= 2:
        result.issues.append({
            "level": IssueLevel.WARNING.value,
            "code": "whistle_tone_detected",
            "message": f"Audio has simple tone pattern ({len(result.spectral_peaks)} dominant frequencies) — likely generated tone, not narration",
            "peaks": [round(f, 1) for f in result.spectral_peaks]
        })

    if result.is_silent:
        result.issues.append({
            "level": IssueLevel.ERROR.value,
            "code": "silent_audio",
            "message": "Audio is silent or near-silent"
        })

    return result


# ============================================================================
# MAIN VALIDATION ENTRY POINT
# ============================================================================

def validate_video(video_path: str, **kwargs) -> ValidationReport:
    """
    Main entry point: validate video output
    Returns: ValidationReport with findings, issues, and recommendations
    """
    from datetime import datetime

    video_path = str(video_path)

    if not Path(video_path).exists():
        return ValidationReport(
            video_path=video_path,
            is_valid=False,
            overall_level=IssueLevel.ERROR,
            audio_analysis=None,
            visual_analysis=None,
            issues=[{"level": "error", "message": f"File not found: {video_path}"}],
            timestamp=datetime.now().isoformat()
        )

    logger.info(f"Validating video: {video_path}")

    # Run analyses
    visual = analyze_visual(video_path)
    audio = analyze_audio(video_path)

    # Aggregate issues
    all_issues = []
    all_recommendations = []
    overall_level = IssueLevel.INFO

    # Visual issues
    for issue in visual.issues:
        all_issues.append(issue)
        issue_level = IssueLevel(issue.get("level", "info"))
        if issue_level.value in ["error", "critical"]:
            overall_level = issue_level

    # Audio issues
    for issue in audio.issues:
        all_issues.append(issue)
        issue_level = IssueLevel(issue.get("level", "info"))
        if issue_level.value in ["error", "critical"]:
            overall_level = issue_level

    # Generate recommendations
    if any(i.get("code") == "mostly_blue_background" for i in all_issues):
        all_recommendations.append("Add visual content (text, shapes, animations) to the video")

    if any(i.get("code") == "whistle_tone_detected" for i in all_issues):
        all_recommendations.append("Replace generated tone with real narration or music")

    if any(i.get("code") == "low_quality_mono_audio" for i in all_issues):
        all_recommendations.append("Use higher bitrate audio or add multi-channel narration")

    is_valid = overall_level not in [IssueLevel.ERROR, IssueLevel.CRITICAL]

    return ValidationReport(
        video_path=video_path,
        is_valid=is_valid,
        overall_level=overall_level,
        audio_analysis=audio,
        visual_analysis=visual,
        known_issues=all_issues,
        recommendations=all_recommendations,
        timestamp=datetime.now().isoformat()
    )


# ============================================================================
# SERIALIZATION (for Skill output)
# ============================================================================

def report_to_dict(report: ValidationReport) -> Dict[str, Any]:
    """Convert report to JSON-serializable dict"""
    return {
        "video_path": report.video_path,
        "is_valid": report.is_valid,
        "overall_level": report.overall_level.value,
        "audio_analysis": asdict(report.audio_analysis) if report.audio_analysis else None,
        "visual_analysis": asdict(report.visual_analysis) if report.visual_analysis else None,
        "known_issues": report.known_issues,
        "recommendations": report.recommendations,
        "timestamp": report.timestamp
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 video_validation_skill.py <video_path>")
        sys.exit(1)

    report = validate_video(sys.argv[1])
    print(json.dumps(report_to_dict(report), indent=2))
