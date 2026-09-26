"""FFmpeg Filter Graph construction for video composition."""

from __future__ import annotations

import functools
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional, Any
import json

# scene.id becomes an ffmpeg filtergraph LABEL ([scaled_<id>] etc, see build()
# below) -- unlike narration text it isn't wrapped in any quoting ffmpeg
# would otherwise parse, so a stray `]`/`;`/`:` in it would corrupt the graph
# outright rather than just mis-render. Scene ids are assigned by the
# storyboard generator, not typed freely, so this is a defensive floor, not
# the primary escaping concern (that's narration text -- see
# _get_caption_filter's textfile= rewrite).
_SAFE_SCENE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

import sys
# Imported by the package's REAL path. A sys.path.insert of <repo>/core/skills
# plus `from os_skills...` loads video_producer/types.py a SECOND time under a
# second module name, so the dataclasses here and the ones the rest of the
# codebase holds are different classes and isinstance() is False across the
# seam (2026-09-20 review).
from core.skills.os_skills.video_producer.types import Storyboard

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _drawtext_available() -> bool:
    """Some static ffmpeg builds (e.g. the johnvansickle build this repo's
    ``video`` extra bundles) link libfreetype/fontconfig for OTHER filters but
    still ship without ``drawtext`` compiled in -- burning captions in on such
    a build doesn't degrade quality, it fails the WHOLE encode ("Filter not
    found"). Checked once per process via ``ffmpeg -filters``, not assumed.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=10,
        )
        return "drawtext" in result.stdout
    except Exception:  # noqa: BLE001 -- absent/broken ffmpeg is handled elsewhere
        return False


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

        Input index convention (must match ``get_input_args()``): scene i's
        image is input ``2*i``, its audio is input ``2*i + 1``.

        Returns:
            FFmpeg filter_complex string ending in ``[outv][outa]`` labels,
            or None if invalid.
        """
        if not self.storyboard.scenes:
            return None

        filters = []
        video_labels: list[str] = []
        audio_labels: list[str] = []

        for i, scene in enumerate(self.storyboard.scenes):
            if not _SAFE_SCENE_ID.match(scene.id):
                raise ValueError(
                    f"unsafe scene id for ffmpeg filtergraph label: {scene.id!r}"
                )
            v_in, a_in = 2 * i, 2 * i + 1

            scaled = f"scaled_{scene.id}"
            filters.append(
                f"[{v_in}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
                f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[{scaled}]"
            )

            if scene.captions and scene.narration and _drawtext_available():
                caption_filter = self._get_caption_filter(scene.narration[:50], scene.id)
                capped = f"capped_{scene.id}"
                filters.append(f"[{scaled}]{caption_filter}[{capped}]")
                video_label = capped
            else:
                if scene.captions and scene.narration:
                    logger.warning(
                        f"Scene {scene.id}: captions requested but this ffmpeg "
                        "build has no 'drawtext' filter -- rendering without "
                        "burned-in captions."
                    )
                video_label = scaled

            out_label = f"vout_{scene.id}"
            filters.append(f"[{video_label}]format=yuv420p[{out_label}]")
            video_labels.append(out_label)
            audio_labels.append(f"{a_in}:a")

        concat_inputs = "".join(
            f"[{v}][{a}]" for v, a in zip(video_labels, audio_labels)
        )
        filters.append(
            f"{concat_inputs}concat=n={len(video_labels)}:v=1:a=1[outv][outa]"
        )

        return ";".join(filters)

    def get_input_args(self) -> list[str]:
        """Ordered ffmpeg ``-i``/``-loop`` input args matching ``build()``'s
        ``2*i`` (image) / ``2*i + 1`` (audio) index convention.

        A still image has no intrinsic duration, so it must be looped
        (``-loop 1``) and cut (``-t <seconds>``) to the scene's own
        ``duration_seconds`` (default 5.0) -- otherwise ffmpeg reads exactly
        one frame of it and the concat filter starves for video on that
        segment.
        """
        args: list[str] = []
        for scene in self.storyboard.scenes:
            slide_path = self.slides_dir / f"{scene.id}.png"
            audio_path = self.audio_dir / f"{scene.id}.mp3"
            duration = scene.duration_seconds or 5.0
            args += ["-loop", "1", "-t", str(duration), "-i", str(slide_path)]
            args += ["-i", str(audio_path)]
        return args

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

    def _get_caption_filter(self, text: str, scene_id: str, font_size: int = 24) -> str:
        """Generate a drawtext filter for captions via ``textfile=``, not
        ``text=``.

        ``scene.narration`` is untrusted content (may come from an
        LLM-generated storyboard). ffmpeg's filtergraph description
        language treats `:`, `,`, `[`, `]`, `;` and `\\` as syntax --
        putting narration text directly into ``text='...'`` (escaping only
        quotes, as this used to) lets narration text like "Hello, friend:
        watch this" break out of the value and be re-parsed as additional
        filter options or a new filter/label. ``textfile=`` reads the raw
        file content with none of that re-parsing -- only the FILE PATH
        (which this code generates, not the caller) needs filtergraph
        escaping.
        """
        caption_dir = self.slides_dir.parent / "captions"
        caption_dir.mkdir(parents=True, exist_ok=True)
        caption_path = caption_dir / f"{scene_id}.txt"
        caption_path.write_text(text)

        escaped_path = str(caption_path).replace("\\", "\\\\").replace(":", "\\:")
        return (
            f"drawtext=textfile='{escaped_path}':fontsize={font_size}:"
            f"fontcolor=white:x=(w-text_w)/2:y=h-50"
        )

    def _get_concat_filter(self, scene_ids: list[str], segment_count: int) -> str:
        """Generate concat filter for joining scenes."""
        # concat=n=<num_segments>:v=1:a=1
        return f"concat=n={segment_count}:v=1:a=1"
