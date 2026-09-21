"""Frame Renderer (PIL) — Phase 2

Renders video spec scenes to PNG frames using PIL.
"""

import os
from PIL import Image, ImageDraw, ImageFont
import logging
from typing import List
from pathlib import Path

from ..llm_synthesis.spec_schema import VideoSpec, Scene, VisualElement

logger = logging.getLogger(__name__)


class FrameRenderer:
    """PIL-based frame renderer for VideoSpec"""

    def __init__(self, output_dir: str = "/tmp/video_frames", font_dir: str = None):
        self.output_dir = output_dir
        self.font_dir = font_dir or "/usr/share/fonts/truetype/dejavu"
        os.makedirs(output_dir, exist_ok=True)

    def _load_fonts(self):
        """Load TrueType fonts"""
        try:
            font_title = ImageFont.truetype(f"{self.font_dir}/DejaVuSans-Bold.ttf", 72)
            font_large = ImageFont.truetype(f"{self.font_dir}/DejaVuSans-Bold.ttf", 48)
            font_text = ImageFont.truetype(f"{self.font_dir}/DejaVuSans.ttf", 32)
            font_small = ImageFont.truetype(f"{self.font_dir}/DejaVuSans.ttf", 20)
        except:
            font_title = font_large = font_text = font_small = ImageFont.load_default()
        return font_title, font_large, font_text, font_small

    def render_scene(self, spec: VideoSpec, scene: Scene) -> List[str]:
        """Render single scene to PNG frames

        Returns:
            List of PNG file paths
        """
        fps = spec.video_metadata.fps
        duration_frames = scene.duration_seconds * fps
        width, height = spec.video_metadata.resolution

        file_paths = []
        fonts = self._load_fonts()

        logger.info(f"📍 Rendering scene '{scene.id}': {duration_frames} frames")

        for frame_idx in range(duration_frames):
            img = Image.new('RGB', (width, height), (15, 19, 32))
            draw = ImageDraw.Draw(img)

            # Draw elements
            for elem in scene.elements:
                if elem.type == "text":
                    draw.text((elem.x, elem.y), elem.text or "", fill=self._parse_color(elem.color), font=fonts[2])
                elif elem.type == "box":
                    draw.rectangle([elem.x, elem.y, elem.x + elem.width, elem.y + elem.height],
                                 outline=self._parse_color(elem.color), width=2)

            # Progress bar
            progress = frame_idx / duration_frames
            bar_w = int((width - 40) * progress)
            draw.rectangle([20, height - 30, 20 + bar_w, height - 10], fill=(52, 211, 153))

            # Save frame
            file_path = os.path.join(self.output_dir, f"frame_{frame_idx:06d}.png")
            img.save(file_path)
            file_paths.append(file_path)

        logger.info(f"✅ Scene '{scene.id}': {len(file_paths)} frames")
        return file_paths

    def render_spec(self, spec: VideoSpec) -> List[str]:
        """Render all scenes in spec

        Returns:
            List of all PNG file paths (frame_000000.png, frame_000001.png, ...)
        """
        all_frames = []
        frame_counter = 0

        for scene in spec.scenes:
            scene_frames = self.render_scene(spec, scene)
            # Rename frames to global sequence
            for i, src_path in enumerate(scene_frames):
                dst_path = os.path.join(self.output_dir, f"frame_{frame_counter:06d}.png")
                if src_path != dst_path:
                    os.rename(src_path, dst_path)
                all_frames.append(dst_path)
                frame_counter += 1

        logger.info(f"✅ All scenes rendered: {len(all_frames)} total frames")
        return all_frames

    @staticmethod
    def _parse_color(color_str: str):
        """Parse hex color string to RGB tuple"""
        if isinstance(color_str, str) and color_str.startswith("#"):
            return tuple(int(color_str[i:i+2], 16) for i in (1, 3, 5))
        return (255, 255, 255)
