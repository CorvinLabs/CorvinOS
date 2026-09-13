#!/usr/bin/env python3
"""VIDEO PRODUCER PHASE 4: REAL DEMO VIDEO GENERATION

Generates a real, working 90-second CorvinOS demo video with:
- Multi-provider TTS (OpenAI primary)
- REAL audio verified with ffprobe
- FFmpeg video assembly
- Subtitle generation
- MP4 output ready for YouTube

Usage:
    python3 scripts/generate_real_corvinos_demo.py
"""

import sys
import os
import subprocess
import json
from pathlib import Path
from dataclasses import dataclass

# Add project to path
sys.path.insert(0, "/home/shumway/projects/CorvinOS")

from core.skills.video_producer.workers.voice_synthesizer_multiprovider import (
    VoiceSynthesizerMultiProvider,
)


print("=" * 80)
print("🎬 VIDEO PRODUCER PHASE 4: REAL DEMO VIDEO GENERATION")
print("=" * 80)
print()

# Define the demo narration (90 seconds target)
DEMO_NARRATION = [
    "What is CorvinOS? It is an open source operating system for AI agents that learn from experience.",
    "CorvinOS powers distributed task automation with real-time audit trails, multi-engine skill composition, and closed-loop learning loops.",
    "Generate professional videos, manage distributed workflows, and let your system improve with every run. Visit corvin-labs.com to get started.",
]

# Job definition
@dataclass
class VideoJob:
    job_id: str = "corvinos_real_demo"
    narration: list = None

    def __post_init__(self):
        if self.narration is None:
            self.narration = DEMO_NARRATION


def verify_audio_real(audio_files):
    """Verify that audio files actually exist and have real content"""

    print("\n🔍 Audio Verification (ffprobe):")
    print("-" * 60)

    total_duration = 0.0
    verified_files = []

    for i, audio_file in enumerate(audio_files):
        # Check file exists
        if not os.path.exists(audio_file):
            print(f"  ✗ Scene {i}: FILE NOT FOUND: {audio_file}")
            continue

        # Check file size
        file_size = os.path.getsize(audio_file)
        if file_size == 0:
            print(f"  ✗ Scene {i}: EMPTY FILE (0 bytes)")
            continue

        # Use ffprobe to verify audio content
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration,size",
                    "-show_entries", "stream=codec_type,duration",
                    "-of", "json",
                    audio_file,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                print(f"  ✗ Scene {i}: ffprobe failed")
                continue

            data = json.loads(result.stdout)

            # Extract duration
            duration = 0.0
            if "format" in data and "duration" in data["format"]:
                duration = float(data["format"]["duration"])
            elif "streams" in data and len(data["streams"]) > 0:
                if "duration" in data["streams"][0]:
                    duration = float(data["streams"][0]["duration"])

            if duration <= 0:
                print(f"  ✗ Scene {i}: Invalid duration (0 or negative)")
                continue

            total_duration += duration
            verified_files.append(audio_file)

            print(f"  ✓ Scene {i}: {file_size:,} bytes, {duration:.2f}s ✓")

        except Exception as e:
            print(f"  ✗ Scene {i}: Error - {e}")
            continue

    print()
    print(f"✅ Verified {len(verified_files)}/{len(audio_files)} audio files")
    print(f"   Total duration: {total_duration:.1f} seconds")

    if len(verified_files) == 0:
        print("\n❌ FAILURE: No valid audio files created")
        return False, []

    return True, verified_files, total_duration


