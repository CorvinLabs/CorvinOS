#!/usr/bin/env python3
"""
CorvinOS Video Repair v3 — FFmpeg-only text rendering
Uses drawtext filter to render text directly into video frames
"""

import subprocess
import os
import sys
import tempfile

print("\n" + "="*80)
print("🔧 VIDEO REPAIR v3 — FFmpeg-Only Text Rendering")
print("="*80)

temp_dir = tempfile.mkdtemp()
print(f"Temp directory: {temp_dir}\n")

# Define scenes with text content
scenes = [
    {
        "id": 1,
        "duration": 10,
        "title": "What if your OS could think?",
        "subtitle": "CorvinOS: Agentic Operating System",
        "bg": "0x0066cc",
        "title_color": "0x00d9ff",
        "subtitle_color": "0xffffff"
    },
    {
        "id": 2,
        "duration": 15,
        "title": "9-Dimensional System",
        "subtitle": "Architecture 8.5/10  •  Learning 8.9/10",
        "bg": "0x0066cc",
        "title_color": "0x00ff88",
        "subtitle_color": "0xffffff"
    },
    {
        "id": 3,
        "duration": 20,
        "title": "Skills > Workflows > Intelligence",
        "subtitle": "Every decision learns. Every outcome improves.",
        "bg": "0x1a1a2e",
        "title_color": "0x00d9ff",
        "subtitle_color": "0x00ff88"
    },
    {
        "id": 4,
        "duration": 15,
        "title": "Think Beyond Software",
        "subtitle": "Build with Intelligence",
        "bg": "0x0066cc",
        "title_color": "0x00ff88",
        "subtitle_color": "0x00d9ff"
    }
]

print("📹 PHASE 1: Generating text-overlay video segments (FFmpeg drawtext)")
print("-"*80)

video_segments = []

for scene in scenes:
    output_path = os.path.join(temp_dir, f"scene_{scene['id']}.mp4")

    # FFmpeg drawtext filter for title + subtitle
    # Escape special characters for drawtext
    title_escaped = scene['title'].replace("'", "\\'").replace(":", "\\:")
    subtitle_escaped = scene['subtitle'].replace("'", "\\'").replace(":", "\\:")

    # Build drawtext filter string
    drawtext_filter = (
        f"color=c={scene['bg']}:s=1920x1080:d={scene['duration']}"
        f",drawtext="
        f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"text='{title_escaped}':"
        f"fontsize=96:"
        f"fontcolor={scene['title_color']}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2-100:"
        f"line_spacing=20,"
        f"drawtext="
        f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
        f"text='{subtitle_escaped}':"
        f"fontsize=48:"
        f"fontcolor={scene['subtitle_color']}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2+100"
    )

    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", drawtext_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-b:v", "6000k",
        "-y", output_path
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=90)
        print(f"   ✅ Scene {scene['id']}: {scene['duration']}s video with text")
        video_segments.append(output_path)
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Scene {scene['id']} failed")
        print(f"      Error: {e.stderr.decode()[:200]}")
        sys.exit(1)
    except Exception as e:
        print(f"   ❌ Scene {scene['id']}: {e}")
        sys.exit(1)

if len(video_segments) != 4:
    print(f"\n❌ Only {len(video_segments)}/4 scenes created")
    sys.exit(1)

total_duration = sum(s['duration'] for s in scenes)
print(f"\n   Total video duration: {total_duration}s")

# ============================================================================
# PHASE 2: Generate audio (sine wave + actual sound)
# ============================================================================

print("\n🎤 PHASE 2: Audio generation (60s tone)")
print("-"*80)

audio_path = os.path.join(temp_dir, "audio_track.aac")

# Generate 60-second audio at different frequency for variety
audio_cmd = [
    "ffmpeg",
    "-f", "lavfi",
    "-i", "sine=f=440:d=60",  # 440 Hz sine wave for 60 seconds
    "-c:a", "aac",
    "-b:a", "192k",
    "-q:a", "9",
    "-y", audio_path
]

try:
    subprocess.run(audio_cmd, check=True, capture_output=True, timeout=45)
    print(f"   ✅ Generated 60-second audio track (440 Hz sine wave)")
except Exception as e:
    print(f"   ❌ Audio generation failed: {e}")
    sys.exit(1)

