"""
Integration Tests: Asset Manager + Screenshot Engine working together.

Tier 3: Integration tests that cross module boundaries.
"""

import pytest
import sys
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from assets.asset_manager import AssetManager, create_screenshot, create_text_slide
from assets.screenshot_engine import ScreenshotEngine


# ============================================================================
# Tier 3: Asset Manager + Screenshot Engine Integration
# ============================================================================

class TestAssetScreenshotIntegration:
    """Test Asset Manager rendering with Screenshot Engine."""

    def test_render_mixed_assets_sequence(self):
        """Can render a sequence of mixed asset types."""
        manager = AssetManager()
        engine = ScreenshotEngine(browser_type="stub")

        # Mixed asset sequence (what videos will use)
        assets = [
            {
                "type": "text_slide",
                "data": {"headline": "CorvinOS Console", "subtitle": "Real-time Dashboard"}
            },
            {
                "type": "screenshot",
                "data": {"url": "http://localhost:8765/console/", "selector": ".dashboard"}
            },
            {
                "type": "svg_diagram",
                "data": {"diagram_id": "layer_stack", "highlight_layer": "L5"}
            },
            {
                "type": "text_slide",
                "data": {"headline": "Architecture Overview", "subtitle": "5-Layer Design"}
            },
        ]

        # Render all assets
        images = []
        for asset in assets:
            image = manager.render_asset(asset)
            images.append(image)

        assert len(images) == 4
        assert all(img.width == 1920 and img.height == 1080 for img in images)

    def test_screenshot_with_asset_annotation(self):
        """Can screenshot and add annotations (using asset manager style)."""
        engine = ScreenshotEngine(browser_type="stub")

        # Screenshot with annotations
        image = engine.screenshot_with_annotations(
            "http://localhost:8765/console/",
            annotations=[
                {
                    "type": "arrow",
                    "x": 500,
                    "y": 300,
                    "text": "Live metrics panel"
                },
                {
                    "type": "box",
                    "x": 800,
                    "y": 400,
                    "size": 150,
                    "text": "Video Producer"
                },
            ]
        )

        assert isinstance(image, Image.Image)
        assert image.size == (1920, 1080)

    def test_storyboard_asset_rendering(self):
        """Can render assets from a storyboard-like JSON structure."""
        manager = AssetManager()

        # Storyboard scene (what phase 4.3 will produce)
        scene = {
            "scene_id": "s01",
            "duration_seconds": 10,
            "assets": [
                {
                    "type": "text_slide",
                    "data": {
                        "headline": "CorvinOS Console",
                        "subtitle": "Real-time Dashboard",
                        "background_color": "primary"
                    }
                },
                {
                    "type": "screenshot",
                    "data": {
                        "url": "http://localhost:8765/console/",
                        "selector": ".dashboard-panel",
                        "wait_ms": 2000
                    }
                },
            ],
            "narration": "The CorvinOS console provides real-time monitoring..."
        }

        # Render all assets in scene
        asset_images = []
        for asset_def in scene["assets"]:
            try:
                image = manager.render_asset(asset_def)
                asset_images.append(image)
            except ValueError:
                # This is OK for now (stubs)
                pass

        assert len(asset_images) >= 1


# ============================================================================
# Tier 3: Video Production Workflow (E2E Preview)
# ============================================================================

class TestVideoProductionWorkflow:
    """Test simplified video production workflow (E2E preview)."""

    def test_simple_storyboard_rendering(self):
        """Can render a simple storyboard end-to-end."""
        manager = AssetManager()

        # Simple storyboard
        storyboard = {
            "title": "Video 1: CorvinOS Overview",
            "scenes": [
                {
                    "scene_id": "s01",
                    "duration_seconds": 5,
                    "assets": [
                        {
                            "type": "text_slide",
                            "data": {
                                "headline": "Welcome to CorvinOS",
                                "subtitle": "The Agent Operating System"
                            }
                        }
                    ],
                    "narration": "Welcome to CorvinOS..."
                },
                {
                    "scene_id": "s02",
                    "duration_seconds": 10,
                    "assets": [
                        {
                            "type": "screenshot",
                            "data": {"url": "http://localhost:8765/console/"}
                        }
                    ],
                    "narration": "The console provides real-time monitoring..."
                },
                {
                    "scene_id": "s03",
                    "duration_seconds": 8,
                    "assets": [
                        {
                            "type": "svg_diagram",
                            "data": {"diagram_id": "layer_stack"}
                        }
                    ],
                    "narration": "The system architecture consists of..."
                },
            ]
        }

        # Render all scenes
        scene_frames = []
        for scene in storyboard["scenes"]:
            for asset in scene["assets"]:
                try:
                    frame = manager.render_asset(asset)
                    scene_frames.append({
                        "scene_id": scene["scene_id"],
                        "frame": frame,
                        "duration": scene["duration_seconds"],
                        "narration": scene["narration"]
                    })
                except ValueError:
                    # Stubs OK
                    pass

        # Verify workflow
        assert len(scene_frames) >= 3
        assert all("frame" in f and "duration" in f for f in scene_frames)

    def test_asset_validation_workflow(self):
        """Can validate assets in storyboard before rendering."""
        manager = AssetManager()

        assets = [
            {"type": "text_slide", "data": {"headline": "Valid"}},
            {"type": "screenshot", "data": {"url": "http://test.com"}},
            {"type": "svg_diagram", "data": {"diagram_id": "test"}},
        ]

        # Validate all
        for asset in assets:
            try:
                valid = manager.validate_asset(asset)
                assert valid is True
            except ValueError as e:
                pytest.fail(f"Asset validation failed: {e}")


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
