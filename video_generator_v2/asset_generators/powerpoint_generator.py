#!/usr/bin/env python3
"""
PowerPoint Generator
Converts PowerPoint slides to video frames with proper timing
"""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from quality_validator import validate_image_has_content, validate_video_asset, QualityValidationError

logger = logging.getLogger(__name__)


class PowerPointGenerator:
    """Convert PowerPoint presentations to video format"""

    def __init__(self, pptx_file: Optional[str] = None, config: Optional[Dict] = None):
        """
        Initialize PowerPoint generator

        Args:
            pptx_file: Path to PPTX file (or create default)
            config: Generator configuration
        """
        self.pptx_file = pptx_file
        self.config = config or {}
        self.output_dir = self.config.get("output_dir", "/tmp/corvinos_pptx")
        self.fps = self.config.get("fps", 25)
        self.resolution = self.config.get("resolution", (1920, 1080))

        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"PowerPoint Generator initialized: {self.output_dir}")

    def create_default_slides(self) -> str:
        """
        Create default CorvinOS presentation using LibreOffice

        Returns:
            Path to generated PPTX file
        """
        slides_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<presentation xmlns="http://schemas.openxmlformats.org/presentationml/2006/main">
    <p:sldIdLst>
        <p:sldId id="256" r:id="rId2"/>
    </p:sldIdLst>