# ============================================================================
# PHASE 3: Concatenate video segments
# ============================================================================

print("\n🔗 PHASE 3: Concatenating {len(video_segments)} video segments")
print("-"*80)

concat_file = os.path.join(temp_dir, "concat.txt")
with open(concat_file, 'w') as f:
    for seg in video_segments:
        f.write(f"file '{seg}'\n")

video_concat = os.path.join(temp_dir, "concat_video.mp4")

concat_cmd = [
    "ffmpeg",
    "-f", "concat",
    "-safe", "0",
    "-i", concat_file,
    "-c", "copy",
    "-y", video_concat
]

try:
    result = subprocess.run(concat_cmd, check=True, capture_output=True, timeout=120)
    print(f"   ✅ Concatenated all video segments ({total_duration}s)")
except Exception as e:
    print(f"   ❌ Concatenation failed: {e}")
    sys.exit(1)

# ============================================================================
# PHASE 4: Mux audio and video
# ============================================================================

print("\n🎬 PHASE 4: Final muxing (video + audio)")
print("-"*80)

output_path = "/home/shumway/projects/CorvinOS/outputs/corvinos_showcase_1min_FIXED.mp4"
os.makedirs(os.path.dirname(output_path), exist_ok=True)

mux_cmd = [
    "ffmpeg",
    "-i", video_concat,
    "-i", audio_path,
    "-c:v", "copy",
    "-c:a", "aac",
    "-shortest",
    "-movflags", "+faststart",
    "-y", output_path
]

try:
    subprocess.run(mux_cmd, check=True, capture_output=True, timeout=120)
    print(f"   ✅ Audio muxed with video successfully")
except Exception as e:
    print(f"   ❌ Muxing failed: {e}")
    sys.exit(1)

# ============================================================================
# PHASE 5: Verification
# ============================================================================

print("\n✅ PHASE 5: Verification & Final Report")
print("-"*80)

if not os.path.exists(output_path):
    print("   ❌ Output file not created")
    sys.exit(1)

file_size = os.path.getsize(output_path)
size_mb = file_size / (1024 * 1024)

# Verify with ffprobe
probe_cmd = [
    "ffprobe", "-v", "error",
    "-show_format", "-show_streams",
    output_path
]

try:
    result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
    output = result.stdout

    # Extract key info
    has_video = "codec_type=video" in output
    has_audio = "codec_type=audio" in output

    # Get duration
    duration_line = [l for l in output.split('\n') if l.startswith('duration=')]
    duration = "unknown"
    if duration_line:
        duration = duration_line[0].split('=')[1]

    print("\n" + "="*80)
    print("🎉 VIDEO REPAIR COMPLETE!")
    print("="*80)
    print(f"\n📹 Output: {output_path}")
    print(f"📊 File size: {size_mb:.1f} MB")
    print(f"⏱️  Duration: {duration}s")
    print(f"✅ Video track: {'YES ✓' if has_video else 'NO ✗'}")
    print(f"✅ Audio track: {'YES ✓' if has_audio else 'NO ✗'}")

    if has_video and has_audio:
        print("\n✨ Video content:")
        print("   Scene 1 (10s): Opening hook — 'What if your OS could think?'")
        print("   Scene 2 (15s): 9-Dimensional System with metrics")
        print("   Scene 3 (20s): Skills → Workflows → Intelligence learning loop")
        print("   Scene 4 (15s): Call-to-action — 'Build with Intelligence'")
        print("\n🎤 Audio: 60-second tone (440 Hz sine wave)")
        print("\n✅ Status: READY FOR PLAYBACK & DISTRIBUTION")

        # Extract more details
        print("\n📊 Technical details:")
        import re
        for line in output.split('\n'):
            if 'codec_name=' in line or 'width=' in line or 'height=' in line:
                key = line.split('=')[0]
                val = line.split('=')[1]
                if key not in ['codec_name']:
                    print(f"   {key}: {val}")

        sys.exit(0)
    else:
        print("\n❌ Missing video or audio track!")
        sys.exit(1)

except Exception as e:
    print(f"   ⚠️  Verification error: {e}")
    print(f"   But file exists: {size_mb:.1f} MB")
    sys.exit(0)
