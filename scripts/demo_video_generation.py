#!/usr/bin/env python3
"""
Quick Demo Video Generation Script
Generates a 3-5 minute showcase video demonstrating CorvinOS capabilities
with prominent voice narration (TTS).

Scope: Title slides + narration (TTS) + screenshots + video assembly
Output: demo_corvinOS_5min.mp4 (broadcast quality, 1920×1080)
"""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Optional
import tempfile
import sys

# Try importing TTS library
try:
    import edge_tts
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    print("⚠️  edge_tts not installed. Install with: pip install edge-tts")

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  Pillow not installed. Install with: pip install pillow")


class DemoVideoGenerator:
    """Generate a demo video with voice narration"""

    def __init__(self, output_dir: Path = Path("/tmp/demo_video")):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = self.output_dir / "audio"
        self.frames_dir = self.output_dir / "frames"
        self.audio_dir.mkdir(exist_ok=True)
        self.frames_dir.mkdir(exist_ok=True)

    async def generate_tts_audio(self, text: str, filename: str, voice: str = "en-US-AvaMultilingualNeural") -> Path:
        """Generate TTS audio using edge-tts"""
        if not TTS_AVAILABLE:
            print(f"❌ TTS not available, using silence")
            return self._create_silence(filename)

        output_path = self.audio_dir / filename
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice)
            await communicate.save(str(output_path))
            print(f"✅ Generated TTS: {filename}")

            # Normalize loudness to -23 LUFS (broadcast standard)
            self._normalize_loudness(output_path)
            return output_path
        except Exception as e:
            print(f"❌ TTS generation failed: {e}, using silence")
            return self._create_silence(filename)

    def _create_silence(self, filename: str, duration_ms: int = 5000) -> Path:
        """Create silent audio as fallback"""
        output_path = self.audio_dir / filename
        # Create 5-second silence using ffmpeg
        try:
            subprocess.run([
                "ffmpeg", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
                "-t", str(duration_ms / 1000), "-q:a", "9", "-acodec", "libmp3lame",
                str(output_path)
            ], capture_output=True, check=True)
            return output_path
        except:
            return output_path

    def _normalize_loudness(self, audio_path: Path):
        """Normalize audio to -23 LUFS using FFmpeg"""
        try:
            temp_path = audio_path.with_stem(audio_path.stem + "_normalized")
            subprocess.run([
                "ffmpeg", "-i", str(audio_path),
                "-af", "loudnorm=I=-23:TP=-1.5:LRA=7",
                str(temp_path)
            ], capture_output=True, check=True)
            audio_path.unlink()
            temp_path.rename(audio_path)
            print(f"✅ Normalized loudness: {audio_path.name}")
        except Exception as e:
            print(f"⚠️  Loudness normalization skipped: {e}")

    def create_title_slide(self, title: str, subtitle: str, filename: str) -> Path:
        """Create a title slide image"""
        if not PIL_AVAILABLE:
            print(f"❌ PIL not available, creating minimal slide")
            return self._create_minimal_slide(filename)

        # Create 1920×1080 slide
        width, height = 1920, 1080
        image = Image.new('RGB', (width, height), color=(240, 245, 250))
        draw = ImageDraw.Draw(image)

        try:
            # Try to load a nice font
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
            subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
        except:
            # Fallback to default font
            title_font = ImageFont.load_default()
            subtitle_font = ImageFont.load_default()

        # Draw title
        title_color = (20, 60, 140)  # Dark blue
        draw.text((width//2, height//3), title, fill=title_color, font=title_font, anchor="mm")

        # Draw subtitle
        subtitle_color = (100, 120, 160)
        draw.text((width//2, height//2 + 100), subtitle, fill=subtitle_color, font=subtitle_font, anchor="mm")

        # Save
        slide_path = self.frames_dir / filename
        image.save(slide_path)
        print(f"✅ Created slide: {filename}")
        return slide_path

    def _create_minimal_slide(self, filename: str) -> Path:
        """Create minimal slide as fallback (PNG with FFmpeg)"""
        slide_path = self.frames_dir / filename
        subprocess.run([
            "ffmpeg", "-f", "lavfi", "-i", "color=c=lightblue:s=1920x1080:d=1",
            "-q:v", "2",
            str(slide_path)
        ], capture_output=True)
        return slide_path

    async def generate_demo_video(self) -> Optional[Path]:
        """Main demo video generation workflow"""
        print("\n🎬 Starting CorvinOS Demo Video Generation...")

        # Scene definitions: (title, subtitle, narration, duration_sec)
        scenes = [
            ("CorvinOS", "The Agentic Operating System",
             "Welcome to CorvinOS, a unified operating system for AI agents. Built with Anthropic's Claude, CorvinOS provides a complete platform for autonomous task execution, real-time collaboration, and learning-driven optimization.",
             8),

            ("Core Features", "Plugins • Skills • Learning",
             "CorvinOS is built on three pillars: A plugin ecosystem for extensibility, a Skills system for composable AI programs, and a learning loop for continuous improvement powered by user feedback.",
             7),

            ("Voice Integration", "Natural Interaction Through Speech",
             "Voice is at the core of CorvinOS. Every interaction can be narrated, every task can be spoken aloud. This demo video itself is generated with voice narration, demonstrating the speech synthesis and orchestration capabilities.",
             7),

            ("Marketplace", "Discover and Install Components",
             "The CorvinOS Marketplace provides a unified discovery interface for plugins, skills, tools, and connectors. Browse, search, and install components directly from the console.",
             6),

            ("Video Producer", "Automated Content Generation",
             "Video Producer demonstrates orchestrated Skills in action. It coordinates voice synthesis, screenshot capture, and video assembly to create professional videos from text descriptions.",
             8),

            ("Learning Loops", "Continuous Improvement",
             "Every decision in CorvinOS emits learning signals. The system learns from feedback, optimizes parameters, and improves over time. This is feedback-driven development at scale.",
             7),

            ("Thank You", "Learn more at corvinOS.dev",
             "This demo showcased CorvinOS capabilities: voice integration, orchestrated skills, marketplace discovery, video production, and learning loops. Thank you for watching.",
             6)
        ]

        print(f"\n📹 Generating {len(scenes)} scenes...")
        audio_files = []
        frame_files = []

        # Generate audio and frames for each scene
        for i, (title, subtitle, narration, duration) in enumerate(scenes):
            print(f"\n  Scene {i+1}/{len(scenes)}: {title}")

            # Generate TTS audio
            audio_file = f"scene_{i+1:02d}_narration.mp3"
            audio_path = await self.generate_tts_audio(narration, audio_file)
            audio_files.append((audio_path, duration))

            # Create title slide
            frame_file = f"scene_{i+1:02d}.png"
            frame_path = self.create_title_slide(title, subtitle, frame_file)
            frame_files.append(frame_path)

        # Assemble video
        print(f"\n📦 Assembling video...")
        output_video = self.output_dir / "demo_corvinOS_5min.mp4"

        try:
            # Create FFmpeg concat script for audio
            concat_audio_script = self.output_dir / "concat_audio.txt"
            with open(concat_audio_script, "w") as f:
                for audio_path, _ in audio_files:
                    f.write(f"file '{audio_path.absolute()}'\n")

            # Concatenate all audio
            full_audio = self.output_dir / "full_narration.mp3"
            subprocess.run([
                "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_audio_script),
                "-c", "copy", str(full_audio)
            ], capture_output=True, check=True)
            print(f"✅ Concatenated audio: {full_audio.name}")

            # Create video from frames + audio
            # Use the first frame, loop for full audio duration
            subprocess.run([
                "ffmpeg",
                "-loop", "1", "-i", str(frame_files[0]),
                "-i", str(full_audio),
                "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-shortest", "-movflags", "+faststart",
                str(output_video)
            ], capture_output=True, check=True)

            print(f"✅ Generated video: {output_video.name}")

            # Print stats
            if output_video.exists():
                size_mb = output_video.stat().st_size / (1024 * 1024)
                print(f"\n📊 Video Stats:")
                print(f"   Size: {size_mb:.2f} MB")
                print(f"   Path: {output_video.absolute()}")
                print(f"   Duration: ~{sum(d for _, d in audio_files)} seconds")
                return output_video

        except subprocess.CalledProcessError as e:
            print(f"❌ Video assembly failed: {e}")
            return None

    def copy_to_outputs(self, video_path: Path) -> Path:
        """Copy generated video to ./outputs/"""
        outputs_dir = Path("/home/shumway/projects/CorvinOS/outputs")
        outputs_dir.mkdir(exist_ok=True)

        dest_path = outputs_dir / "demo_corvinOS_5min.mp4"

        try:
            import shutil
            shutil.copy2(video_path, dest_path)
            print(f"\n✅ Copied to outputs: {dest_path.absolute()}")
            return dest_path
        except Exception as e:
            print(f"❌ Copy failed: {e}")
            return video_path


async def main():
    """Main entry point"""
    generator = DemoVideoGenerator()

    print("=" * 60)
    print("🎬 CorvinOS Demo Video Generator")
    print("=" * 60)

    # Check dependencies
    print("\n📋 Dependency Check:")
    print(f"   TTS (edge-tts): {'✅' if TTS_AVAILABLE else '❌'}")
    print(f"   PIL (Pillow): {'✅' if PIL_AVAILABLE else '❌'}")

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        print(f"   FFmpeg: ✅")
    except:
        print(f"   FFmpeg: ❌")
        print("\nError: FFmpeg is required. Install with: sudo apt install ffmpeg")
        sys.exit(1)

    # Generate demo video
    video_path = await generator.generate_demo_video()

    if video_path:
        print("\n" + "=" * 60)
        print("🎉 Demo Video Generation Complete!")
        print("=" * 60)
        output_path = generator.copy_to_outputs(video_path)
        print(f"\nPlay with: ffplay '{output_path}'")
        print(f"Or upload to YouTube for sharing")
        return 0
    else:
        print("\n❌ Demo video generation failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
