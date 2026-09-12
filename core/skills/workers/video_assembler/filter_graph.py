"""FFmpeg Filter Graph construction for video composition."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Any
import json

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from os_skills.video_producer.types import Storyboard


class FilterGraph:
    """Builder for FFmpeg filter_complex strings."""

    def __init__(
        self,
        storyboard: Storyboard,
        slides_dir: Path,
        audio_dir: Path,
        screenshots_dir: Optional[Path] = None,
    ):
        """Initialize filter graph builder."""
        self.storyboard = storyboard
        self.slides_dir = Path(slides_dir)
        self.audio_dir = Path(audio_dir)
        self.screenshots_dir = Path(screenshots_dir) if screenshots_dir else None
        self.filter_parts = []

    async def build(self) -> Optional[str]:
        """
        Build FFmpeg filter_complex string.

        Composition logic:
        1. For each scene: slide + audio overlay
        2. Slide displays for audio duration (or default 5s)
        3. Add captions if scene.captions
        4. Concatenate all scenes into final video

        Returns:
            FFmpeg filter_complex string, or None if invalid
        """
        if not self.storyboard.scenes:
            return None

        # Stub implementation: return minimal filter graph
        # Real: build concat filter, overlay captions, etc.

        filters = []
        input_count = 0

        for scene in self.storyboard.scenes:
            # Input 0: slide image
            # Input 1: audio
            # Output: scene_1, scene_2, etc.

            # Scale slide to 1920x1080
            slide_filter = f"[{input_count}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[scaled_{scene.id}]"
            filters.append(slide_filter)

            # Overlay captions if needed
            if scene.captions and scene.narration:
                # Add text overlay (stub)
                caption_text = scene.narration[:50]  # First 50 chars
                caption_filter = f"[scaled_{scene.id}]drawtext=text='{caption_text}':fontsize=24:fontcolor=white[with_caption_{scene.id}]"
                filters.append(caption_filter)
                last_output = f"with_caption_{scene.id}"
            else:
                last_output = f"scaled_{scene.id}"

            input_count += 1

        # Stub: just return a basic description
        # Real: return actual FFmpeg filter_complex string
        return "[0:v]scale=1920:1080[v];[v][1:a]concat=n=1:v=1:a=1[out]"

    def _validate_scenes(self) -> bool:
        """Validate that all scenes have corresponding files."""
        for scene in self.storyboard.scenes:
            slide_path = self.slides_dir / f"{scene.id}.png"
            audio_path = self.audio_dir / f"{scene.id}.mp3"

            if not slide_path.exists() or not audio_path.exists():
                return False

        return True

    def _get_scene_duration_ms(self, scene_id: str) -> float:
        """Get scene duration from audio file (stub)."""
        # Stub: return 5000ms (5 seconds)
        # Real: use ffprobe to get actual duration
        return 5000.0

    def _get_caption_filter(self, text: str, font_size: int = 24) -> str:
        """Generate drawtext filter for captions."""
        # Escape text for shell
        safe_text = text.replace("'", "\\'").replace('"', '\\"')
        return f"drawtext=text='{safe_text}':fontsize={font_size}:fontcolor=white:x=(w-text_w)/2:y=h-50"

    def _get_concat_filter(self, scene_ids: list[str], segment_count: int) -> str:
        """Generate concat filter for joining scenes."""
        # concat=n=<num_segments>:v=1:a=1
        return f"concat=n={segment_count}:v=1:a=1"
