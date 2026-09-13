"""Unit tests for PowerPoint Generator."""

import pytest
import asyncio
import tempfile
import json
from pathlib import Path
from datetime import datetime

# Adjust path for imports
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.workers.powerpoint_generator import (
    PowerPointGenerator,
    BrandingConfig,
    SlideContent,
    AnimationSpec,
)


@pytest.fixture
def branding():
    """Fixture: CorvinOS branding config."""
    return BrandingConfig(
        primary_color=(50, 120, 200),  # CorvinOS blue
        secondary_color=(70, 30, 100),  # Deep purple
        accent_color=(255, 100, 50),   # Orange
        font_title="Montserrat",
        font_body="Inter",
        font_mono="Monaco",
        resolution=(1920, 1080),
    )


@pytest.fixture
def temp_output_dir():
    """Fixture: temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_slides():
    """Fixture: sample slide content."""
    return [
        SlideContent(
            slide_id="s01",
            slide_type="title",
            heading=None,
            title="CorvinOS",
            subtitle="Das erste Agentic Operating System",
            tagline=None,
            bullets=None,
            narration="Intro narration",
            duration_seconds=15.0,
            kicker="Enterprise • Production Ready",
            footer="CorvinOS.dev",
            animations=[
                AnimationSpec("title", "fade-in", 400, 300),
                AnimationSpec("subtitle", "slide-up", 500, 800),
            ],
        ),
        SlideContent(
            slide_id="s02",
            slide_type="diagram",
            heading="Skills 2.0 Architecture",
            title=None,
            subtitle=None,
            tagline=None,
            bullets=None,
            narration="5-box architecture diagram",
            duration_seconds=20.0,
            animations=[AnimationSpec("boxes", "slide-in-left", 500, 500)],
        ),
        SlideContent(
            slide_id="s03",
            slide_type="text-with-bullets",
            heading="Features",
            title=None,
            subtitle=None,
            tagline=None,
            bullets=["Multi-tenant GDPR", "Plugins wired", "Learning loops live"],
            narration="Feature list",
            duration_seconds=18.0,
            animations=[AnimationSpec("bullets", "bullet-reveal", 300, 500)],
        ),
        SlideContent(
            slide_id="s04",
            slide_type="outro",
            heading="Learn More",
            title=None,
            subtitle=None,
            tagline="Skills 2.0 makes your system programmable, learnable, and trustworthy.",
            bullets=None,
            narration="Outro narration",
            duration_seconds=8.0,
            footer="CorvinOS.dev • Open Source",
            animations=[AnimationSpec("heading", "fade-in", 400, 0)],
        ),
    ]


class TestBrandingConfig:
    """Tests for BrandingConfig dataclass."""

    def test_branding_immutable(self, branding):
        """Test that BrandingConfig is frozen (immutable)."""
        with pytest.raises(AttributeError):
            branding.primary_color = (100, 100, 100)

    def test_branding_defaults(self):
        """Test default resolution."""
        b = BrandingConfig(
            primary_color=(0, 0, 0),
            secondary_color=(255, 255, 255),
            accent_color=(128, 128, 128),
            font_title="Arial",
            font_body="Arial",
            font_mono="Courier",
        )
        assert b.resolution == (1920, 1080)
        assert b.logo_path is None


class TestSlideContent:
    """Tests for SlideContent dataclass."""

    def test_slide_content_creation(self):
        """Test creating slide content."""
        slide = SlideContent(
            slide_id="test",
            slide_type="title",
            heading="Test",
            title="Title",
            subtitle="Subtitle",
            tagline=None,
            bullets=None,
            narration="Test narration",
            duration_seconds=10.0,
        )
        assert slide.slide_id == "test"
        assert slide.slide_type == "title"
        assert slide.duration_seconds == 10.0

    def test_slide_with_animations(self):
        """Test slide with animations."""
        anim = AnimationSpec("element", "fade-in", 400, 100)
        slide = SlideContent(
            slide_id="test",
            slide_type="diagram",
            heading="Test",
            title=None,
            subtitle=None,
            tagline=None,
            bullets=None,
            narration="Test",
            duration_seconds=20.0,
            animations=[anim],
        )
        assert len(slide.animations) == 1
        assert slide.animations[0].animation_type == "fade-in"


class TestPowerPointGenerator:
    """Tests for PowerPointGenerator."""

    @pytest.mark.asyncio
    async def test_generator_initialization(self, branding, temp_output_dir):
        """Test generator initialization."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        assert gen.slides_created == 0
        assert gen.branding.primary_color == (50, 120, 200)
        assert gen.output_dir == temp_output_dir

    @pytest.mark.asyncio
    async def test_generate_single_slide(self, branding, temp_output_dir):
        """Test generating a single slide."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        slides = [
            SlideContent(
                slide_id="s01",
                slide_type="title",
                heading=None,
                title="Test Title",
                subtitle="Test Subtitle",
                tagline=None,
                bullets=None,
                narration="Test narration",
                duration_seconds=10.0,
                kicker="Test Kicker",
                footer="Test Footer",
            )
        ]
        result = await gen.generate(slides, "test_single.pptx")
        assert result["status"] == "success"
        assert result["slide_count"] == 1
        assert result["output_path"] is not None
        assert Path(result["output_path"]).exists()

    @pytest.mark.asyncio
    async def test_generate_multiple_slides(self, branding, sample_slides, temp_output_dir):
        """Test generating multiple slides."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        result = await gen.generate(sample_slides, "test_multi.pptx")
        assert result["status"] == "success"
        assert result["slide_count"] == 4
        assert result["file_size_bytes"] > 0
        assert result["animations_count"] == 5

    @pytest.mark.asyncio
    async def test_generate_title_slide(self, branding, temp_output_dir):
        """Test title slide generation."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        slide = SlideContent(
            slide_id="title",
            slide_type="title",
            heading=None,
            title="Main Title",
            subtitle="Subtitle here",
            tagline=None,
            bullets=None,
            narration="narration",
            duration_seconds=15.0,
            kicker="KICKER",
            footer="footer.com",
        )
        result = await gen.generate([slide], "title.pptx")
        assert result["status"] == "success"
        assert result["slide_count"] == 1

    @pytest.mark.asyncio
    async def test_generate_bullets_slide(self, branding, temp_output_dir):
        """Test bullets slide generation."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        slide = SlideContent(
            slide_id="bullets",
            slide_type="text-with-bullets",
            heading="Key Points",
            title=None,
            subtitle=None,
            tagline=None,
            bullets=["Point 1", "Point 2", "Point 3"],
            narration="bullet narration",
            duration_seconds=18.0,
        )
        result = await gen.generate([slide], "bullets.pptx")
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_generate_diagram_slide(self, branding, temp_output_dir):
        """Test diagram slide generation."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        slide = SlideContent(
            slide_id="diagram",
            slide_type="diagram",
            heading="System Architecture",
            title=None,
            subtitle=None,
            tagline=None,
            bullets=None,
            narration="diagram description",
            duration_seconds=20.0,
        )
        result = await gen.generate([slide], "diagram.pptx")
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_generate_outro_slide(self, branding, temp_output_dir):
        """Test outro slide generation."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        slide = SlideContent(
            slide_id="outro",
            slide_type="outro",
            heading="Thank You",
            title=None,
            subtitle=None,
            tagline="Visit CorvinOS.dev",
            bullets=None,
            narration="outro",
            duration_seconds=8.0,
            footer="CorvinOS.dev",
        )
        result = await gen.generate([slide], "outro.pptx")
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_output_file_format(self, branding, sample_slides, temp_output_dir):
        """Test output file is valid PPTX."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        result = await gen.generate(sample_slides, "test_format.pptx")

        # PPTX is a ZIP file, should be readable
        output_path = Path(result["output_path"])
        assert output_path.suffix == ".pptx"
        assert output_path.stat().st_size > 10000  # Reasonable file size

    @pytest.mark.asyncio
    async def test_metadata_in_result(self, branding, sample_slides, temp_output_dir):
        """Test metadata is included in result."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        result = await gen.generate(sample_slides, "test_meta.pptx")

        assert "metadata" in result
        assert "branding_colors" in result["metadata"]
        assert "fonts" in result["metadata"]
        assert "created_at" in result["metadata"]

    @pytest.mark.asyncio
    async def test_invalid_slide_type_fallback(self, branding, temp_output_dir):
        """Test that invalid slide types fall back to generic."""
        gen = PowerPointGenerator(branding, temp_output_dir)
        slide = SlideContent(
            slide_id="invalid",
            slide_type="unknown-type",
            heading="Test",
            title=None,
            subtitle=None,
            tagline=None,
            bullets=None,
            narration="fallback test",
            duration_seconds=10.0,
        )
        result = await gen.generate([slide], "invalid.pptx")
        assert result["status"] == "success"  # Should fall back gracefully


