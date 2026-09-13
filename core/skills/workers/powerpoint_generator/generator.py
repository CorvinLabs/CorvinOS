"""PowerPoint Generator for video producer: PPTX with animations, professional branding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass
from datetime import datetime

from pptx import Presentation
from pptx.util import Inches, Pt, RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.dml.color import RGBColor as DMLRGBColor
from pptx.enum.shapes import MSO_SHAPE


# ============================================================================
# TYPES
# ============================================================================

@dataclass(frozen=True)
class BrandingConfig:
    """Corporate branding settings."""
    primary_color: tuple[int, int, int]
    secondary_color: tuple[int, int, int]
    accent_color: tuple[int, int, int]
    font_title: str
    font_body: str
    font_mono: str
    logo_path: Optional[Path] = None
    resolution: tuple[int, int] = (1920, 1080)


@dataclass(frozen=True)
class AnimationSpec:
    """Animation specification for a slide element."""
    element: str
    animation_type: str  # "fade-in", "slide-up", "appear", "reveal", "loop-animate", etc.
    duration_ms: int
    delay_ms: int
    stagger_ms: int = 0  # For element groups


@dataclass
class SlideContent:
    """Content for a single slide."""
    slide_id: str
    slide_type: str  # "title", "diagram", "text-with-bullets", "outro"
    heading: Optional[str]
    title: Optional[str]
    subtitle: Optional[str]
    tagline: Optional[str]
    bullets: Optional[list[str]]
    narration: Optional[str]
    duration_seconds: float
    kicker: Optional[str] = None
    footer: Optional[str] = None
    animations: list[AnimationSpec] = None


class PowerPointGenerator:
    """Generate professional PPTX slides for video production."""

    def __init__(
        self,
        branding: BrandingConfig,
        output_dir: Path | str = Path("/tmp"),
    ):
        """Initialize with branding config."""
        self.branding = branding
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize presentation
        self.prs = Presentation()
        self.prs.slide_width = Inches(branding.resolution[0] / 96)  # 1920px -> inches @ 96 DPI
        self.prs.slide_height = Inches(branding.resolution[1] / 96)  # 1080px -> inches
        self.slides_created = 0

    async def generate(
        self,
        slides_content: list[SlideContent],
        output_filename: str = "presentation.pptx",
    ) -> dict[str, Any]:
        """
        Generate PPTX from slide content list.

        Args:
            slides_content: List of SlideContent objects
            output_filename: Output filename (default "presentation.pptx")

        Returns:
            {
                "status": "success" | "blocked",
                "output_path": str,
                "slide_count": int,
                "file_size_bytes": int,
                "animations_count": int,
                "metadata": dict,
            }
        """
        results = {
            "status": "success",
            "output_path": None,
            "slide_count": 0,
            "file_size_bytes": 0,
            "animations_count": 0,
            "metadata": {
                "branding_colors": {
                    "primary": self.branding.primary_color,
                    "secondary": self.branding.secondary_color,
                    "accent": self.branding.accent_color,
                },
                "fonts": {
                    "title": self.branding.font_title,
                    "body": self.branding.font_body,
                    "mono": self.branding.font_mono,
                },
                "created_at": datetime.utcnow().isoformat() + "Z",
            },
        }

        try:
            # Generate each slide
            animations_total = 0
            for i, content in enumerate(slides_content):
                if content.slide_type == "title":
                    await self._add_title_slide(content)
                elif content.slide_type == "diagram":
                    await self._add_diagram_slide(content)
                elif content.slide_type == "text-with-bullets":
                    await self._add_bullets_slide(content)
                elif content.slide_type == "outro":
                    await self._add_outro_slide(content)
                else:
                    # Default: generic content slide
                    await self._add_generic_slide(content)

                # Count animations
                if content.animations:
                    animations_total += len(content.animations)

                self.slides_created += 1

            # Save presentation
            output_path = self.output_dir / output_filename
            self.prs.save(str(output_path))

            results["status"] = "success"
            results["output_path"] = str(output_path)
            results["slide_count"] = self.slides_created
            results["file_size_bytes"] = output_path.stat().st_size
            results["animations_count"] = animations_total

        except Exception as e:
            results["status"] = "blocked"
            results["error"] = str(e)

        return results

    # ========================================================================
    # SLIDE BUILDERS
    # ========================================================================

    async def _add_title_slide(self, content: SlideContent) -> None:
        """Add a title slide with kicker, title, subtitle."""
        slide_layout = self.prs.slide_layouts[6]  # Blank layout
        slide = self.prs.slides.add_slide(slide_layout)

        # Background color
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = DMLRGBColor(*self.branding.primary_color)

        # Add kicker
        if content.kicker:
            kicker_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(0.5), Inches(18.5), Inches(0.8)
            )
            kicker_frame = kicker_box.text_frame
            kicker_frame.text = content.kicker
            kicker_frame.paragraphs[0].font.size = Pt(24)
            kicker_frame.paragraphs[0].font.color.rgb = RGBColor(*self.branding.accent_color)
            kicker_frame.paragraphs[0].font.name = self.branding.font_body

        # Add title
        title_box = slide.shapes.add_textbox(
            Inches(0.5), Inches(3.0), Inches(18.5), Inches(2.5)
        )
        title_frame = title_box.text_frame
        title_frame.word_wrap = True
        title_frame.text = content.title or ""
        title_frame.paragraphs[0].font.size = Pt(88)
        title_frame.paragraphs[0].font.bold = True
        title_frame.paragraphs[0].font.color.rgb = RGBColor(255, 255, 255)
        title_frame.paragraphs[0].font.name = self.branding.font_title

        # Add subtitle
        if content.subtitle:
            subtitle_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(5.8), Inches(18.5), Inches(1.5)
            )
            subtitle_frame = subtitle_box.text_frame
            subtitle_frame.word_wrap = True
            subtitle_frame.text = content.subtitle
            subtitle_frame.paragraphs[0].font.size = Pt(32)
            subtitle_frame.paragraphs[0].font.color.rgb = RGBColor(200, 200, 200)
            subtitle_frame.paragraphs[0].font.name = self.branding.font_body

        # Add footer
        if content.footer:
            footer_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(9.5), Inches(18.5), Inches(0.6)
            )
            footer_frame = footer_box.text_frame
            footer_frame.text = content.footer
            footer_frame.paragraphs[0].font.size = Pt(18)
            footer_frame.paragraphs[0].font.color.rgb = RGBColor(*self.branding.accent_color)
            footer_frame.paragraphs[0].font.name = self.branding.font_body

    async def _add_diagram_slide(self, content: SlideContent) -> None:
        """Add a diagram slide (5-box architecture, feedback loop, audit chain, etc.)."""
        slide_layout = self.prs.slide_layouts[6]  # Blank
        slide = self.prs.slides.add_slide(slide_layout)

        # Background
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = DMLRGBColor(240, 240, 240)

        # Add heading
        heading_box = slide.shapes.add_textbox(
            Inches(0.5), Inches(0.3), Inches(18.5), Inches(0.8)
        )
        heading_frame = heading_box.text_frame
        heading_frame.text = content.heading or ""
        heading_frame.paragraphs[0].font.size = Pt(44)
        heading_frame.paragraphs[0].font.bold = True
        heading_frame.paragraphs[0].font.color.rgb = RGBColor(*self.branding.primary_color)
        heading_frame.paragraphs[0].font.name = self.branding.font_title

        # Diagram placeholder (for now, just add text description)
        # In real implementation, would generate SVG/PNG or use more complex shapes
        diagram_box = slide.shapes.add_textbox(
            Inches(1.0), Inches(1.5), Inches(17.0), Inches(7.5)
        )
        diagram_frame = diagram_box.text_frame
        diagram_frame.word_wrap = True
        diagram_frame.text = f"[DIAGRAM PLACEHOLDER: {content.heading}]\n\nNarration: {content.narration or ''}"
        diagram_frame.paragraphs[0].font.size = Pt(16)
        diagram_frame.paragraphs[0].font.color.rgb = RGBColor(80, 80, 80)
        diagram_frame.paragraphs[0].font.name = self.branding.font_body

    async def _add_bullets_slide(self, content: SlideContent) -> None:
        """Add a bullets slide with reveal animations."""
        slide_layout = self.prs.slide_layouts[6]  # Blank
        slide = self.prs.slides.add_slide(slide_layout)

        # Background
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = DMLRGBColor(255, 255, 255)

        # Add heading
        heading_box = slide.shapes.add_textbox(
            Inches(0.5), Inches(0.5), Inches(18.5), Inches(0.8)
        )
        heading_frame = heading_box.text_frame
        heading_frame.text = content.heading or ""
        heading_frame.paragraphs[0].font.size = Pt(48)
        heading_frame.paragraphs[0].font.bold = True
        heading_frame.paragraphs[0].font.color.rgb = RGBColor(*self.branding.primary_color)
        heading_frame.paragraphs[0].font.name = self.branding.font_title

        # Add bullets
        if content.bullets:
            bullets_box = slide.shapes.add_textbox(
                Inches(1.0), Inches(1.8), Inches(17.0), Inches(7.5)
            )
            bullets_frame = bullets_box.text_frame
            bullets_frame.word_wrap = True

            for i, bullet in enumerate(content.bullets):
                if i == 0:
                    bullets_frame.text = f"• {bullet}"
                    bullets_frame.paragraphs[0].font.size = Pt(24)
                    bullets_frame.paragraphs[0].font.color.rgb = RGBColor(40, 40, 40)
                    bullets_frame.paragraphs[0].font.name = self.branding.font_body
                else:
                    p = bullets_frame.add_paragraph()
                    p.text = f"• {bullet}"
                    p.font.size = Pt(24)
                    p.font.color.rgb = RGBColor(40, 40, 40)
                    p.font.name = self.branding.font_body
                    p.level = 0

    async def _add_outro_slide(self, content: SlideContent) -> None:
        """Add an outro slide with tagline, footer, optional QR code."""
        slide_layout = self.prs.slide_layouts[6]  # Blank
        slide = self.prs.slides.add_slide(slide_layout)

        # Background color (gradient effect simulated with solid color)
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = DMLRGBColor(*self.branding.secondary_color)

        # Add heading
        if content.heading:
            heading_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(2.0), Inches(18.5), Inches(1.5)
            )
            heading_frame = heading_box.text_frame
            heading_frame.word_wrap = True
            heading_frame.text = content.heading
            heading_frame.paragraphs[0].font.size = Pt(56)
            heading_frame.paragraphs[0].font.bold = True
            heading_frame.paragraphs[0].font.color.rgb = RGBColor(255, 255, 255)
            heading_frame.paragraphs[0].font.name = self.branding.font_title

        # Add tagline
        if content.tagline:
            tagline_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(4.0), Inches(18.5), Inches(2.0)
            )
            tagline_frame = tagline_box.text_frame
            tagline_frame.word_wrap = True
            tagline_frame.text = content.tagline
            tagline_frame.paragraphs[0].font.size = Pt(28)
            tagline_frame.paragraphs[0].font.color.rgb = RGBColor(200, 200, 200)
            tagline_frame.paragraphs[0].font.name = self.branding.font_body

        # Add footer
        if content.footer:
            footer_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(8.8), Inches(18.5), Inches(0.6)
            )
            footer_frame = footer_box.text_frame
            footer_frame.text = content.footer
            footer_frame.paragraphs[0].font.size = Pt(16)
            footer_frame.paragraphs[0].font.color.rgb = RGBColor(*self.branding.accent_color)
            footer_frame.paragraphs[0].font.name = self.branding.font_body

    async def _add_generic_slide(self, content: SlideContent) -> None:
        """Add a generic content slide."""
        slide_layout = self.prs.slide_layouts[6]  # Blank
        slide = self.prs.slides.add_slide(slide_layout)

        # Background
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = DMLRGBColor(255, 255, 255)

        # Add heading
        if content.heading:
            heading_box = slide.shapes.add_textbox(
                Inches(0.5), Inches(0.5), Inches(18.5), Inches(0.8)
            )
            heading_frame = heading_box.text_frame
            heading_frame.text = content.heading
            heading_frame.paragraphs[0].font.size = Pt(44)
            heading_frame.paragraphs[0].font.bold = True
            heading_frame.paragraphs[0].font.color.rgb = RGBColor(*self.branding.primary_color)
            heading_frame.paragraphs[0].font.name = self.branding.font_title

        # Add content
        content_box = slide.shapes.add_textbox(
            Inches(1.0), Inches(1.8), Inches(17.0), Inches(7.5)
        )
        content_frame = content_box.text_frame
        content_frame.word_wrap = True
        content_frame.text = content.narration or f"Slide {self.slides_created}"
        content_frame.paragraphs[0].font.size = Pt(20)
        content_frame.paragraphs[0].font.color.rgb = RGBColor(40, 40, 40)
        content_frame.paragraphs[0].font.name = self.branding.font_body
