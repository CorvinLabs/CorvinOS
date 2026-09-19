"""
Phase 5.3: Asset Compositor Tests.

Tests for compositing mixed assets into video frame sequences.
"""

import pytest
import sys
import tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from assets.asset_manager import AssetManager
from video.asset_compositor import AssetCompositor, composite_simple_sequence, FPS


class TestAssetCompositor:
    """Test asset compositor."""

    def test_compositor_creation(self):
        """Should create compositor."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)
        assert compositor is not None
        assert compositor.asset_manager is manager

    def test_compositor_frame_count_tracking(self):
        """Should track frame count."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        assert compositor.get_frame_count() == 0
        compositor.reset()
        assert compositor.get_frame_count() == 0


class TestSequentialComposition:
    """Test sequential asset composition (default layout)."""

    def test_single_asset_scene(self):
        """Single asset should generate correct frame count."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 2,  # 2 seconds = 60 frames @ 30 FPS
                "data": {"headline": "Test"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        assert len(frames) == 2 * FPS  # 60 frames
        assert all(isinstance(f, Image.Image) for f in frames)

    def test_multi_asset_scene(self):
        """Multiple assets should compose sequentially."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 1,
                "data": {"headline": "Slide 1"}
            },
            {
                "type": "text_slide",
                "duration_s": 1,
                "data": {"headline": "Slide 2"}
            },
            {
                "type": "text_slide",
                "duration_s": 1,
                "data": {"headline": "Slide 3"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        # 3 seconds total = 90 frames
        assert len(frames) == 3 * FPS

    def test_frame_output_properties(self):
        """Frames should be proper PIL Images."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 0.1,  # Very short for speed
                "data": {"headline": "Test"}
            }
        ]

        frames = list(compositor.composite_scene(scene))

        for frame in frames:
            assert isinstance(frame, Image.Image)
            assert frame.mode == "RGB"
            assert frame.size == (1920, 1080)

    def test_long_duration_scene(self):
        """Should handle long duration scenes."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 10,  # 10 seconds
                "data": {"headline": "Long slide"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        assert len(frames) == 10 * FPS  # 300 frames


class TestLayoutPatterns:
    """Test different layout composition patterns."""

    def test_sequential_layout_explicit(self):
        """Explicit sequential layout should work."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 1,
                "layout": "sequential",
                "data": {"headline": "Sequential"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        assert len(frames) == FPS

    def test_overlay_layout(self):
        """Overlay layout should composite two assets."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 1,
                "layout": "overlay",
                "background": {
                    "type": "text_slide",
                    "data": {"headline": "Background", "background_color": "primary"}
                },
                "data": {
                    "headline": "Overlay",
                    "background_color": "secondary"
                }
            }
        ]

        frames = list(compositor.composite_scene(scene))
        assert len(frames) == FPS
        assert all(isinstance(f, Image.Image) for f in frames)

    def test_split_screen_layout(self):
        """Split-screen layout should side-by-side two assets."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 1,
                "layout": "split_screen",
                "other": {
                    "type": "text_slide",
                    "data": {"headline": "Right"}
                },
                "data": {"headline": "Left"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        assert len(frames) == FPS
        # Split screen might have different dimensions
        assert all(isinstance(f, Image.Image) for f in frames)

    def test_picture_in_picture_layout(self):
        """PiP layout should place small image in corner."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 1,
                "layout": "picture_in_picture",
                "pip_asset": {
                    "type": "text_slide",
                    "data": {"headline": "PiP"}
                },
                "pip_size": (480, 270),
                "pip_position": "bottom_right",
                "data": {"headline": "Main"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        assert len(frames) == FPS
        assert all(isinstance(f, Image.Image) for f in frames)

    def test_unknown_layout_fallback(self):
        """Unknown layout should fall back to sequential."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 1,
                "layout": "unknown_layout",
                "data": {"headline": "Test"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        assert len(frames) == FPS


class TestImageCompositing:
    """Test image compositing helper methods."""

    def test_overlay_images(self):
        """Should overlay two images."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        # Create test images
        bg = Image.new("RGB", (1920, 1080), color="blue")
        fg = Image.new("RGB", (1920, 1080), color="red")

        result = compositor._overlay_images(bg, fg, alpha=0.5)

        assert isinstance(result, Image.Image)
        assert result.size == (1920, 1080)

    def test_split_screen_images(self):
        """Should create split-screen composition."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        left = Image.new("RGB", (1920, 1080), color="blue")
        right = Image.new("RGB", (1920, 1080), color="red")

        result = compositor._split_screen_images(left, right)

        assert isinstance(result, Image.Image)
        assert result.mode == "RGB"
        # Width should be 2x of input (side by side)

    def test_picture_in_picture_positioning(self):
        """Should place PiP image in correct position."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        bg = Image.new("RGB", (1920, 1080), color="blue")
        pip = Image.new("RGB", (480, 270), color="red")

        for position in ["top_left", "top_right", "bottom_left", "bottom_right"]:
            result = compositor._picture_in_picture(bg, pip, (480, 270), position)
            assert isinstance(result, Image.Image)
            assert result.size == (1920, 1080)


class TestAssetCompositorWithFileOutput:
    """Test compositor with file output."""

    def test_composite_with_frame_saving(self):
        """Should save frames to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = AssetManager()
            compositor = AssetCompositor(manager)

            scene = [
                {
                    "type": "text_slide",
                    "duration_s": 0.1,  # Short for speed
                    "data": {"headline": "Test"}
                }
            ]

            frames = list(compositor.composite_scene(scene, output_dir=tmpdir))

            # Check frames were saved
            saved_frames = list(Path(tmpdir).glob("frame_*.png"))
            assert len(saved_frames) == len(frames)


class TestCompositeSimpleSequence:
    """Test convenience function."""

    def test_simple_sequence_helper(self):
        """composite_simple_sequence helper should work."""
        manager = AssetManager()

        assets = [
            {
                "type": "text_slide",
                "duration_s": 1,
                "data": {"headline": "Test"}
            }
        ]

        frame_count = composite_simple_sequence(manager, assets)
        assert frame_count == FPS

    def test_simple_sequence_with_output_dir(self):
        """Helper should support output directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = AssetManager()

            assets = [
                {
                    "type": "text_slide",
                    "duration_s": 0.1,
                    "data": {"headline": "Test"}
                }
            ]

            frame_count = composite_simple_sequence(manager, assets, output_dir=tmpdir)
            assert frame_count > 0

            # Verify frames were saved
            saved_frames = list(Path(tmpdir).glob("frame_*.png"))
            assert len(saved_frames) == frame_count


class TestCompositorEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_scene(self):
        """Empty scene should yield no frames."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        frames = list(compositor.composite_scene([]))
        assert len(frames) == 0

    def test_zero_duration_asset(self):
        """Zero duration should yield zero frames."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "type": "text_slide",
                "duration_s": 0,
                "data": {"headline": "Zero duration"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        assert len(frames) == 0

    def test_asset_missing_type(self):
        """Asset missing type should be skipped."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene = [
            {
                "duration_s": 1,
                "data": {"headline": "No type"}
            }
        ]

        frames = list(compositor.composite_scene(scene))
        # Should skip invalid asset
        assert len(frames) == 0

    def test_frame_counter_accumulation(self):
        """Frame counter should accumulate across multiple scenes."""
        manager = AssetManager()
        compositor = AssetCompositor(manager)

        scene1 = [{"type": "text_slide", "duration_s": 1, "data": {"headline": "1"}}]
        list(compositor.composite_scene(scene1))
        count1 = compositor.get_frame_count()

        scene2 = [{"type": "text_slide", "duration_s": 1, "data": {"headline": "2"}}]
        list(compositor.composite_scene(scene2))
        count2 = compositor.get_frame_count()

        assert count2 == count1 + FPS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