class TestAnimationSpec:
    """Tests for AnimationSpec."""

    def test_animation_creation(self):
        """Test creating animation spec."""
        anim = AnimationSpec("element_id", "fade-in", 400, 100, 50)
        assert anim.element == "element_id"
        assert anim.animation_type == "fade-in"
        assert anim.duration_ms == 400
        assert anim.delay_ms == 100
        assert anim.stagger_ms == 50

    def test_animation_defaults(self):
        """Test animation defaults."""
        anim = AnimationSpec("elem", "appear", 300, 0)
        assert anim.stagger_ms == 0


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestPowerPointGeneratorIntegration:
    """Integration tests for full workflow."""

    @pytest.mark.asyncio
    async def test_full_corvinOS_presentation(self, temp_output_dir):
        """Test generating full CorvinOS presentation."""
        branding = BrandingConfig(
            primary_color=(50, 120, 200),
            secondary_color=(70, 30, 100),
            accent_color=(255, 100, 50),
            font_title="Montserrat",
            font_body="Inter",
            font_mono="Monaco",
            resolution=(1920, 1080),
        )

        slides = [
            SlideContent(
                slide_id="s01",
                slide_type="title",
                heading=None,
                title="CorvinOS",
                subtitle="Agentic Operating System",
                tagline=None,
                bullets=None,
                narration="Intro",
                duration_seconds=15.0,
                kicker="Enterprise Ready",
                footer="CorvinOS.dev",
                animations=[AnimationSpec("title", "fade-in", 400, 300)],
            ),
            SlideContent(
                slide_id="s02",
                slide_type="text-with-bullets",
                heading="Features",
                title=None,
                subtitle=None,
                tagline=None,
                bullets=["Feature 1", "Feature 2"],
                narration="Features",
                duration_seconds=18.0,
                animations=[AnimationSpec("bullets", "reveal", 300, 500)],
            ),
        ]

        gen = PowerPointGenerator(branding, temp_output_dir)
        result = await gen.generate(slides, "corvinOS_demo.pptx")

        assert result["status"] == "success"
        assert result["slide_count"] == 2
        assert result["animations_count"] == 2
        assert Path(result["output_path"]).exists()


# ============================================================================
# ASYNC RUNNER
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
