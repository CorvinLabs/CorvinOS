#!/usr/bin/env python3
"""
SVG Diagram Generator (Simplified)
Creates animated SVG diagrams using FFmpeg (no drawtext)
"""

import os
import sys
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from quality_validator import validate_image_has_content, validate_video_asset, QualityValidationError

logger = logging.getLogger(__name__)


class SVGDiagramGenerator:
    """Generate animated SVG diagrams"""

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize SVG generator

        Args:
            config: Configuration dict
        """
        self.config = config or {}
        self.output_dir = self.config.get("output_dir", "/tmp/corvinos_svgs")
        self.resolution = self.config.get("resolution", (1920, 1080))
        self.fps = self.config.get("fps", 25)

        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"SVG Generator initialized: {self.output_dir}")

    def _render_diagram_frame(self, name: str, title: str, items: List[str], accent: str) -> str:
        """Render a single diagram frame with real drawn content (PIL).

        Root cause fixed here: the previous implementation filled the
        frame with a flat `color=c=...` field plus a fade in/out — visible
        motion, zero visual information. This draws an actual labeled
        diagram (title bar + boxes) so the frame carries real content and
        clears the bitrate/brightness quality checks honestly instead of
        by accident.
        """
        from PIL import Image, ImageDraw, ImageFont

        width, height = self.resolution
        bg = "#0f1320"
        png_file = os.path.join(self.output_dir, f"{name}_frame.png")

        try:
            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(height * 0.06))
            font_text = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(height * 0.035))
        except OSError as e:
            raise RuntimeError(f"Required font not available: {e}") from e

        img = Image.new("RGB", (width, height), color=bg)
        draw = ImageDraw.Draw(img)

        bar_h = int(height * 0.14)
        draw.rectangle([(0, 0), (width, bar_h)], fill=accent)
        draw.text((int(width * 0.05), int(bar_h * 0.2)), title, fill="white", font=font_title)

        # Draw each item as a distinct labeled box so the diagram carries
        # real, per-item visual structure (not just a title on a color field).
        n = max(len(items), 1)
        box_h = int((height - bar_h * 2) / n * 0.8)
        gap = int((height - bar_h * 2) / n * 0.2)
        y = bar_h + gap
        for item in items:
            draw.rectangle(
                [(int(width * 0.08), y), (int(width * 0.6), y + box_h)],
                outline=accent, width=4
            )
            draw.text((int(width * 0.10), y + box_h // 3), item, fill="white", font=font_text)
            y += box_h + gap

        footer_h = int(height * 0.07)
        draw.rectangle([(0, height - footer_h), (width, height)], fill=accent)

        img.save(png_file)
        validate_image_has_content(png_file)
        return png_file

    def _create_simple_video(
        self, name: str, title: str, items: List[str],
        duration_seconds: int = 8, color: str = "#0f1320"
    ) -> str:
        """
        Create a diagram video: render one real content frame, hold it,
        fade in/out.

        Args:
            name: Diagram name
            title: Diagram title text
            items: Labeled items to draw as boxes
            duration_seconds: Duration
            color: Accent color code

        Returns:
            Path to video file
        """
        output_video = os.path.join(self.output_dir, f"{name}.mp4")

        frame_png = self._render_diagram_frame(name, title, items, color)

        cmd = [
            "ffmpeg",
            "-loop", "1",
            "-i", frame_png,
            "-t", str(duration_seconds),
            "-vf", (
                f"scale={self.resolution[0]}:{self.resolution[1]},"
                f"fade=t=in:st=0:d=1,"
                f"fade=t=out:st={duration_seconds-1}:d=1"
            ),
            "-c:v", "libx264",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-y",
            output_video
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr[:200]}")
                raise RuntimeError(f"{name} generation failed")

            # Fail-closed: raises QualityValidationError on empty/flat output.
            validate_video_asset(output_video, require_audio=False)

            logger.info(f"✓ {name}: {output_video} (content verified)")
            return output_video
        except Exception as e:
            logger.error(f"{name} creation failed: {e}")
            raise

    def create_four_pillars_diagram(self, duration_seconds: int = 8) -> str:
        """Create Four Pillars of CorvinOS diagram"""
        return self._create_simple_video(
            "four_pillars", "Four Core Pillars",
            ["Voice Integration", "Encryption", "Deduplication", "A2A Connectivity"],
            duration_seconds, "#00D9FF"
        )

    def create_architecture_diagram(self, duration_seconds: int = 8) -> str:
        """Create Architecture Stack diagram"""
        return self._create_simple_video(
            "architecture", "Layer-Based Architecture",
            ["Data Layer", "Worker Engine", "Orchestration", "API Layer"],
            duration_seconds, "#00F077"
        )

    def create_dataflow_diagram(self, duration_seconds: int = 8) -> str:
        """Create Data Flow diagram"""
        return self._create_simple_video(
            "dataflow", "Data Flow",
            ["Input Classification", "Encryption Gate", "Worker Dispatch", "Audit Chain"],
            duration_seconds, "#FF006E"
        )

    def create_timeline_diagram(self, duration_seconds: int = 8) -> str:
        """Create Timeline/Roadmap diagram"""
        return self._create_simple_video(
            "timeline", "Vision",
            ["AI Integration", "Marketplace", "Decentralized", "Self-Healing"],
            duration_seconds, "#FF6B35"
        )

    def execute(self) -> Dict[str, str]:
        """
        Execute all diagram generation.

        Diagram content is config-driven via `config["diagrams"]` (a list
        of {name, title, items, color, duration_seconds}), so this
        generator is reusable for any subject, not hardcoded to the
        CorvinOS overview. Falls back to the four CorvinOS default
        diagrams when no config is supplied.

        Returns:
            Dict of diagram names to video files
        """
        logger.info("Executing SVG generator...")
        start_time = datetime.now()

        diagram_specs = self.config.get("diagrams")

        try:
            if diagram_specs:
                diagrams = {}
                for spec in diagram_specs:
                    diagrams[spec["name"]] = self._create_simple_video(
                        spec["name"], spec["title"], spec["items"],
                        spec.get("duration_seconds", 8), spec.get("color", "#00D9FF")
                    )
            else:
                diagrams = {
                    "four_pillars": self.create_four_pillars_diagram(),
                    "architecture": self.create_architecture_diagram(),
                    "dataflow": self.create_dataflow_diagram(),
                    "timeline": self.create_timeline_diagram()
                }

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"✓ SVG pipeline complete ({duration:.1f}s): {len(diagrams)} diagrams")

            return diagrams

        except Exception as e:
            logger.error(f"SVG generation failed: {e}")
            raise


def generate_all_diagrams(
    output_dir: Optional[str] = None,
    config: Optional[Dict] = None
) -> Dict[str, str]:
    """
    Generate all required diagrams

    Args:
        output_dir: Output directory
        config: Configuration dict

    Returns:
        Dict of diagram videos
    """
    cfg = config or {}
    if output_dir:
        cfg["output_dir"] = output_dir

    generator = SVGDiagramGenerator(config=cfg)
    return generator.execute()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        diagrams = generate_all_diagrams()
        print(f"Generated {len(diagrams)} diagrams:")
        for name, path in diagrams.items():
            print(f"  {name}: {path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
