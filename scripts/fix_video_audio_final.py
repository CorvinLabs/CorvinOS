#!/usr/bin/env python3
"""
Fix Video Audio: Generate REAL TTS Narration + Reassemble Videos with Proper Audio Bitrate
Author: Claude Haiku 4.5
Date: 2026-09-13
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional
import re

# ============================================================================
# CONFIGURATION
# ============================================================================

CORVINOS_ROOT = Path("/home/shumway/projects/CorvinOS")
OUTPUTS_DIR = CORVINOS_ROOT / "outputs"
AUDIO_REAL_DIR = OUTPUTS_DIR / "audio_real_fixed"
SUBTITLES_DIR = OUTPUTS_DIR / "subtitles"
VIDEO_FINAL_DIR = OUTPUTS_DIR / "video_final_fixed"

# Create directories
AUDIO_REAL_DIR.mkdir(parents=True, exist_ok=True)
SUBTITLES_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_FINAL_DIR.mkdir(parents=True, exist_ok=True)

# Video metadata
VIDEOS = [
    {
        'name': 'video_1_what_is_corvinos',
        'duration_seconds': 60,
        'storyboard': OUTPUTS_DIR / 'video_1_what_is_corvinos.storyboard.json',
    },
    {
        'name': 'video_2_architecture',
        'duration_seconds': 240,
        'storyboard': OUTPUTS_DIR / 'video_2_architecture.storyboard.json',
    },
    {
        'name': 'video_3_building_plugins',
        'duration_seconds': 900,
        'storyboard': OUTPUTS_DIR / 'video_3_building_plugins.storyboard.json',
    },
]

# ============================================================================
# LOGGING
# ============================================================================

def log_info(msg: str):
    print(f"[FIX-AUDIO] ℹ️  {msg}")

def log_success(msg: str):
    print(f"[FIX-AUDIO] ✅ {msg}")

def log_error(msg: str):
    print(f"[FIX-AUDIO] ❌ {msg}", file=sys.stderr)

def log_warn(msg: str):
    print(f"[FIX-AUDIO] ⚠️  {msg}")

# ============================================================================
# TTS GENERATION (REAL NARRATION)
# ============================================================================

def extract_narration_from_storyboard(storyboard_path: Path) -> str:
    """Extract all narration text from storyboard."""
    try:
        with open(storyboard_path, 'r') as f:
            storyboard = json.load(f)

        narrations = []
        for scene in storyboard.get('scenes', []):
            text = scene.get('narration', '').strip()
            if text:
                narrations.append(text)

        return " ".join(narrations)
    except Exception as e:
        log_error(f"Failed to extract narration from {storyboard_path}: {e}")
        return ""

def generate_tts_with_gtts(text: str, output_path: Path, speed_multiplier: float = 1.2) -> bool:
    """Generate TTS audio with gTTS (real narration)."""
    try:
        from gtts import gTTS

        log_info(f"Generating TTS audio ({len(text)} chars) → {output_path.name}")

        # Generate with gTTS
        tts = gTTS(text=text, lang='en', slow=False)
        tts.save(str(output_path))

        # Verify file exists and has reasonable size
        if output_path.exists() and output_path.stat().st_size > 5000:  # At least 5KB
            log_success(f"Generated TTS: {output_path.name} ({output_path.stat().st_size / 1024:.1f} KB)")
            return True
        else:
            log_error(f"TTS output too small: {output_path}")
            return False

    except Exception as e:
        log_error(f"gTTS generation failed: {e}")
        return False

def re_encode_audio_to_proper_bitrate(input_path: Path, output_path: Path, bitrate: str = "192k") -> bool:
    """Re-encode audio to ensure proper bitrate (not 1 kb/s)."""
    try:
        log_info(f"Re-encoding audio to {bitrate} bitrate: {input_path.name}")

        cmd = [
            'ffmpeg',
            '-i', str(input_path),
            '-codec:a', 'libmp3lame',
            '-b:a', bitrate,
            '-q:a', '4',  # Variable bitrate quality
            '-y',  # Overwrite
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=60, text=True)

        if result.returncode == 0 and output_path.exists():
            file_size = output_path.stat().st_size / 1024
            duration = get_audio_duration(output_path)
            actual_bitrate = (file_size * 8) / duration if duration > 0 else 0
            log_success(f"Re-encoded: {output_path.name} ({file_size:.1f} KB, ~{actual_bitrate:.0f} kb/s)")
            return True
        else:
            log_error(f"FFmpeg re-encoding failed: {result.stderr[:200]}")
            return False

    except Exception as e:
        log_error(f"Audio re-encoding failed: {e}")
        return False

# ============================================================================
# AUDIO UTILITIES
# ============================================================================

def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds."""
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1:nokey=1',
                str(audio_path),
            ],
            capture_output=True,
            timeout=10,
            text=True,
        )
        return float(result.stdout.strip())
    except:
        return 0

