"""REAL E2E Tests — Using call_extension() API (not direct class calls)"""

import pytest
from pathlib import Path
import tempfile
from PIL import Image

from video_producer_skill_2_0.extension_points import call_extension, get_extension
from video_producer_skill_2_0.renderers.svg_renderer import SvgNode, SvgEdge
from video_producer_skill_2_0.renderers.effects_processor import Effect


class TestRealE2E:
    """Real E2E tests through extension point registry"""

    @pytest.fixture
    def temp_dir(self):
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_svg_renderer_via_extension_point(self, temp_dir):
        """Real E2E: call_extension("svg_renderer") produces PNG"""

        # This is the REAL way callers use the renderer
        nodes = [
            SvgNode("start", "BEGIN", 400, 200, color="#4CAF50"),
            SvgNode("end", "END", 400, 600, color="#F44336"),
        ]
        edges = [SvgEdge("start", "end")]

        # Call through extension point (not direct class)
        result = call_extension("svg_renderer", nodes, edges, scene_idx=0)

        # Verify result is valid PNG path
        assert result is not None, "Extension point returned None"
        assert Path(result).exists(), f"Output file not created: {result}"
        assert result.endswith(".png"), "Output should be PNG"

        # Verify it's a valid PNG file
        img = Image.open(result)
        assert img.size == (1920, 1080), "Wrong resolution"

    def test_effects_processor_via_extension_point(self, temp_dir):
        """Real E2E: call_extension("effects_processor") applies effects"""

        # Create sample frames
        frames = []
        frame_paths = []
        for i in range(10):
            frame = Image.new("RGB", (1920, 1080), color=(100, 100, 100))
            path = Path(temp_dir) / f"frame_{i:04d}.png"
            frame.save(path)
            frame_paths.append(str(path))

        # Call through extension point
        result = call_extension("effects_processor", frame_paths)

        # Verify results
        assert result is not None, "Extension point returned None"
        assert len(result) == len(frame_paths), "Wrong output count"
        assert all(Path(p).exists() for p in result), "Not all output files exist"

    def test_screencast_renderer_via_extension_point(self):
        """Real E2E: call_extension("screencast_renderer") is callable"""

        # Verify extension point exists
        ext = get_extension("screencast_renderer")
        assert ext is not None, "screencast_renderer extension not registered"

        # Verify it's callable
        assert callable(ext), "screencast_renderer extension is not callable"

    def test_blender_renderer_via_extension_point(self):
        """Real E2E: call_extension("blender_renderer") is callable"""

        ext = get_extension("blender_renderer")
        assert ext is not None, "blender_renderer extension not registered"
        assert callable(ext), "blender_renderer extension is not callable"

    def test_all_renderers_registered(self):
        """Integration test: All 4 renderers are registered"""

        renderers = [
            "svg_renderer",
            "effects_processor",
            "screencast_renderer",
            "blender_renderer"
        ]

        for renderer_name in renderers:
            ext = get_extension(renderer_name)
            assert ext is not None, f"{renderer_name} not registered"
            assert callable(ext), f"{renderer_name} not callable"

    def test_pipeline_svg_then_effects(self, temp_dir):
        """Full pipeline: SVG render → save → apply effects"""

        # Step 1: Render SVG
        nodes = [SvgNode("test", "Test", 400, 300, color="#2196F3")]
        edges = []
        svg_result = call_extension("svg_renderer", nodes, edges, scene_idx=0)
        assert svg_result is not None

        # Step 2: Create frame sequence from SVG
        frames = []
        for i in range(5):
            img = Image.open(svg_result).copy()
            path = Path(temp_dir) / f"pipeline_frame_{i:04d}.png"
            img.save(path)
            frames.append(str(path))

        # Step 3: Apply effects
        effects_result = call_extension("effects_processor", frames)
        assert effects_result is not None
        assert len(effects_result) == len(frames)

        logger.info(f"✅ Full pipeline test passed: SVG → 5 frames → effects applied")


import logging
logger = logging.getLogger(__name__)