</presentation>'''

        logger.info("Creating default CorvinOS presentation...")

        # For now, we'll use FFmpeg to create slides directly
        # A real implementation would use python-pptx (if available)
        pptx_path = os.path.join(self.output_dir, "corvinos_slides.pptx")

        # Create simple text-based slides for now
        # In production: Use python-pptx or LibreOffice conversion
        logger.info(f"Default PPTX would be at: {pptx_path}")

        return pptx_path

    def generate_slide_images(self) -> List[str]:
        """
        Convert PowerPoint slides to PNG images
        Uses LibreOffice or fallback to FFmpeg video generation

        Returns:
            List of generated PNG image paths
        """
        logger.info("Generating slide images...")

        if self.pptx_file and os.path.exists(self.pptx_file):
            # Try LibreOffice conversion
            return self._convert_with_libreoffice()
        else:
            # Create default slides with FFmpeg
            return self._create_default_slide_images()

    def _convert_with_libreoffice(self) -> List[str]:
        """
        Convert PPTX to PNG using LibreOffice

        Returns:
            List of PNG file paths
        """
        logger.info(f"Converting {self.pptx_file} with LibreOffice...")

        output_prefix = os.path.join(self.output_dir, "slide")

        cmd = [
            "libreoffice",
            "--headless",
            "--convert-to", "png",
            "--outdir", self.output_dir,
            self.pptx_file
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.returncode != 0:
                logger.warning(f"LibreOffice conversion failed: {result.stderr}")
                return self._create_default_slide_images()

            # Find generated PNG files
            pngs = sorted(Path(self.output_dir).glob("slide*.png"))
            logger.info(f"✓ Converted {len(pngs)} slides to PNG")

            return [str(p) for p in pngs]

        except FileNotFoundError:
            logger.warning("LibreOffice not found, using fallback...")
            return self._create_default_slide_images()
        except Exception as e:
            logger.warning(f"Conversion error: {e}, using fallback...")
            return self._create_default_slide_images()

    def _create_default_slide_images(self) -> List[str]:
        """
        Create default slide images with real rendered text content (PIL).

        Root cause fixed here: the previous implementation used an FFmpeg
        `color=c=...` lavfi filter with nothing drawn on top, producing a
        flat single-color PNG per slide. It "succeeded" (ffmpeg exit 0,
        file size >100 bytes) while carrying zero visual content. Text is
        now rendered directly onto the frame and validated for real
        brightness variance before being accepted.

        Returns:
            List of PNG file paths
        """
        logger.info("Creating default slide images with real text content (PIL)...")

        from PIL import Image, ImageDraw, ImageFont

        default_slides = [
            {"name": "intro", "color": "#FFD700", "title": "CorvinOS",
             "bullets": ["Enterprise Operating System", "Security. Control. Transparency."]},
            {"name": "pillars", "color": "#00D9FF", "title": "Four Core Pillars",
             "bullets": ["Voice Integration", "Encryption", "Deduplication", "A2A Connectivity"]},
            {"name": "architecture", "color": "#00F077", "title": "Layer-Based Architecture",
             "bullets": ["Data Layer", "Worker Engine", "Orchestration", "API Layer"]},
            {"name": "roadmap", "color": "#FF6B35", "title": "Vision",
             "bullets": ["AI Integration", "Marketplace", "Decentralized", "Self-Healing"]},
            {"name": "closing", "color": "#FFD700", "title": "CorvinOS",
             "bullets": ["Enterprise Operating System", "Thank you"]},
        ]
        # Topic content is config-driven so this generator is reusable for
        # any subject (e.g. ACP), not hardcoded to the CorvinOS overview.
        slides = self.config.get("slides") or default_slides
        footer_text = self.config.get("footer_text", "CorvinOS — Enterprise Operating System")

        bg = "#0f1320"
        width, height = self.resolution

        try:
            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(height * 0.06))
            font_text = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(height * 0.04))
        except OSError as e:
            raise RuntimeError(f"Required font not available: {e}") from e

        generated_pngs = []

        for slide_idx, slide in enumerate(slides, 1):
            png_file = os.path.join(self.output_dir, f"slide_{slide_idx:02d}_{slide['name']}.png")

            img = Image.new("RGB", (width, height), color=bg)
            draw = ImageDraw.Draw(img)

            bar_h = int(height * 0.14)
            draw.rectangle([(0, 0), (width, bar_h)], fill=slide["color"])
            draw.text((int(width * 0.05), int(bar_h * 0.2)), slide["title"],
                       fill="white", font=font_title)

            y = int(height * 0.28)
            line_h = int(height * 0.13)
            for bullet in slide["bullets"]:
                draw.text((int(width * 0.08), y), f"• {bullet}", fill="white", font=font_text)
                y += line_h

            footer_h = int(height * 0.07)
            draw.rectangle([(0, height - footer_h), (width, height)], fill=slide["color"])
            draw.text((int(width * 0.05), height - int(footer_h * 0.8)),
                       footer_text, fill="white", font=font_text)

            img.save(png_file)

            try:
                validate_image_has_content(png_file)
            except QualityValidationError:
                # Fail closed: a flat/empty slide must not enter the pipeline.
                raise

            file_size = os.path.getsize(png_file)
            generated_pngs.append(png_file)
            logger.info(f"✓ Generated slide {slide_idx}: {slide['name']} ({file_size} bytes, content verified)")

        logger.info(f"✓ Created {len(generated_pngs)} slide images with verified content")
        return generated_pngs

    def images_to_video(
        self,
        image_files: List[str],
        durations_per_slide: Optional[List[int]] = None,
        output_video: Optional[str] = None
    ) -> str:
        """
        Convert image sequence to video with per-slide duration

        Args:
            image_files: List of PNG files (in order)
            durations_per_slide: Duration in seconds for each slide
            output_video: Output MP4 file path

        Returns:
            Path to generated video file
        """
        if not image_files:
            raise ValueError("No image files provided")

        if not output_video:
            output_video = os.path.join(self.output_dir, "slides.mp4")

        # Default durations if not provided
        if not durations_per_slide:
            durations_per_slide = [5] * len(image_files)

        if len(durations_per_slide) != len(image_files):
            logger.warning(f"Duration list length ({len(durations_per_slide)}) != "
                          f"image count ({len(image_files)}), using default durations")
            durations_per_slide = [5] * len(image_files)

        logger.info(f"Converting {len(image_files)} images to video...")

        # Create concat file for FFmpeg (without quotes)
        concat_file = os.path.join(self.output_dir, "concat_images.txt")
        with open(concat_file, 'w') as f:
            for image_file, duration in zip(image_files, durations_per_slide):
                # Write without quotes to avoid "unsafe file name" errors
                f.write(f"file {image_file}\n")
                f.write(f"duration {duration}\n")

        Path(output_video).parent.mkdir(parents=True, exist_ok=True)

        # Use FFmpeg concat demuxer with image2 filter
        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",  # Allow arbitrary paths
            "-i", concat_file,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-fps_mode", "vfr",
            "-y",
            output_video
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                logger.error(f"Video conversion failed: {result.stderr}")
                raise RuntimeError(f"FFmpeg error: {result.stderr}")

            if not os.path.exists(output_video):
                raise RuntimeError("Output video file not created")

            file_size = os.path.getsize(output_video)
            logger.info(f"✓ Generated video: {output_video} ({file_size} bytes)")

            # Fail-closed content verification (bitrate + stream check).
            # This raises QualityValidationError if the video is a flat
            # black/color field instead of real content — it does NOT
            # catch-and-continue like the check this replaced.
            validate_video_asset(output_video, require_audio=False)
            logger.info("✓ Video content verified (real, non-empty)")

            return output_video

        except Exception as e:
            logger.error(f"Video generation failed: {e}")
            raise

    def execute(self) -> str:
        """
        Execute complete PowerPoint to video conversion

        Returns:
            Path to generated video file
        """
        logger.info("Executing PowerPoint generator...")
        start_time = datetime.now()

        try:
            # Step 1: Generate or convert slides to images
            slide_images = self.generate_slide_images()

            if not slide_images:
                raise RuntimeError("No slide images generated")

            logger.info(f"Generated {len(slide_images)} slide images")

            # Step 2: Convert to video
            output_video = os.path.join(self.output_dir, "corvinos_slides.mp4")
            durations = self.config.get("slide_durations_seconds")
            video = self.images_to_video(slide_images, durations_per_slide=durations, output_video=output_video)

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"✓ PowerPoint pipeline complete ({duration:.1f}s): {video}")

            return video

        except Exception as e:
            logger.error(f"PowerPoint generation failed: {e}")
            raise


def generate_presentation_video(
    pptx_file: Optional[str] = None,
    output_video: Optional[str] = None,
    config: Optional[Dict] = None
) -> str:
    """
    Generate presentation video from PowerPoint file

    Args:
        pptx_file: Path to PPTX file
        output_video: Output video path
        config: Configuration dict

    Returns:
        Path to generated video
    """
    generator = PowerPointGenerator(pptx_file=pptx_file, config=config or {})
    return generator.execute()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        video = generate_presentation_video()
        print(f"Generated: {video}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
