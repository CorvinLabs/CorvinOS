#!/usr/bin/env python3
"""
Generate REAL Audio for Videos using espeak-ng + FFmpeg
Creates proper bitrate MP3 files with actual speech synthesis.
Author: Claude Haiku 4.5
Date: 2026-09-13
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional
import tempfile

# ============================================================================
# CONFIGURATION
# ============================================================================

CORVINOS_ROOT = Path("/home/shumway/projects/CorvinOS")
OUTPUTS_DIR = CORVINOS_ROOT / "outputs"
AUDIO_OUTPUT_DIR = OUTPUTS_DIR / "audio_final"
VIDEO_OUTPUT_DIR = OUTPUTS_DIR / "videos_with_real_audio"

AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VIDEOS = [
    {'name': 'video_1_what_is_corvinos', 'storyboard': OUTPUTS_DIR / 'video_1_what_is_corvinos.storyboard.json'},
    {'name': 'video_2_architecture', 'storyboard': OUTPUTS_DIR / 'video_2_architecture.storyboard.json'},
    {'name': 'video_3_building_plugins', 'storyboard': OUTPUTS_DIR / 'video_3_building_plugins.storyboard.json'},
]

# ============================================================================
# LOGGING
# ============================================================================

def log(msg: str, level: str = "INFO"):
    level_map = {"INFO": "ℹ️ ", "OK": "✅", "ERR": "❌", "WARN": "⚠️ "}
    prefix = f"[AUDIO-GEN] {level_map.get(level, '•')}"
    print(f"{prefix} {msg}")

# ============================================================================
# EXTRACT NARRATION
# ============================================================================

def extract_narration(storyboard_path: Path) -> str:
    """Extract all narration from storyboard."""
    if not storyboard_path.exists():
        log(f"Storyboard not found: {storyboard_path}", "ERR")
        return ""

    try:
        with open(storyboard_path) as f:
            sb = json.load(f)

        texts = []
        for scene in sb.get('scenes', []):
            narration = scene.get('narration', '').strip()
            if narration:
                texts.append(narration)

        result = " ".join(texts)
        log(f"Extracted {len(texts)} narrations ({len(result)} chars)", "OK")
        return result
    except Exception as e:
        log(f"Failed to parse storyboard: {e}", "ERR")
        return ""

# ============================================================================
# TTS WITH ESPEAK-NG
# ============================================================================

def generate_audio_with_espeak_ng(text: str, output_path: Path) -> bool:
    """Generate audio with espeak-ng."""
    try:
        log(f"Generating speech audio for {len(text)} characters...")

        # Step 1: Generate WAV with espeak-ng
        wav_temp = Path(tempfile.gettempdir()) / "temp_tts.wav"

        cmd_espeak = [
            'espeak-ng',
            '-v', 'en-us',           # English (US)
            '-s', '150',             # Speed: 150 WPM
            '--stdout',
            text,
        ]

        with open(wav_temp, 'wb') as wav_file:
            result = subprocess.run(cmd_espeak, stdout=wav_file, stderr=subprocess.PIPE, timeout=60)

        if result.returncode != 0:
            log(f"espeak-ng failed: {result.stderr.decode()[:200]}", "ERR")
            wav_temp.unlink(missing_ok=True)
            return False

        if not wav_temp.exists() or wav_temp.stat().st_size < 1000:
            log(f"espeak-ng output too small", "ERR")
            wav_temp.unlink(missing_ok=True)
            return False

        log(f"Generated WAV: {wav_temp.stat().st_size / 1024:.1f} KB")

        # Step 2: Convert WAV to MP3 with proper bitrate (192 kbps)
        cmd_ffmpeg = [
            'ffmpeg',
            '-i', str(wav_temp),
            '-codec:a', 'libmp3lame',
            '-b:a', '192k',          # IMPORTANT: 192 kbps bitrate
            '-q:a', '4',             # High quality
            '-y',
            str(output_path),
        ]

        result = subprocess.run(cmd_ffmpeg, capture_output=True, timeout=60)

        wav_temp.unlink(missing_ok=True)

        if result.returncode != 0:
            log(f"FFmpeg conversion failed: {result.stderr.decode()[:200]}", "ERR")
            return False

        if not output_path.exists():
            log(f"Output file not created", "ERR")
            return False

        file_size = output_path.stat().st_size / 1024
        duration = get_audio_duration(output_path)
        bitrate = (file_size * 8) / duration if duration > 0 else 0

        log(f"Generated MP3: {output_path.name} ({file_size:.1f} KB, ~{bitrate:.0f} kb/s, {duration:.1f}s)", "OK")
        return True

    except subprocess.TimeoutExpired:
        log(f"Timeout during audio generation", "ERR")
        return False
    except Exception as e:
        log(f"Audio generation error: {e}", "ERR")
        return False

# ============================================================================
# AUDIO UTILITIES
# ============================================================================

def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1:nokey=1', str(audio_path)],
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

def find_source_video(name: str) -> Optional[Path]:
    """Find source video file."""
    candidates = [
        OUTPUTS_DIR / f"{name}_v2.mp4",
        OUTPUTS_DIR / f"{name}.mp4",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None

def reassemble_video(video_source: Path, audio_source: Path, output_path: Path) -> bool:
    """Reassemble video with audio."""
    try:
        log(f"Reassembling: {output_path.name}")

        if not video_source.exists():
            log(f"Video source missing: {video_source}", "ERR")
            return False

        if not audio_source.exists():
            log(f"Audio source missing: {audio_source}", "ERR")
            return False

        cmd = [
            'ffmpeg',
            '-i', str(video_source),
            '-i', str(audio_source),
            '-c:v', 'libx264',
            '-crf', '23',
            '-preset', 'fast',
            '-c:a', 'aac',
            '-b:a', '192k',           # CRITICAL: Proper bitrate
            '-shortest',
            '-y',
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=600, text=True)

        if result.returncode != 0:
            log(f"FFmpeg error: {result.stderr[:200]}", "ERR")
            return False

        if not output_path.exists():
            log(f"Output file not created", "ERR")
            return False

        size = output_path.stat().st_size / 1024 / 1024
        log(f"Reassembled: {output_path.name} ({size:.1f} MB)", "OK")
        return True

    except Exception as e:
        log(f"Reassembly error: {e}", "ERR")
        return False

# ============================================================================
# VERIFICATION
# ============================================================================

def verify_video_audio(video_path: Path) -> bool:
    """Verify video has proper audio."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_name,bit_rate', '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path)],
            capture_output=True,
            timeout=10,
            text=True,
        )

        output_lines = result.stdout.strip().split('\n')
        audio_codec = output_lines[0] if len(output_lines) > 0 else "NONE"
        audio_bitrate = output_lines[1] if len(output_lines) > 1 else "0"

        has_audio = audio_codec != "NONE"
        bitrate_ok = int(audio_bitrate) > 100000 if audio_bitrate.isdigit() else False

        if has_audio and bitrate_ok:
            log(f"{video_path.name}: {audio_codec} @ {int(audio_bitrate)//1000} kb/s ✅", "OK")
            return True
        else:
            log(f"{video_path.name}: codec={audio_codec}, bitrate={audio_bitrate} ❌", "ERR")
            return False
    except Exception as e:
        log(f"Verification error: {e}", "ERR")
        return False

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "="*80)
    print("GENERATE REAL VIDEO AUDIO")
    print("Phase: espeak-ng TTS + FFmpeg Reassembly")
    print("="*80 + "\n")

    success_count = 0

    # Generate audio for each video
    for video_info in VIDEOS:
        video_name = video_info['name']
        sb_path = video_info['storyboard']

        log(f"\n[{video_name}]")
        log(f"Storyboard: {sb_path}")

        # Extract narration
        narration = extract_narration(sb_path)
        if not narration:
            log(f"Skipping {video_name} (no narration)", "WARN")
            continue

        # Generate audio
        audio_output = AUDIO_OUTPUT_DIR / f"{video_name}_narration.mp3"
        if not generate_audio_with_espeak_ng(narration, audio_output):
            log(f"Audio generation failed for {video_name}", "ERR")
            continue

        # Find and reassemble video
        source_video = find_source_video(video_name)
        if not source_video:
            log(f"Source video not found for {video_name}", "ERR")
            continue

        video_output = VIDEO_OUTPUT_DIR / f"{video_name}_FINAL.mp4"
        if not reassemble_video(source_video, audio_output, video_output):
            log(f"Video reassembly failed for {video_name}", "ERR")
            continue

        # Verify
        if verify_video_audio(video_output):
            success_count += 1
        else:
            log(f"Audio verification failed for {video_name}", "ERR")

    # Summary
    print("\n" + "="*80)
    print(f"RESULT: {success_count}/3 videos successfully created with real audio")
    print(f"Audio:  {AUDIO_OUTPUT_DIR}")
    print(f"Videos: {VIDEO_OUTPUT_DIR}")
    print("="*80 + "\n")

    return 0 if success_count == 3 else 1

if __name__ == "__main__":
    sys.exit(main())
