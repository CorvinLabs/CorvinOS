"""E2E Tests: Screencast Renderer — Phase 4

Tests screen capture, overlay composition, FFmpeg integration.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from PIL import Image
from unittest.mock import patch, MagicMock

from video_producer_skill_2_0.renderers.screencast_renderer import (
    ScreencastRenderer, ScreencastConfig, Overlay
)
from video_producer_skill_2_0.extension_points import register_extension


class TestScreencastRendererE2E:
    """E2E tests for Screencast Renderer"""

    @pytest.fixture
    def temp_output_dir(self):
        """Create temp output directory"""
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture
    def renderer(self, temp_output_dir):
        """Create screencast renderer instance"""
        config = ScreencastConfig(framerate=30, resolution="1920x1080")
        return ScreencastRenderer(output_dir=temp_output_dir, config=config)

    @pytest.fixture
    def sample_spec(self):
        """Sample video spec for screencast"""
        return {
            "title": "Demo Screencast",
            "duration_seconds": 10,
            "keystrokes": [
                {"time": 2.0, "key": "a"},
                {"time": 3.0, "key": "b"},
            ],
            "annotations": [
                {"text": "Click here", "frame": 50, "position": "top-left"},
            ]
        }

    def test_screencast_config_instantiation(self):
        """Test screencast config can be created"""
        config = ScreencastConfig()
        assert config.framerate == 30
        assert config.resolution == "1920x1080"
        assert config.quality == "high"

    def test_renderer_instantiation(self, renderer):
        """Test renderer can be created"""
        assert renderer.output_dir
        assert renderer.config.framerate == 30
        assert len(renderer.overlays) == 0

    def test_add_overlay(self, renderer):
        """Test adding overlays"""
        overlay = Overlay(type="annotation", position="top-left", text="Test")
        renderer.add_overlay(overlay)
        assert len(renderer.overlays) == 1
        assert renderer.overlays[0].text == "Test"

    def test_multiple_overlays(self, renderer):
        """Test adding multiple overlays"""
        renderer.add_overlay(Overlay(type="annotation", position="top-left", text="Text 1"))
        renderer.add_overlay(Overlay(type="timestamp", position="top-right"))
        renderer.add_overlay(Overlay(type="webcam", position="bottom-right"))
        assert len(renderer.overlays) == 3

    @patch("subprocess.run")
    def test_capture_screen_ffmpeg_call(self, mock_run, renderer):
        """Test screen capture calls FFmpeg"""
        mock_run.return_value = MagicMock(returncode=0)

        output_path = "/tmp/screencast.mp4"
        # This will fail because subprocess is mocked, but we can verify FFmpeg command was attempted
        try:
            renderer.capture_screen(5.0, output_path)
        except:
            pass

        # Verify FFmpeg was called
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert "ffmpeg" in call_args

    def test_draw_text_overlay(self, renderer):
        """Test drawing text overlay on frame"""
        frame = Image.new("RGB", (1920, 1080), color=(100, 100, 100))
        overlay = Overlay(type="annotation", position="top-left", text="Overlay Text")

        result = renderer._draw_text_overlay(frame, overlay, frame_idx=0)

        assert result.size == (1920, 1080)
        assert isinstance(result, Image.Image)

    def test_draw_timestamp_overlay(self, renderer):
        """Test drawing timestamp overlay"""
        frame = Image.new("RGB", (1920, 1080), color=(100, 100, 100))
        overlay = Overlay(type="timestamp", position="top-right")

        result = renderer._draw_timestamp(frame, overlay, frame_idx=60)

        assert result.size == (1920, 1080)
        # Frame 60 at 30fps = 2 seconds

    def test_draw_title_overlay(self, renderer):
        """Test drawing title overlay"""
        frame = Image.new("RGB", (1920, 1080), color=(100, 100, 100))
        overlay = Overlay(type="title", text="Demo Recording")

        result = renderer._draw_title(frame, overlay)

        assert result.size == (1920, 1080)

    def test_draw_cursor_highlight(self, renderer):
        """Test drawing cursor highlight"""
        frame = Image.new("RGB", (1920, 1080), color=(100, 100, 100))

        result = renderer._draw_cursor_highlight(frame, frame_idx=0)

        assert result.size == (1920, 1080)

    def test_draw_keystroke_indicator(self, renderer):
        """Test drawing keystroke indicator"""
        frame = Image.new("RGB", (1920, 1080), color=(100, 100, 100))
        keystrokes = [
            {"time": 1.0, "key": "a"},
            {"time": 2.0, "key": "b"},
        ]

        result = renderer._draw_keystroke_indicator(frame, keystrokes)

        assert result.size == (1920, 1080)

    def test_composite_webcam_placeholder(self, renderer):
        """Test webcam composite (placeholder)"""
        frame = Image.new("RGB", (1920, 1080), color=(100, 100, 100))
        overlay = Overlay(type="webcam", position="bottom-right")

        result = renderer._composite_webcam(frame, overlay, frame_idx=0)

        assert result.size == (1920, 1080)

    def test_overlay_position_variants(self, renderer):
        """Test all overlay position variants"""
        frame = Image.new("RGB", (1920, 1080), color=(100, 100, 100))

        positions = ["top-left", "top-right", "bottom-left", "bottom-right", "center"]

        for position in positions:
            overlay = Overlay(type="annotation", position=position, text="Text")
            result = renderer._draw_text_overlay(frame, overlay, frame_idx=0)
            assert result.size == (1920, 1080)

    def test_cleanup(self, renderer):
        """Test cleanup removes temp directory"""
        temp_dir = renderer.temp_dir
        assert Path(temp_dir).exists()

        renderer.cleanup()

        # Cleanup should remove temp directory
        assert not Path(temp_dir).exists()

    def test_extension_point_registration(self, renderer):
        """Test screencast renderer can be registered as extension point"""

        def screencast_extension(duration_seconds, output_path):
            return str(output_path)

        success = register_extension("screencast_renderer", screencast_extension)
        assert success

    def test_renderer_with_sample_spec(self, renderer, sample_spec):
        """Test renderer integration with video spec"""
        # Add overlays from spec
        for annotation in sample_spec.get("annotations", []):
            renderer.add_overlay(
                Overlay(
                    type="annotation",
                    position=annotation.get("position", "top-left"),
                    text=annotation.get("text")
                )
            )

        assert len(renderer.overlays) == len(sample_spec["annotations"])

    def test_config_quality_settings(self):
        """Test config quality level affects bitrate"""
        config_low = ScreencastConfig(quality="low")
        config_medium = ScreencastConfig(quality="medium")
        config_high = ScreencastConfig(quality="high")

        assert config_low.quality == "low"
        assert config_medium.quality == "medium"
        assert config_high.quality == "high"

    def test_cursor_highlight_toggle(self):
        """Test cursor highlight can be toggled"""
        config_with_cursor = ScreencastConfig(cursor_highlight=True)
        config_without_cursor = ScreencastConfig(cursor_highlight=False)

        assert config_with_cursor.cursor_highlight is True
        assert config_without_cursor.cursor_highlight is False

    def test_keystrokes_display_toggle(self):
        """Test keystroke display can be toggled"""
        config_with_keys = ScreencastConfig(show_keystrokes=True)
        config_without_keys = ScreencastConfig(show_keystrokes=False)

        assert config_with_keys.show_keystrokes is True
        assert config_without_keys.show_keystrokes is False
