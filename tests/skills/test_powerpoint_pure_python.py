"""Unit tests for Pure Python PPTX Generator (no external dependencies)."""

import pytest
import asyncio
import tempfile
from pathlib import Path
from zipfile import ZipFile

# Adjust path for imports
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.workers.powerpoint_generator.generator_pure_python import (
    PurePythonPPTXGenerator,
)


@pytest.fixture
def temp_output_dir():
    """Fixture: temporary output directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestPurePythonPPTXGenerator:
    """Tests for Pure Python PPTX Generator."""

    @pytest.mark.asyncio
    async def test_generator_initialization(self, temp_output_dir):
        """Test generator initialization."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        assert gen.slide_count == 0
        assert gen.output_dir == temp_output_dir

    @pytest.mark.asyncio
    async def test_add_title_slide(self, temp_output_dir):
        """Test adding a title slide."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        gen.add_title_slide(
            kicker="Enterprise Ready",
            title="CorvinOS",
            subtitle="Agentic Operating System",
            footer="CorvinOS.dev",
        )
        assert gen.slide_count == 1
        assert len(gen.slides) == 1

    @pytest.mark.asyncio
    async def test_add_bullets_slide(self, temp_output_dir):
        """Test adding a bullets slide."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        gen.add_bullets_slide(
            heading="Key Features",
            bullets=["Feature 1", "Feature 2", "Feature 3"],
        )
        assert gen.slide_count == 1
        assert len(gen.slides) == 1

    @pytest.mark.asyncio
    async def test_add_generic_slide(self, temp_output_dir):
        """Test adding a generic slide."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        gen.add_generic_slide(
            heading="Slide Heading",
            content="This is the slide content.",
        )
        assert gen.slide_count == 1

    @pytest.mark.asyncio
    async def test_save_creates_valid_pptx(self, temp_output_dir):
        """Test saving creates a valid PPTX file."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        gen.add_title_slide("KICKER", "Title", "Subtitle", "footer.com")

        result = await gen.save("test.pptx")

        assert result["status"] == "success"
        assert "output_path" in result
        assert Path(result["output_path"]).exists()
        assert result["slide_count"] == 1
        assert result["file_size_bytes"] > 0

    @pytest.mark.asyncio
    async def test_pptx_is_valid_zip(self, temp_output_dir):
        """Test that output is a valid ZIP file (PPTX is ZIP-based)."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        gen.add_title_slide("KICKER", "Title", "Subtitle", "footer.com")

        result = await gen.save("test.pptx")
        output_path = Path(result["output_path"])

        # PPTX is a ZIP file
        assert output_path.suffix == ".pptx"

        try:
            with ZipFile(output_path, 'r') as pptx:
                # Check required files exist
                file_list = pptx.namelist()
                assert "[Content_Types].xml" in file_list
                assert "_rels/.rels" in file_list
                assert "ppt/presentation.xml" in file_list
                assert "ppt/slides/slide1.xml" in file_list
        except Exception as e:
            pytest.fail(f"Failed to open as ZIP: {e}")

    @pytest.mark.asyncio
    async def test_multiple_slides(self, temp_output_dir):
        """Test generating presentation with multiple slides."""
        gen = PurePythonPPTXGenerator(temp_output_dir)

        gen.add_title_slide("KICKER", "Title", "Subtitle", "footer")
        gen.add_bullets_slide("Features", ["Point 1", "Point 2"])
        gen.add_generic_slide("Content", "Some content")

        result = await gen.save("multi.pptx")

        assert result["status"] == "success"
        assert result["slide_count"] == 3

        # Verify ZIP contains all slides
        with ZipFile(Path(result["output_path"]), 'r') as pptx:
            file_list = pptx.namelist()
            assert "ppt/slides/slide1.xml" in file_list
            assert "ppt/slides/slide2.xml" in file_list
            assert "ppt/slides/slide3.xml" in file_list

    @pytest.mark.asyncio
    async def test_presentation_xml_generated(self, temp_output_dir):
        """Test that presentation.xml is correctly generated."""
        gen = PurePythonPPTXGenerator(temp_output_dir)

        gen.add_title_slide("K", "T", "S", "F")
        gen.add_bullets_slide("H", ["B1", "B2"])

        result = await gen.save("test.pptx")

        with ZipFile(Path(result["output_path"]), 'r') as pptx:
            pres_xml = pptx.read("ppt/presentation.xml").decode("utf-8")

            # Should contain slide references
            assert "rId1" in pres_xml
            assert "rId2" in pres_xml
            # Should reference correct slide IDs
            assert "256" in pres_xml  # First slide ID = 255 + 1

    @pytest.mark.asyncio
    async def test_core_properties_generated(self, temp_output_dir):
        """Test that core properties (metadata) are generated."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        gen.add_title_slide("K", "T", "S", "F")

        result = await gen.save("test.pptx")

        with ZipFile(Path(result["output_path"]), 'r') as pptx:
            core_xml = pptx.read("docProps/core.xml").decode("utf-8")

            # Should contain CorvinOS branding
            assert "CorvinOS" in core_xml
            # Should have creation timestamp
            assert "<cp:created" in core_xml or ":created" in core_xml

    @pytest.mark.asyncio
    async def test_app_properties_generated(self, temp_output_dir):
        """Test that app properties (document info) are generated."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        gen.add_title_slide("K", "T", "S", "F")
        gen.add_bullets_slide("H", ["B"])

        result = await gen.save("test.pptx")

        with ZipFile(Path(result["output_path"]), 'r') as pptx:
            app_xml = pptx.read("docProps/app.xml").decode("utf-8")

            # Should list number of slides
            assert "<Slides>2</Slides>" in app_xml or "Slides" in app_xml

    @pytest.mark.asyncio
    async def test_special_characters_in_text(self, temp_output_dir):
        """Test handling of special characters."""
        gen = PurePythonPPTXGenerator(temp_output_dir)
        gen.add_title_slide(
            kicker="Spécial & Chàrs",
            title="Tëst™ Video",
            subtitle="Côrvin-Öß™",
            footer="test@domain.com",
        )

        result = await gen.save("special.pptx")
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_empty_presentation(self, temp_output_dir):
        """Test saving empty presentation (no slides)."""
        gen = PurePythonPPTXGenerator(temp_output_dir)

        result = await gen.save("empty.pptx")

        # Should still create valid PPTX
        assert result["status"] == "success"
        assert result["slide_count"] == 0
        assert Path(result["output_path"]).exists()

    @pytest.mark.asyncio
    async def test_large_presentation(self, temp_output_dir):
        """Test generating large presentation (20 slides)."""
        gen = PurePythonPPTXGenerator(temp_output_dir)

        for i in range(20):
            if i % 2 == 0:
                gen.add_title_slide(f"KICKER {i}", f"Title {i}", f"Subtitle {i}", "footer")
            else:
                gen.add_bullets_slide(f"Slide {i}", [f"Bullet {j}" for j in range(3)])

        result = await gen.save("large.pptx")

        assert result["status"] == "success"
        assert result["slide_count"] == 20
        assert result["file_size_bytes"] > 50000  # Should be reasonably sized

    @pytest.mark.asyncio
    async def test_corvinOS_presentation_demo(self, temp_output_dir):
        """Demo: Generate a CorvinOS-themed presentation."""
        gen = PurePythonPPTXGenerator(temp_output_dir)

        # Slide 1: Title
        gen.add_title_slide(
            kicker="Enterprise • Production Ready",
            title="CorvinOS",
            subtitle="Das erste Agentic Operating System",
            footer="CorvinOS.dev",
        )

        # Slide 2: Features
        gen.add_bullets_slide(
            heading="Skills 2.0: Das Herz",
            bullets=[
                "L5 Routing: Intelligente Klassifikation",
                "L10 Context: Adaptive Ingestion",
                "L16 Security: Fail-Closed Gates",
                "L22 Workflow: Orchestration",
                "L34 Data Flow: Sichere Flüsse",
            ],
        )

        # Slide 3: Learning Loop
        gen.add_bullets_slide(
            heading="ACP Vision: Learning Loops",
            bullets=[
                "Decision: Skill führt Operation aus",
                "Audit: Event wird hash-chained logged",
                "Feedback: Nutzer gibt Signal",
                "Optimize: Konfiguration wird tuned",
                "Improve: Nächste Entscheidung besser",
            ],
        )

        # Slide 4: Audit-First
        gen.add_generic_slide(
            heading="Audit-First Architektur",
            content="Jedes Event: immutable, hash-chained, cryptographically verifiable. Kein Silent Learning.",
        )

        # Slide 5: Call to Action
        gen.add_title_slide(
            kicker="Open Source",
            title="Lerne mehr",
            subtitle="Skills 2.0 macht dein System programmierbar, lernbar und vertrauenswürdig",
            footer="CorvinOS.dev",
        )

        result = await gen.save("corvinOS_demo.pptx")

        assert result["status"] == "success"
        assert result["slide_count"] == 5
        assert Path(result["output_path"]).exists()
        assert result["file_size_bytes"] > 20000

        # Verify it's a valid ZIP/PPTX
        with ZipFile(Path(result["output_path"]), 'r') as pptx:
            assert "ppt/presentation.xml" in pptx.namelist()
            assert "ppt/slides/slide5.xml" in pptx.namelist()


# ============================================================================
# ASYNC RUNNER
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
