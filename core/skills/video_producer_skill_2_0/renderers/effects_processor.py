"""Effects Processor — Phase 4 (Transitions, Color Grading, Animations)

Post-processing pipeline for frame sequences: transitions (fade, slide, zoom),
color grading (hue, saturation, brightness), animations (fade-in/out, pan, scale).
"""

import logging
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance
from dataclasses import dataclass
import math

logger = logging.getLogger(__name__)


@dataclass
class Effect:
    """Effect definition"""
    type: str  # transition, color_grade, animation
    name: str  # fade, slide, zoom, hue_shift, saturate, brightness, fade_in, pan, scale
    start_frame: int
    end_frame: int
    params: Dict = None  # effect-specific params

    def __post_init__(self):
        if self.params is None:
            self.params = {}


class EffectsProcessor:
    """Post-processing effects for video frames"""

    def __init__(self, output_dir: str = "/tmp/video_frames"):
        self.output_dir = output_dir
        self.effects: List[Effect] = []
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def add_effect(self, effect: Effect) -> None:
        """Add effect to processing queue"""
        self.effects.append(effect)
        logger.info(f"✅ Effect added: {effect.name} (frames {effect.start_frame}-{effect.end_frame})")

    def process_frames(self, frame_paths: List[str], output_paths: List[str] = None) -> List[str]:
        """Apply all effects to frame sequence

        Args:
            frame_paths: List of input PNG frame paths
            output_paths: Optional custom output paths (auto-generated if None)

        Returns:
            List of output frame paths
        """
        if not output_paths:
            output_paths = [
                str(Path(self.output_dir) / f"effect_frame_{i:04d}.png")
                for i in range(len(frame_paths))
            ]

        try:
            # Load all frames into memory (for multi-frame effects)
            frames = [Image.open(path) for path in frame_paths]

            # Apply effects in order
            for effect in self.effects:
                frames = self._apply_effect(frames, effect)

            # Write output frames
            for i, (frame, output_path) in enumerate(zip(frames, output_paths)):
                frame.save(output_path, "PNG")
                logger.debug(f"Saved effect frame: {output_path}")

            logger.info(f"✅ Effects processed: {len(frames)} frames")
            return output_paths

        except Exception as e:
            logger.error(f"❌ Effect processing failed: {e}")
            raise

    def _apply_effect(self, frames: List[Image.Image], effect: Effect) -> List[Image.Image]:
        """Apply single effect to frame sequence"""

        # Transition effects (span multiple frames)
        if effect.type == "transition":
            if effect.name == "fade":
                return self._transition_fade(frames, effect)
            elif effect.name == "slide":
                return self._transition_slide(frames, effect)
            elif effect.name == "zoom":
                return self._transition_zoom(frames, effect)

        # Color grading (per-frame)
        elif effect.type == "color_grade":
            return [self._apply_color_grade(f, effect) for f in frames]

        # Animation (per-frame, incremental effect)
        elif effect.type == "animation":
            return self._apply_animation(frames, effect)

        else:
            logger.warning(f"Unknown effect type: {effect.type}")
            return frames

    def _transition_fade(self, frames: List[Image.Image], effect: Effect) -> List[Image.Image]:
        """Fade transition (0→1 alpha over duration)"""

        start = effect.start_frame
        end = effect.end_frame
        direction = effect.params.get("direction", "in")  # in or out

        output_frames = frames.copy()

        for i in range(start, min(end + 1, len(frames))):
            progress = (i - start) / max(1, (end - start))
            if direction == "out":
                progress = 1.0 - progress

            # Apply opacity
            frame = output_frames[i].convert("RGBA")
            alpha = frame.split()[3]
            alpha = ImageEnhance.Brightness(alpha).enhance(progress)
            frame.putalpha(alpha)
            output_frames[i] = frame.convert("RGB")

        return output_frames

    def _transition_slide(self, frames: List[Image.Image], effect: Effect) -> List[Image.Image]:
        """Slide transition (move across screen)"""

        start = effect.start_frame
        end = effect.end_frame
        direction = effect.params.get("direction", "left")  # left, right, up, down

        output_frames = frames.copy()
        frame_width = frames[0].width
        frame_height = frames[0].height

        for i in range(start, min(end + 1, len(frames))):
            progress = (i - start) / max(1, (end - start))

            # Calculate offset
            if direction == "left":
                offset_x = int(frame_width * progress)
                offset_y = 0
            elif direction == "right":
                offset_x = int(-frame_width * progress)
                offset_y = 0
            elif direction == "up":
                offset_x = 0
                offset_y = int(frame_height * progress)
            elif direction == "down":
                offset_x = 0
                offset_y = int(-frame_height * progress)

            # Create new frame with offset
            new_frame = Image.new("RGB", (frame_width, frame_height), (0, 0, 0))
            new_frame.paste(output_frames[i], (offset_x, offset_y))
            output_frames[i] = new_frame

        return output_frames

    def _transition_zoom(self, frames: List[Image.Image], effect: Effect) -> List[Image.Image]:
        """Zoom transition (scale in/out)"""

        start = effect.start_frame
        end = effect.end_frame
        direction = effect.params.get("direction", "in")  # in or out

        output_frames = frames.copy()
        frame_width = frames[0].width
        frame_height = frames[0].height

        for i in range(start, min(end + 1, len(frames))):
            progress = (i - start) / max(1, (end - start))

            # Calculate scale factor
            if direction == "in":
                scale = 1.0 + progress  # 1.0 → 2.0
            else:
                scale = 1.0 + (1.0 - progress)  # 1.0 → 2.0 reversed

            # Resize and center
            new_width = int(frame_width / scale)
            new_height = int(frame_height / scale)
            cropped = output_frames[i].resize((new_width, new_height), Image.LANCZOS)

            # Center crop on new frame
            new_frame = Image.new("RGB", (frame_width, frame_height), (0, 0, 0))
            left = (frame_width - new_width) // 2
            top = (frame_height - new_height) // 2
            new_frame.paste(cropped, (left, top))
            output_frames[i] = new_frame

        return output_frames

    def _apply_color_grade(self, frame: Image.Image, effect: Effect) -> Image.Image:
        """Apply color grading: hue shift, saturation, brightness"""

        output = frame.copy().convert("RGB")

        if effect.name == "hue_shift":
            shift = effect.params.get("shift", 0)  # degrees (0-360)
            # Convert to HSV, shift hue, convert back
            output = self._shift_hue(output, shift)

        elif effect.name == "saturate":
            factor = effect.params.get("factor", 1.0)  # 0=grayscale, 1=normal, 2=double
            output = ImageEnhance.Color(output).enhance(factor)

        elif effect.name == "brightness":
            factor = effect.params.get("factor", 1.0)  # 0=black, 1=normal, 2=double
            output = ImageEnhance.Brightness(output).enhance(factor)

        elif effect.name == "contrast":
            factor = effect.params.get("factor", 1.0)
            output = ImageEnhance.Contrast(output).enhance(factor)

        return output

    def _apply_animation(self, frames: List[Image.Image], effect: Effect) -> List[Image.Image]:
        """Apply animation effect: fade_in, pan, scale, etc."""

        start = effect.start_frame
        end = effect.end_frame
        output_frames = frames.copy()
        frame_width = frames[0].width
        frame_height = frames[0].height

        if effect.name == "fade_in":
            # Progressively increase opacity from 0→1
            for i in range(start, min(end + 1, len(frames))):
                progress = (i - start) / max(1, (end - start))
                frame = output_frames[i].convert("RGBA")
                alpha = frame.split()[3]
                alpha = ImageEnhance.Brightness(alpha).enhance(progress)
                frame.putalpha(alpha)
                output_frames[i] = frame.convert("RGB")

        elif effect.name == "fade_out":
            # Progressively decrease opacity from 1→0
            for i in range(start, min(end + 1, len(frames))):
                progress = 1.0 - (i - start) / max(1, (end - start))
                frame = output_frames[i].convert("RGBA")
                alpha = frame.split()[3]
                alpha = ImageEnhance.Brightness(alpha).enhance(progress)
                frame.putalpha(alpha)
                output_frames[i] = frame.convert("RGB")

        elif effect.name == "pan":
            # Pan across frame (e.g., left-to-right)
            pan_x = effect.params.get("pan_x", frame_width // 4)
            for i in range(start, min(end + 1, len(frames))):
                progress = (i - start) / max(1, (end - start))
                offset = int(pan_x * progress)
                new_frame = Image.new("RGB", (frame_width, frame_height), (0, 0, 0))
                new_frame.paste(output_frames[i], (offset, 0))
                output_frames[i] = new_frame

        elif effect.name == "scale":
            # Scale up/down
            start_scale = effect.params.get("start_scale", 1.0)
            end_scale = effect.params.get("end_scale", 1.2)
            for i in range(start, min(end + 1, len(frames))):
                progress = (i - start) / max(1, (end - start))
                scale = start_scale + (end_scale - start_scale) * progress
                new_width = int(frame_width * scale)
                new_height = int(frame_height * scale)
                scaled = output_frames[i].resize((new_width, new_height), Image.LANCZOS)
                new_frame = Image.new("RGB", (frame_width, frame_height), (0, 0, 0))
                left = (frame_width - new_width) // 2
                top = (frame_height - new_height) // 2
                new_frame.paste(scaled, (left, top))
                output_frames[i] = new_frame

        return output_frames

    def _shift_hue(self, image: Image.Image, shift_degrees: float) -> Image.Image:
        """Shift hue by amount (0-360 degrees)"""
        # Convert RGB → HSV, shift H, convert back to RGB
        import colorsys

        image_hsv = Image.new("HSV", image.size)
        pixels = image.load()
        hsv_pixels = image_hsv.load()

        for y in range(image.height):
            for x in range(image.width):
                r, g, b = pixels[x, y][:3]
                h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                h = (h + shift_degrees / 360) % 1.0
                r, g, b = colorsys.hsv_to_rgb(h, s, v)
                hsv_pixels[x, y] = (int(r * 255), int(g * 255), int(b * 255))

        return image_hsv.convert("RGB")


# Example usage
def example_effects_pipeline():
    """Example: apply fade transition + color grading"""

    from PIL import Image

    # Create sample frames
    frames = [
        Image.new("RGB", (1920, 1080), color=(50, 50, 50)) for _ in range(30)
    ]
    frame_paths = [f"/tmp/frame_{i:04d}.png" for i in range(len(frames))]
    for path, frame in zip(frame_paths, frames):
        frame.save(path)

    # Create processor
    processor = EffectsProcessor()

    # Add effects
    processor.add_effect(
        Effect("transition", "fade", start_frame=0, end_frame=10, params={"direction": "in"})
    )
    processor.add_effect(
        Effect("color_grade", "saturate", start_frame=0, end_frame=30, params={"factor": 1.5})
    )
    processor.add_effect(
        Effect("animation", "pan", start_frame=5, end_frame=15, params={"pan_x": 100})
    )

    # Process
    return processor.process_frames(frame_paths)


__all__ = ["EffectsProcessor", "Effect"]
