"""E2E Tests: Effects Processor — Phase 4

Tests transitions (fade, slide, zoom), color grading, animations.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from PIL import Image

from video_producer_skill_2_0.renderers.effects_processor import (
    EffectsProcessor, Effect
)
from video_producer_skill_2_0.extension_points import register_extension, get_extension


class TestEffectsProcessorE2E:
    """E2E tests for Effects Processor"""

    @pytest.fixture
    def temp_output_dir(self):
        """Create temp output directory"""
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture
    def processor(self, temp_output_dir):
        """Create processor instance"""
        return EffectsProcessor(output_dir=temp_output_dir)

    @pytest.fixture
    def sample_frames(self, temp_output_dir, num_frames=30):
        """Create sample frame sequence"""
        frames = []
        paths = []
        for i in range(num_frames):
            frame = Image.new("RGB", (1920, 1080), color=(100 + i*3, 100, 100))
            frame_path = Path(temp_output_dir) / f"input_frame_{i:04d}.png"
            frame.save(frame_path)
            frames.append(frame)
            paths.append(str(frame_path))
        return paths, frames

    def test_effects_processor_instantiation(self, processor):
        """Test processor can be created"""
        assert processor.output_dir
        assert len(processor.effects) == 0

    def test_add_effect_to_queue(self, processor):
        """Test adding effects to processing queue"""
        effect = Effect("transition", "fade", start_frame=0, end_frame=10)
        processor.add_effect(effect)
        assert len(processor.effects) == 1
        assert processor.effects[0].name == "fade"

    def test_fade_transition_in(self, processor, sample_frames):
        """Test fade-in transition"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("transition", "fade", start_frame=0, end_frame=10, params={"direction": "in"})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)
        for path in output_paths[:11]:  # Affected frames
            assert Path(path).exists()
            assert Path(path).stat().st_size > 0

    def test_fade_transition_out(self, processor, sample_frames):
        """Test fade-out transition"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("transition", "fade", start_frame=15, end_frame=25, params={"direction": "out"})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)
        assert all(Path(p).exists() for p in output_paths)

    def test_slide_transition_left(self, processor, sample_frames):
        """Test left slide transition"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("transition", "slide", start_frame=5, end_frame=15, params={"direction": "left"})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)
        assert all(Path(p).exists() for p in output_paths)

    def test_slide_transition_down(self, processor, sample_frames):
        """Test down slide transition"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("transition", "slide", start_frame=0, end_frame=10, params={"direction": "down"})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)

    def test_zoom_transition_in(self, processor, sample_frames):
        """Test zoom-in transition"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("transition", "zoom", start_frame=0, end_frame=15, params={"direction": "in"})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)
        assert all(Path(p).exists() for p in output_paths)

    def test_zoom_transition_out(self, processor, sample_frames):
        """Test zoom-out transition"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("transition", "zoom", start_frame=0, end_frame=15, params={"direction": "out"})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)

    def test_color_grading_saturate(self, processor, sample_frames):
        """Test saturation color grading"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("color_grade", "saturate", start_frame=0, end_frame=30, params={"factor": 1.5})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)
        # Verify output image exists and is valid
        img = Image.open(output_paths[0])
        assert img.size == (1920, 1080)

    def test_color_grading_brightness(self, processor, sample_frames):
        """Test brightness adjustment"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("color_grade", "brightness", start_frame=0, end_frame=30, params={"factor": 1.2})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)

    def test_color_grading_contrast(self, processor, sample_frames):
        """Test contrast adjustment"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("color_grade", "contrast", start_frame=0, end_frame=30, params={"factor": 0.8})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)

    def test_animation_fade_in(self, processor, sample_frames):
        """Test fade-in animation"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("animation", "fade_in", start_frame=0, end_frame=10)
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)
        assert all(Path(p).exists() for p in output_paths)

    def test_animation_fade_out(self, processor, sample_frames):
        """Test fade-out animation"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("animation", "fade_out", start_frame=15, end_frame=25)
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)

    def test_animation_pan(self, processor, sample_frames):
        """Test pan animation"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("animation", "pan", start_frame=0, end_frame=15, params={"pan_x": 200})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)

    def test_animation_scale(self, processor, sample_frames):
        """Test scale animation"""
        frame_paths, _ = sample_frames

        processor.add_effect(
            Effect("animation", "scale", start_frame=5, end_frame=20,
                   params={"start_scale": 1.0, "end_scale": 1.3})
        )

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)

    def test_multiple_effects_combined(self, processor, sample_frames):
        """Test multiple effects applied in sequence"""
        frame_paths, _ = sample_frames

        # Apply multiple effects
        processor.add_effect(Effect("transition", "fade", start_frame=0, end_frame=5))
        processor.add_effect(Effect("color_grade", "saturate", start_frame=0, end_frame=30,
                                   params={"factor": 1.5}))
        processor.add_effect(Effect("animation", "pan", start_frame=10, end_frame=20,
                                   params={"pan_x": 100}))

        output_paths = processor.process_frames(frame_paths)

        assert len(output_paths) == len(frame_paths)
        assert all(Path(p).exists() for p in output_paths)

    def test_effect_with_custom_output_paths(self, processor, sample_frames, temp_output_dir):
        """Test specifying custom output paths"""
        frame_paths, _ = sample_frames
        custom_output_dir = Path(temp_output_dir) / "custom_output"
        custom_output_dir.mkdir()
        custom_paths = [str(custom_output_dir / f"custom_{i:04d}.png") for i in range(len(frame_paths))]

        processor.add_effect(Effect("transition", "fade", start_frame=0, end_frame=10))

        output_paths = processor.process_frames(frame_paths, output_paths=custom_paths)

        assert len(output_paths) == len(custom_paths)
        assert all(Path(p).exists() for p in output_paths)

    def test_extension_point_registration(self, processor):
        """Test that effects processor can be registered as extension point"""

        def effects_extension(frame_paths, effects_list):
            for effect in effects_list:
                processor.add_effect(effect)
            return processor.process_frames(frame_paths)

        success = register_extension("effects_processor", effects_extension)
        assert success

        ext = get_extension("effects_processor")
        assert ext is not None