def create_subtitles(audio_files, narration, job_id):
    """Generate SRT subtitle file based on narration"""

    print("\n📝 Subtitle Generation:")
    print("-" * 60)

    srt_path = f"/tmp/{job_id}_subtitles.srt"

    try:
        with open(srt_path, "w", encoding="utf-8") as f:
            current_time = 0.0

            for i, (audio_file, text) in enumerate(zip(audio_files, narration)):
                # Get duration
                result = subprocess.run(
                    [
                        "ffprobe",
                        "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        audio_file,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

                duration = 5.0
                if result.returncode == 0:
                    try:
                        duration = float(result.stdout.strip())
                    except:
                        pass

                # Format timestamps
                start_h = int(current_time // 3600)
                start_m = int((current_time % 3600) // 60)
                start_s = int(current_time % 60)
                start_ms = int((current_time % 1) * 1000)

                end_time = current_time + duration
                end_h = int(end_time // 3600)
                end_m = int((end_time % 3600) // 60)
                end_s = int(end_time % 60)
                end_ms = int((end_time % 1) * 1000)

                # Write SRT entry
                f.write(f"{i + 1}\n")
                f.write(f"{start_h:02d}:{start_m:02d}:{start_s:02d},{start_ms:03d} --> {end_h:02d}:{end_m:02d}:{end_s:02d},{end_ms:03d}\n")
                f.write(f"{text}\n")
                f.write("\n")

                current_time = end_time

        print(f"  ✓ Created SRT file: {srt_path}")
        print(f"  ✓ {len(narration)} subtitle entries")

        return srt_path

    except Exception as e:
        print(f"  ✗ Subtitle generation failed: {e}")
        return None


def create_simple_video(audio_files, output_path, job_id):
    """Create MP4 video from audio files with silent video track"""

    print("\n🎬 Video Assembly (FFmpeg):")
    print("-" * 60)

    try:
        # Get total audio duration
        total_duration = 0.0
        for audio_file in audio_files:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    audio_file,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                total_duration += float(result.stdout.strip())

        print(f"  Total audio duration: {total_duration:.1f}s")

        # Concatenate audio files using ffmpeg
        concat_file = f"/tmp/{job_id}_concat.txt"

        with open(concat_file, "w") as f:
            for audio_file in audio_files:
                f.write(f"file '{audio_file}'\n")

        # Create video with audio
        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-f", "lavfi",
            "-i", f"color=c=black:s=1920x1080:d={total_duration}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-q:a", "100",
            "-pix_fmt", "yuv420p",
            "-y",
            output_path,
        ]

        print(f"  Running FFmpeg...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            print(f"  ✗ FFmpeg failed: {result.stderr[:200]}")
            return False

        # Verify output file
        if not os.path.exists(output_path):
            print(f"  ✗ Output file not created")
            return False

        file_size = os.path.getsize(output_path)
        print(f"  ✓ Video created: {file_size:,} bytes")

        # Verify with ffprobe
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            duration = float(result.stdout.strip())
            print(f"  ✓ Duration verified: {duration:.1f}s")
            return True

        return False

    except Exception as e:
        print(f"  ✗ Video assembly failed: {e}")
        return False


def main():
    """Main execution"""

    # Create job
    job = VideoJob()

    print(f"📋 Job: {job.job_id}")
    print(f"   Narration scenes: {len(job.narration)}")
    print(f"   Target duration: ~90 seconds")
    print()

    # ====== PHASE 1: VOICE SYNTHESIS ======
    print("=" * 80)
    print("PHASE 1: VOICE SYNTHESIS")
    print("=" * 80)

    tts_worker = VoiceSynthesizerMultiProvider()
    voice_result = tts_worker.execute(job)

    if not voice_result.success:
        print("\n❌ FAILURE: TTS execution failed")
        return False

    # ====== PHASE 2: AUDIO VERIFICATION ======
    print("\n" + "=" * 80)
    print("PHASE 2: AUDIO VERIFICATION")
    print("=" * 80)

    success, verified_files, total_duration = verify_audio_real(voice_result.audio_files)

    if not success:
        print("\n❌ FAILURE: Audio verification failed")
        return False

    # ====== PHASE 3: SUBTITLE GENERATION ======
    print("\n" + "=" * 80)
    print("PHASE 3: SUBTITLE GENERATION")
    print("=" * 80)

    srt_path = create_subtitles(verified_files, job.narration, job.job_id)

    # ====== PHASE 4: VIDEO ASSEMBLY ======
    print("\n" + "=" * 80)
    print("PHASE 4: VIDEO ASSEMBLY")
    print("=" * 80)

    output_path = f"/home/shumway/projects/CorvinOS/outputs/corvinos_real_demo_final.mp4"

    # Create outputs directory if needed
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    success = create_simple_video(verified_files, output_path, job.job_id)

    if not success:
        print("\n❌ FAILURE: Video assembly failed")
        return False

    # ====== FINAL VERIFICATION ======
    print("\n" + "=" * 80)
    print("FINAL VERIFICATION")
    print("=" * 80)

    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path)
        print(f"\n✅ VIDEO FILE CREATED")
        print(f"   Path: {output_path}")
        print(f"   Size: {file_size:,} bytes ({file_size / 1024 / 1024:.1f} MB)")

        # Final ffprobe verification
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration,size,bit_rate",
                "-show_entries", "stream=codec_type,codec_name",
                "-of", "json",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            if "format" in data:
                duration = data["format"].get("duration", 0)
                bit_rate = data["format"].get("bit_rate", 0)
                print(f"   Duration: {float(duration):.1f}s")
                print(f"   Bitrate: {int(bit_rate) // 1000} kbps" if bit_rate else "   Bitrate: unknown")

        print(f"\n🎉 SUCCESS: Demo video generated with REAL audio!")
        return True
    else:
        print(f"\n❌ FAILURE: Video file not found at {output_path}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
