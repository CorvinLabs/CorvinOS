"""Screencast Renderer — Phase 4 (Screen Capture + Overlays)

Captures screen output with optional overlays (webcam, annotations, cursor highlight).
Uses FFmpeg for screen recording and frame composition.
"""

import logging
import subprocess
import tempfile
import os
from pathlib import Path
from typing import List, Optional, Dict
from dataclasses import dataclass
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


@dataclass
class ScreencastConfig:
    """Screencast recording configuration"""
    framerate: int = 30  # fps
    resolution: str = "1920x1080"  # WxH
    quality: str = "high"  # low, medium, high
    audio_source: Optional[str] = None  # alsa_input.xxx or None
    cursor_highlight: bool = True
    show_keystrokes: bool = False
    show_mouse_clicks: bool = False


@dataclass
class Overlay:
    """Overlay configuration"""
    type: str  # webcam, annotation, timestamp, title
    position: str  # top-left, top-right, bottom-left, bottom-right, center
    source: Optional[str] = None  # file path or device
    text: Optional[str] = None  # for annotation/title/timestamp
    opacity: float = 1.0  # 0-1
    duration_frames: Optional[int] = None  # None = full duration


class ScreencastRenderer:
    """Screen recording with overlays and effects"""

    def __init__(
        self,
        output_dir: str = "/tmp/video_frames",
        config: Optional[ScreencastConfig] = None
    ):
        self.output_dir = output_dir
        self.config = config or ScreencastConfig()
        self.overlays: List[Overlay] = []
        self.temp_dir = tempfile.mkdtemp()
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def add_overlay(self, overlay: Overlay) -> None:
        """Add overlay to recording"""
        self.overlays.append(overlay)
        logger.info(f"✅ Overlay added: {overlay.type} at {overlay.position}")

    def capture_screen(self, duration_seconds: float, output_path: str) -> str:
        """Capture screen to video file

        Args:
            duration_seconds: Recording duration
            output_path: Output MP4 file path

        Returns:
            Path to recorded MP4
        """
        try:
            logger.info(f"🎬 Starting screen capture: {duration_seconds}s @ {self.config.framerate}fps")

            # Determine display (Linux/X11)
            display = os.environ.get("DISPLAY", ":0")

            # FFmpeg command for screen capture
            ffmpeg_cmd = [
                "ffmpeg",
                "-video_size", self.config.resolution,
                "-framerate", str(self.config.framerate),
                "-t", str(duration_seconds),
                "-f", "x11grab",
                "-i", display,
            ]

            # Add audio if configured
            if self.config.audio_source:
                ffmpeg_cmd.extend([
                    "-f", "pulse",
                    "-i", self.config.audio_source,
                ])

            # Bitrate based on quality
            bitrate_map = {"low": "2000k", "medium": "5000k", "high": "10000k"}
            ffmpeg_cmd.extend([
                "-c:v", "libx264",
                "-preset", "fast",
                "-b:v", bitrate_map.get(self.config.quality, "5000k"),
                "-c:a", "aac",
                output_path,
            ])

            # Run FFmpeg
            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                timeout=duration_seconds + 30,
            )

            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg capture failed: {result.stderr.decode()}")

            logger.info(f"✅ Screen capture completed: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Screen capture failed: {e}")
            raise

    def capture_and_compose(
        self,
        duration_seconds: float,
        spec: Dict,
        scene_idx: int = 0
    ) -> str:
        """Capture screen and apply overlays (compose frame-by-frame)

        Args:
            duration_seconds: Recording duration
            spec: Video spec (for annotations, text overlays)
            scene_idx: Scene index

        Returns:
            Path to composited MP4
        """
        try:
            # Step 1: Capture raw screen
            temp_capture = Path(self.temp_dir) / f"capture_raw_{scene_idx}.mp4"
            self.capture_screen(duration_seconds, str(temp_capture))

            # Step 2: Extract frames from captured video
            frames_dir = Path(self.temp_dir) / f"screencast_frames_{scene_idx}"
            frames_dir.mkdir(exist_ok=True)
            self._extract_frames(temp_capture, frames_dir, scene_idx)

            # Step 3: Apply overlays to frames
            composited_frames = self._apply_overlays_to_frames(
                list(frames_dir.glob("*.png")),
                spec,
                scene_idx
            )

            # Step 4: Compose frames back to MP4
            output_path = Path(self.output_dir) / f"screencast_scene_{scene_idx}.mp4"
            self._compose_frames_to_mp4(composited_frames, output_path, scene_idx)

            logger.info(f"✅ Screencast with overlays completed: {output_path}")
            return str(output_path)

        except Exception as e:
            logger.error(f"❌ Screencast composition failed: {e}")
            raise

    def _extract_frames(self, video_path: Path, output_dir: Path, scene_idx: int) -> None:
        """Extract frames from MP4 using FFmpeg"""

        output_pattern = str(output_dir / f"frame_%04d.png")

        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vf", f"fps={self.config.framerate}",
            output_pattern,
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"Frame extraction failed: {result.stderr.decode()}")

        frame_count = len(list(output_dir.glob("*.png")))
        logger.info(f"✅ Extracted {frame_count} frames from screencast")

    def _apply_overlays_to_frames(
        self,
        frame_paths: List[Path],
        spec: Dict,
        scene_idx: int
    ) -> List[str]:
        """Apply overlays to each frame"""

        output_frames = []
        output_dir = Path(self.temp_dir) / f"composited_{scene_idx}"
        output_dir.mkdir(exist_ok=True)

        for frame_idx, frame_path in enumerate(frame_paths):
            frame = Image.open(frame_path).convert("RGB")

            # Apply each overlay
            for overlay in self.overlays:
                frame = self._apply_single_overlay(frame, overlay, frame_idx)

            # Add cursor highlight if enabled
            if self.config.cursor_highlight:
                frame = self._draw_cursor_highlight(frame, frame_idx)

            # Add keystroke indicator if enabled
            if self.config.show_keystrokes:
                frame = self._draw_keystroke_indicator(frame, spec.get("keystrokes", []))

            # Save composited frame
            output_path = output_dir / f"composited_{frame_idx:04d}.png"
            frame.save(output_path)
            output_frames.append(str(output_path))

        logger.info(f"✅ Applied overlays to {len(output_frames)} frames")
        return output_frames

    def _apply_single_overlay(
        self,
        frame: Image.Image,
        overlay: Overlay,
        frame_idx: int
    ) -> Image.Image:
        """Apply single overlay to frame"""

        output = frame.copy()

        if overlay.type == "annotation":
            # Draw text annotation
            output = self._draw_text_overlay(output, overlay, frame_idx)

        elif overlay.type == "timestamp":
            # Draw timestamp (frame counter or time)
            output = self._draw_timestamp(output, overlay, frame_idx)

        elif overlay.type == "title":
            # Draw title/subtitle
            output = self._draw_title(output, overlay)

        elif overlay.type == "webcam":
            # Composite webcam feed (if available)
            output = self._composite_webcam(output, overlay, frame_idx)

        return output

    def _draw_text_overlay(
        self,
        frame: Image.Image,
        overlay: Overlay,
        frame_idx: int
    ) -> Image.Image:
        """Draw text annotation on frame"""

        draw = ImageDraw.Draw(frame)
        text = overlay.text or f"Frame {frame_idx}"

        # Position calculation
        font_size = 32
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        except:
            font = ImageFont.load_default()

        # Get text size
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Position based on overlay.position
        margin = 20
        if overlay.position == "top-left":
            x, y = margin, margin
        elif overlay.position == "top-right":
            x, y = frame.width - text_width - margin, margin
        elif overlay.position == "bottom-left":
            x, y = margin, frame.height - text_height - margin
        elif overlay.position == "bottom-right":
            x, y = frame.width - text_width - margin, frame.height - text_height - margin
        elif overlay.position == "center":
            x, y = (frame.width - text_width) // 2, (frame.height - text_height) // 2
        else:
            x, y = margin, margin

        # Draw with alpha
        color = (255, 255, 255) if overlay.opacity > 0.7 else (200, 200, 200)
        draw.text((x, y), text, font=font, fill=color)

        return frame

    def _draw_timestamp(
        self,
        frame: Image.Image,
        overlay: Overlay,
        frame_idx: int
    ) -> Image.Image:
        """Draw timestamp on frame"""

        seconds = frame_idx / self.config.framerate
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        timestamp = f"{minutes:02d}:{secs:02d}"

        overlay_copy = Overlay(
            type="annotation",
            position=overlay.position or "top-left",
            text=timestamp,
            opacity=overlay.opacity
        )
        return self._draw_text_overlay(frame, overlay_copy, frame_idx)

    def _draw_title(self, frame: Image.Image, overlay: Overlay) -> Image.Image:
        """Draw title/subtitle"""

        draw = ImageDraw.Draw(frame)
        text = overlay.text or "Screencast"

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        except:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (frame.width - text_width) // 2
        y = frame.height - 100

        draw.text((x, y), text, font=font, fill=(255, 255, 255))
        return frame

    def _draw_cursor_highlight(self, frame: Image.Image, frame_idx: int) -> Image.Image:
        """Draw cursor highlight circle (visual indicator)"""

        draw = ImageDraw.Draw(frame)
        # Simple circle at position (could integrate actual mouse position from spec)
        cursor_x, cursor_y = 400 + (frame_idx % 10) * 50, 300
        radius = 20
        draw.ellipse(
            [(cursor_x - radius, cursor_y - radius), (cursor_x + radius, cursor_y + radius)],
            outline=(255, 100, 100),
            width=3
        )
        return frame

    def _draw_keystroke_indicator(self, frame: Image.Image, keystrokes: List[Dict]) -> Image.Image:
        """Draw keystroke indicator"""

        draw = ImageDraw.Draw(frame)

        if keystrokes:
            last_keystroke = keystrokes[-1].get("key", "?")
            text = f"Key: {last_keystroke}"
            draw.text((frame.width - 200, 50), text, font=ImageFont.load_default(), fill=(255, 255, 0))

        return frame

    def _composite_webcam(
        self,
        frame: Image.Image,
        overlay: Overlay,
        frame_idx: int
    ) -> Image.Image:
        """Composite webcam feed into frame (placeholder)"""

        # This would require a webcam image; for now, draw a placeholder rectangle
        draw = ImageDraw.Draw(frame)

        # Determine position and size
        webcam_width, webcam_height = 320, 240
        margin = 20

        if overlay.position == "bottom-right":
            x1 = frame.width - webcam_width - margin
            y1 = frame.height - webcam_height - margin
        else:
            x1 = margin
            y1 = margin

        x2, y2 = x1 + webcam_width, y1 + webcam_height

        # Draw placeholder box
        draw.rectangle([x1, y1, x2, y2], outline=(0, 255, 0), width=3)
        draw.text((x1 + 10, y1 + 10), "[Webcam]", font=ImageFont.load_default(), fill=(0, 255, 0))

        return frame

    def _compose_frames_to_mp4(self, frame_paths: List[str], output_path: Path, scene_idx: int) -> None:
        """Compose frame sequence back to MP4"""

        input_pattern = frame_paths[0].replace(frame_paths[0].split("/")[-1], "*.png")

        cmd = [
            "ffmpeg",
            "-framerate", str(self.config.framerate),
            "-i", input_pattern,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"MP4 composition failed: {result.stderr.decode()}")

        logger.info(f"✅ Composed {len(frame_paths)} frames to {output_path}")

    def cleanup(self) -> None:
        """Clean up temporary files"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        logger.info(f"✅ Cleaned up temp directory: {self.temp_dir}")

    def __del__(self):
        """Cleanup on deletion"""
        self.cleanup()


__all__ = ["ScreencastRenderer", "ScreencastConfig", "Overlay"]