def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds."""
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1:nokey=1',
                str(video_path),
            ],
            capture_output=True,
            timeout=10,
            text=True,
        )
        return float(result.stdout.strip())
    except:
        return 0

# ============================================================================
# VIDEO REASSEMBLY
# ============================================================================

def find_source_video(video_name: str) -> Optional[Path]:
    """Find the source video file."""
    candidates = [
        OUTPUTS_DIR / f"{video_name}_v2.mp4",
        OUTPUTS_DIR / f"{video_name}.mp4",
        OUTPUTS_DIR / f"{video_name}_raw.mp4",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None

def reassemble_video_with_audio(
    video_source: Path,
    audio_source: Path,
    output_path: Path,
) -> bool:
    """Reassemble video with new audio using FFmpeg."""
    try:
        log_info(f"Reassembling video: {output_path.name}")

        if not video_source.exists():
            log_error(f"Source video not found: {video_source}")
            return False

        if not audio_source.exists():
            log_error(f"Source audio not found: {audio_source}")
            return False

        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-i', str(video_source),
            '-i', str(audio_source),
            '-c:v', 'libx264',
            '-crf', '23',
            '-preset', 'fast',
            '-c:a', 'aac',
            '-b:a', '192k',  # IMPORTANT: Proper bitrate, not 1 kb/s
            '-shortest',  # Use shortest stream
            '-y',  # Overwrite
            str(output_path),
        ]

        log_info(f"Running FFmpeg: video + audio merge")
        result = subprocess.run(cmd, capture_output=True, timeout=600, text=True)

        if result.returncode != 0:
            log_error(f"FFmpeg failed: {result.stderr[:300]}")
            return False

        if not output_path.exists() or output_path.stat().st_size < 500000:
            log_error(f"Output file invalid or too small")
            return False

        # Verify audio bitrate in final file
        audio_info = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=bit_rate', '-of', 'default=noprint_wrappers=1:nokey=1:nokey=1', str(output_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        audio_bitrate = audio_info.stdout.strip()
        log_info(f"Output audio bitrate: {audio_bitrate} bps")

        log_success(f"Reassembled: {output_path.name} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
        return True

    except subprocess.TimeoutExpired:
        log_error("FFmpeg timeout (>10 min)")
        return False
    except Exception as e:
        log_error(f"Video reassembly failed: {e}")
        return False

# ============================================================================
# VERIFICATION
# ============================================================================

def verify_video_audio(video_path: Path) -> Dict:
    """Verify video has proper audio."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,bit_rate,duration', '-show_entries', 'format=duration', '-of', 'json', str(video_path)],
            capture_output=True,
            timeout=10,
            text=True,
        )

        info = json.loads(result.stdout)

        audio_stream = None
        for stream in info.get('streams', []):
            if stream.get('codec_type') == 'audio':
                audio_stream = stream
                break

        return {
            'has_audio': audio_stream is not None,
            'audio_codec': audio_stream.get('codec_name', 'N/A') if audio_stream else None,
            'audio_bitrate': audio_stream.get('bit_rate', '?') if audio_stream else None,
            'duration': float(info.get('format', {}).get('duration', 0)),
        }
    except Exception as e:
        log_error(f"Verification failed: {e}")
        return {'has_audio': False}

