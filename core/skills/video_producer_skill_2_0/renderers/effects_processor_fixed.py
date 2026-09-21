"""Effects Processor FIXED — Batching to prevent OOM

Process frames in batches instead of loading all at once.
"""

import logging
from typing import List, Dict, Optional
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Effect:
    """Effect definition"""
    type: str
    name: str
    start_frame: int
    end_frame: int
    params: Dict = None

    def __post_init__(self):
        if self.params is None:
            self.params = {}


class EffectsProcessor:
    """Effects processor with memory-safe batching"""

    BATCH_SIZE = 32  # Load 32 frames at a time (~200MB for 1920x1080 RGB)

    def __init__(self, output_dir: str = "/tmp/video_frames", batch_size: int = 32):
        self.output_dir = output_dir
        self.effects: List[Effect] = []
        self.batch_size = batch_size
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def add_effect(self, effect: Effect) -> None:
        """Add effect to processing queue"""
        self.effects.append(effect)
        logger.info(f"✅ Effect added: {effect.name} (frames {effect.start_frame}-{effect.end_frame})")

    def process_frames(self, frame_paths: List[str], output_paths: List[str] = None) -> List[str]:
        """Process frames in batches (memory-safe)

        Args:
            frame_paths: List of input frame paths
            output_paths: Optional custom output paths

        Returns:
            List of output frame paths
        """
        if not output_paths:
            output_paths = [
                str(Path(self.output_dir) / f"effect_frame_{i:04d}.png")
                for i in range(len(frame_paths))
            ]

        # Process in batches to avoid OOM
        for batch_start in range(0, len(frame_paths), self.batch_size):
            batch_end = min(batch_start + self.batch_size, len(frame_paths))
            batch_paths = frame_paths[batch_start:batch_end]
            batch_outputs = output_paths[batch_start:batch_end]

            logger.info(f"Processing batch {batch_start}-{batch_end} of {len(frame_paths)}")

            # Load batch
            frames = [Image.open(path).convert("RGB") for path in batch_paths]

            # Apply effects to batch
            for effect in self.effects:
                frames = self._apply_effect_to_batch(frames, effect, batch_start)

            # Write batch
            for frame, output_path in zip(frames, batch_outputs):
                frame.save(output_path, "PNG")
                logger.debug(f"Saved: {output_path}")

            # Explicitly free memory
            del frames

        logger.info(f"✅ Effects processed (batched): {len(frame_paths)} frames")
        return output_paths

    def _apply_effect_to_batch(self, frames: List[Image.Image], effect: Effect, batch_offset: int) -> List[Image.Image]:
        """Apply effect to frame batch with frame index offset"""

        output_frames = frames.copy()

        # Adjust frame indices for batch context
        for i, frame in enumerate(output_frames):
            global_frame_idx = batch_offset + i

            if global_frame_idx < effect.start_frame or global_frame_idx > effect.end_frame:
                continue  # Effect doesn't apply to this frame

            # Apply effect
            if effect.type == "transition":
                frame = self._apply_transition(frame, effect, global_frame_idx)
            elif effect.type == "color_grade":
                frame = self._apply_color_grade(frame, effect)
            elif effect.type == "animation":
                frame = self._apply_animation(frame, effect, global_frame_idx)

            output_frames[i] = frame

        return output_frames

    def _apply_transition(self, frame: Image.Image, effect: Effect, frame_idx: int) -> Image.Image:
        """Apply transition effect (fade, slide, zoom)"""
        progress = (frame_idx - effect.start_frame) / max(1, (effect.end_frame - effect.start_frame))

        if effect.name == "fade":
            direction = effect.params.get("direction", "in")
            if direction == "out":
                progress = 1.0 - progress
            frame = frame.convert("RGBA")
            alpha = frame.split()[3]
            alpha = ImageEnhance.Brightness(alpha).enhance(progress)
            frame.putalpha(alpha)
            return frame.convert("RGB")

        return frame

    def _apply_color_grade(self, frame: Image.Image, effect: Effect) -> Image.Image:
        """Apply color grading"""
        if effect.name == "saturate":
            factor = effect.params.get("factor", 1.0)
            return ImageEnhance.Color(frame).enhance(factor)
        elif effect.name == "brightness":
            factor = effect.params.get("factor", 1.0)
            return ImageEnhance.Brightness(frame).enhance(factor)
        return frame

    def _apply_animation(self, frame: Image.Image, effect: Effect, frame_idx: int) -> Image.Image:
        """Apply animation effect"""
        # Simplified: just return frame
        return frame


__all__ = ["EffectsProcessor", "Effect"]
