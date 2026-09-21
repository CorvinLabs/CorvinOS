"""Composition (FFmpeg) — Phase 2

Merges video frames + audio → MP4 using FFmpeg.
"""

import os
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoComposer:
    """FFmpeg-based video composition"""

    def __init__(self):
        # Check FFmpeg availability
        result = subprocess.run(["which", "ffmpeg"], capture_output=True)
        if result.returncode != 0:
            raise RuntimeError("FFmpeg not found. Install: sudo apt-get install ffmpeg")

    def compose_video(
        self,
        frame_dir: str,
        audio_file: str,
        output_mp4: str,
        fps: int = 30,
        resolution: tuple = (1920, 1080),
    ) -> str:
        """Compose video from frames + audio

        Args:
            frame_dir: Directory with PNG frames (frame_000000.png, ...)
            audio_file: MP3 audio file
            output_mp4: Output MP4 path
            fps: Frames per second
            resolution: Video resolution (width, height)

        Returns:
            Path to generated MP4 file
        """
        logger.info(f"🎬 Composing video: {output_mp4}")

        # Convert MP3 → WAV for FFmpeg
        wav_file = audio_file.replace(".mp3", ".wav")
        self._mp3_to_wav(audio_file, wav_file)

        # FFmpeg: merge frames + audio
        ffmpeg_cmd = [
            "ffmpeg",
            "-framerate", str(fps),
            "-i", os.path.join(frame_dir, "frame_%06d.png"),
            "-i", wav_file,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-map", "0:v:0",
            "-map", "1:a:0",
            output_mp4,
            "-y",  # Overwrite
        ]

        try:
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600)

            if result.returncode == 0:
                file_size_mb = os.path.getsize(output_mp4) / (1024 * 1024)
                logger.info(f"✅ Video composed: {file_size_mb:.2f} MB → {output_mp4}")
                return output_mp4
            else:
                logger.error(f"❌ FFmpeg error: {result.stderr[:500]}")
                raise RuntimeError(f"FFmpeg failed: {result.returncode}")

        except subprocess.TimeoutExpired:
            logger.error("❌ FFmpeg encoding timed out")
            raise
        except Exception as e:
            logger.error(f"❌ Composition failed: {e}")
            raise

    @staticmethod
    def _mp3_to_wav(mp3_file: str, wav_file: str) -> str:
        """Convert MP3 → WAV using FFmpeg"""
        cmd = [
            "ffmpeg",
            "-i", mp3_file,
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
            wav_file,
            "-y",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            raise RuntimeError(f"MP3→WAV conversion failed: {result.stderr[:200]}")

        logger.info(f"✅ Audio converted: {wav_file}")
        return wav_file