# ============================================================================
# MAIN WORKFLOW
# ============================================================================

def main():
    print("\n" + "="*80)
    print("FIX VIDEO AUDIO: Generate REAL TTS + Reassemble with Proper Bitrate")
    print("="*80 + "\n")

    results = {
        'tts_generated': 0,
        'audio_reencoded': 0,
        'videos_reassembled': 0,
        'errors': [],
    }

    # Phase 1: Generate TTS Audio from Storyboards
    print("[Phase 1/3] Generating REAL TTS Narration Audio\n" + "-"*80)

    for video in VIDEOS:
        storyboard_path = video['storyboard']

        if not storyboard_path.exists():
            log_error(f"Storyboard not found: {storyboard_path}")
            results['errors'].append(f"Storyboard missing: {video['name']}")
            continue

        # Extract narration
        narration_text = extract_narration_from_storyboard(storyboard_path)

        if not narration_text:
            log_error(f"No narration text found in {video['name']}")
            results['errors'].append(f"No narration: {video['name']}")
            continue

        # Generate TTS
        audio_path_raw = AUDIO_REAL_DIR / f"{video['name']}_narration_raw.mp3"
        audio_path_final = AUDIO_REAL_DIR / f"{video['name']}_narration.mp3"

        if generate_tts_with_gtts(narration_text, audio_path_raw):
            results['tts_generated'] += 1

            # Re-encode to proper bitrate
            if re_encode_audio_to_proper_bitrate(audio_path_raw, audio_path_final, bitrate="192k"):
                results['audio_reencoded'] += 1
                # Clean up raw file
                audio_path_raw.unlink(missing_ok=True)
            else:
                results['errors'].append(f"Audio re-encoding failed: {video['name']}")
        else:
            results['errors'].append(f"TTS generation failed: {video['name']}")

    # Phase 2: Reassemble Videos
    print(f"\n[Phase 2/3] Reassembling Videos with Real Audio\n" + "-"*80)

    for video in VIDEOS:
        audio_path = AUDIO_REAL_DIR / f"{video['name']}_narration.mp3"
        output_path = VIDEO_FINAL_DIR / f"{video['name']}_FINAL.mp4"

        if not audio_path.exists():
            log_warn(f"Audio not found, skipping: {video['name']}")
            continue

        # Find source video
        source_video = find_source_video(video['name'])

        if not source_video:
            log_error(f"Source video not found: {video['name']}")
            results['errors'].append(f"Source video missing: {video['name']}")
            continue

        # Reassemble
        if reassemble_video_with_audio(source_video, audio_path, output_path):
            results['videos_reassembled'] += 1
        else:
            results['errors'].append(f"Video reassembly failed: {video['name']}")

    # Phase 3: Verify
    print(f"\n[Phase 3/3] Verifying Final Videos\n" + "-"*80)

    for video in VIDEOS:
        final_path = VIDEO_FINAL_DIR / f"{video['name']}_FINAL.mp4"

        if not final_path.exists():
            log_warn(f"Final video not found: {video['name']}")
            continue

        info = verify_video_audio(final_path)

        if info['has_audio']:
            log_success(f"✅ {video['name']}: has {info['audio_codec']} audio @ {info['audio_bitrate']} bps, duration {info['duration']:.0f}s")
        else:
            log_error(f"❌ {video['name']}: NO AUDIO")

    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"✅ TTS Generated: {results['tts_generated']}/3")
    print(f"✅ Audio Re-encoded: {results['audio_reencoded']}/3")
    print(f"✅ Videos Reassembled: {results['videos_reassembled']}/3")

    if results['errors']:
        print(f"\n⚠️  Errors ({len(results['errors'])})")
        for error in results['errors']:
            print(f"  - {error}")

    print(f"\n📁 Output Locations:")
    print(f"  Audio:  {AUDIO_REAL_DIR}")
    print(f"  Videos: {VIDEO_FINAL_DIR}")

    return 0 if results['videos_reassembled'] == 3 else 1

if __name__ == "__main__":
    sys.exit(main())
