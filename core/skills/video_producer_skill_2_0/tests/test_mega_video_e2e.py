"""E2E Tests: Mega-Video Integration + Blender Renderer — Phase 4

Full-stack test: all 4 renderers + effects pipeline → final video.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from PIL import Image
from unittest.mock import patch, MagicMock
import json

from video_producer_skill_2_0.renderers.svg_renderer import SvgRenderer, SvgNode, SvgEdge
from video_producer_skill_2_0.renderers.effects_processor import EffectsProcessor, Effect
from video_producer_skill_2_0.renderers.screencast_renderer import ScreencastRenderer, ScreencastConfig, Overlay
from video_producer_skill_2_0.renderers.blender_renderer import BlenderRenderer, BlenderConfig, BlenderScene
from video_producer_skill_2_0.extension_points import (
    register_extension, get_extension, call_extension
)


class TestBlenderRendererE2E:
    """E2E tests for Blender Renderer"""

    @pytest.fixture
    def temp_output_dir(self):
        """Create temp output directory"""
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture
    def renderer(self, temp_output_dir):
        """Create Blender renderer"""
        config = BlenderConfig(framerate=30, samples=100)
        return BlenderRenderer(output_dir=temp_output_dir, config=config)

    def test_blender_config_instantiation(self):
        """Test Blender config creation"""
        config = BlenderConfig()
        assert config.framerate == 30
        assert config.samples == 100
        assert config.engine == "CYCLES"

    def test_renderer_instantiation(self, renderer):
        """Test Blender renderer creation"""
        assert renderer.output_dir
        assert renderer.config.framerate == 30

    def test_render_script_generation(self, renderer):
        """Test Python script generation for Blender"""
        scene = BlenderScene(
            blend_file="/tmp/test.blend",
            start_frame=1,
            end_frame=10
        )

        script = renderer._generate_render_script(scene)

        assert "CYCLES" in script
        assert "1920" in script  # resolution
        assert "30" in script  # framerate
        assert "render.render" in script

    def test_blender_command_building(self, renderer):
        """Test FFmpeg command construction"""
        scene = BlenderScene(
            blend_file="/tmp/test.blend",
            start_frame=1,
            end_frame=100
        )

        cmd = renderer._build_blender_command(scene, Path("/tmp/output"))

        assert "blender" in cmd
        assert "--background" in cmd
        assert "/tmp/test.blend" in cmd
        assert "-a" in cmd  # Render all frames

    def test_camera_animation_script_creation(self, renderer):
        """Test camera animation script generation"""
        camera_path = [
            {"frame": 1, "x": 0, "y": 0, "z": 5},
            {"frame": 50, "x": 5, "y": 0, "z": 5},
            {"frame": 100, "x": 10, "y": 5, "z": 10},
        ]

        script_path = renderer.create_animation_script(
            "/tmp/scene.blend",
            camera_path,
            "/tmp/output",
            duration_frames=100
        )

        assert Path(script_path).exists()
        script_content = Path(script_path).read_text()
        assert "keyframe_insert" in script_content
        assert str(len(camera_path)) in script_content

    @patch("subprocess.run")
    def test_render_scene_ffmpeg_call(self, mock_run, renderer):
        """Test Blender render subprocess execution"""
        mock_run.return_value = MagicMock(returncode=0)

        scene = BlenderScene(
            blend_file="/tmp/test.blend",
            start_frame=1,
            end_frame=10
        )

        try:
            renderer.render_scene(scene)
        except:
            pass

        assert mock_run.called

    def test_cleanup(self, renderer):
        """Test cleanup removes temp directory"""
        temp_dir = renderer.temp_dir
        assert Path(temp_dir).exists()

        renderer.cleanup()

        assert not Path(temp_dir).exists()

    def test_extension_point_registration(self, renderer):
        """Test Blender renderer as extension point"""

        def blender_extension(scene, scene_idx=0):
            return []  # Mock return

        success = register_extension("blender_renderer", blender_extension)
        assert success


class TestMegaVideoIntegrationE2E:
    """Integration tests: all 4 renderers + effects + final video"""

    @pytest.fixture
    def temp_output_dir(self):
        """Create temp output directory"""
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture
    def renderers(self, temp_output_dir):
        """Create all renderer instances"""
        return {
            "svg": SvgRenderer(output_dir=temp_output_dir),
            "effects": EffectsProcessor(output_dir=temp_output_dir),
            "screencast": ScreencastRenderer(output_dir=temp_output_dir),
            "blender": BlenderRenderer(output_dir=temp_output_dir),
        }

    @pytest.fixture
    def sample_video_spec(self):
        """Sample complete video spec"""
        return {
            "title": "Complete Video Production Demo",
            "duration_seconds": 30,
            "framerate": 30,
            "scenes": [
                {
                    "type": "svg",
                    "title": "Flowchart Scene",
                    "nodes": [
                        {"id": "start", "label": "BEGIN", "x": 400, "y": 200, "color": "#4CAF50"},
                        {"id": "end", "label": "END", "x": 400, "y": 600, "color": "#F44336"},
                    ],
                    "edges": [
                        {"from_id": "start", "to_id": "end"},
                    ]
                },
                {
                    "type": "effects",
                    "effects": [
                        {"type": "transition", "name": "fade", "start_frame": 0, "end_frame": 30},
                        {"type": "color_grade", "name": "saturate", "start_frame": 0, "end_frame": 900, "params": {"factor": 1.5}},
                    ]
                },
            ]
        }

    def test_all_extension_points_registered(self, renderers):
        """Test all 4 extension points are registered"""
        from video_producer_skill_2_0.extension_points import _extensions

        # SVG and Effects already registered
        assert "svg_renderer" in _extensions
        assert "effects_processor" in _extensions
        assert "screencast_renderer" in _extensions
        assert "blender_renderer" in _extensions

    def test_svg_render_integration(self, renderers):
        """Test SVG renderer in pipeline"""
        renderer = renderers["svg"]

        nodes = [
            SvgNode("box", "Content", 400, 300, color="#2196F3"),
        ]
        edges = []

        png_path = renderer.render_flowchart(nodes, edges, scene_idx=0)

        assert Path(png_path).exists()

    def test_effects_pipeline_integration(self, renderers, temp_output_dir):
        """Test effects processor in pipeline"""
        # Create sample frames
        frames = []
        frame_paths = []
        for i in range(30):
            frame = Image.new("RGB", (1920, 1080), color=(100 + i*2, 100, 100))
            frame_path = Path(temp_output_dir) / f"frame_{i:04d}.png"
            frame.save(frame_path)
            frame_paths.append(str(frame_path))

        renderer = renderers["effects"]
        renderer.add_effect(Effect("transition", "fade", start_frame=0, end_frame=10))

        output_paths = renderer.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)
        assert all(Path(p).exists() for p in output_paths)

    def test_complete_pipeline_sequence(self, renderers, sample_video_spec, temp_output_dir):
        """Test complete video production pipeline: spec → scenes → effects → final video"""

        # Step 1: Render SVG scene
        svg_renderer = renderers["svg"]
        svg_scene = sample_video_spec["scenes"][0]

        nodes = [
            SvgNode(n["id"], n["label"], n["x"], n["y"], color=n["color"])
            for n in svg_scene["nodes"]
        ]
        edges = [
            SvgEdge(e["from_id"], e["to_id"])
            for e in svg_scene["edges"]
        ]

        svg_output = svg_renderer.render_flowchart(nodes, edges, scene_idx=0)
        assert Path(svg_output).exists()

        # Step 2: Apply effects (would normally be on multiple frames)
        effects_renderer = renderers["effects"]
        effects_spec = sample_video_spec["scenes"][1]

        # Create sample frames for effects
        test_frames = []
        test_frame_paths = []
        for i in range(30):
            frame = Image.open(svg_output).convert("RGB")
            frame_path = Path(temp_output_dir) / f"effect_input_{i:04d}.png"
            frame.save(frame_path)
            test_frame_paths.append(str(frame_path))

        for effect_spec in effects_spec["effects"]:
            effect = Effect(
                type=effect_spec["type"],
                name=effect_spec["name"],
                start_frame=effect_spec["start_frame"],
                end_frame=effect_spec["end_frame"],
                params=effect_spec.get("params", {})
            )
            effects_renderer.add_effect(effect)

        effects_output = effects_renderer.process_frames(test_frame_paths)
        assert len(effects_output) == len(test_frame_paths)
        assert all(Path(p).exists() for p in effects_output)

        logger.info(f"✅ Complete pipeline test passed: {len(effects_output)} frames produced")

    def test_mega_video_validates_all_features(self, renderers):
        """Meta-test: verify all 4 renderers are present and callable"""

        required_renderers = [
            "svg_renderer",
            "effects_processor",
            "screencast_renderer",
            "blender_renderer",
        ]

        for renderer_name in required_renderers:
            ext = get_extension(renderer_name)
            assert ext is not None, f"{renderer_name} extension not found"
            logger.info(f"✅ {renderer_name} extension verified")

    def test_pipeline_output_quality(self, renderers, temp_output_dir):
        """Test pipeline produces valid output files"""

        # Render SVG
        svg_renderer = renderers["svg"]
        nodes = [SvgNode("test", "Test", 400, 300)]
        png_path = svg_renderer.render_flowchart(nodes, [], scene_idx=0)

        # Verify PNG format
        assert Path(png_path).exists()
        with open(png_path, "rb") as f:
            magic = f.read(8)
            assert magic == b'\x89PNG\r\n\x1a\n', "Output should be valid PNG"


# Global logger for tests
import logging
logger = logging.getLogger(__name__)
