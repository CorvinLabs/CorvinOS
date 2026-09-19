"""
Unit + Integration Tests for Asset Manager (Tier 1–2).

Tests:
- Type system (AssetDefinition, AssetType, RenderCache)
- Validation (all renderer types)
- Rendering (basic stubs)
- Caching (hit/miss, size tracking)
- Extensibility (custom renderer registration)
"""

import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from assets.asset_manager import (
    AssetManager,
    AssetDefinition,
    AssetType,
    RenderCache,
    TextSlideRenderer,
    SVGDiagramRenderer,
    ScreenshotRenderer,
    create_text_slide,
    create_screenshot,
    create_svg_diagram,
)


# ============================================================================
# Tier 1: Type System & Basic Structure
# ============================================================================

class TestAssetDefinition:
    """Test AssetDefinition data structure."""

    def test_asset_definition_creation(self):
        """Asset can be created with type and data."""
        asset = AssetDefinition(
            type="text_slide",
            data={"headline": "Test"}
        )
        assert asset.type == "text_slide"
        assert asset.data["headline"] == "Test"

    def test_asset_definition_to_dict(self):
        """Asset converts to dict."""
        asset = create_text_slide("Test Headline", "Subtitle")
        d = asset.to_dict()
        assert d["type"] == "text_slide"
        assert d["data"]["headline"] == "Test Headline"


class TestRenderCache:
    """Test RenderCache functionality."""

    def test_cache_empty_on_init(self):
        """Cache is empty on initialization."""
        cache = RenderCache()
        assert cache.size() == 0

    def test_cache_put_and_get(self):
        """Can put and get items from cache."""
        from PIL import Image

        cache = RenderCache()
        img = Image.new("RGB", (100, 100))

        cache.put("test_id", img)
        assert cache.size() == 1
        assert cache.get("test_id") is img

    def test_cache_miss(self):
        """Cache returns None on miss."""
        cache = RenderCache()
        assert cache.get("nonexistent") is None

    def test_cache_clear(self):
        """Cache can be cleared."""
        from PIL import Image

        cache = RenderCache()
        cache.put("test", Image.new("RGB", (100, 100)))
        assert cache.size() == 1
        cache.clear()
        assert cache.size() == 0


# ============================================================================
# Tier 1: Renderer Validation (no rendering yet)
# ============================================================================

class TestTextSlideValidation:
    """Test TextSlideRenderer validation."""

    def test_valid_text_slide(self):
        """Valid text slide passes validation."""
        renderer = TextSlideRenderer()
        data = {"headline": "Test"}
        assert renderer.validate(data) is True

    def test_missing_headline_fails(self):
        """Text slide without headline fails validation."""
        renderer = TextSlideRenderer()
        data = {"subtitle": "Test"}
        with pytest.raises(ValueError):
            renderer.validate(data)


class TestSVGDiagramValidation:
    """Test SVGDiagramRenderer validation."""

    def test_valid_with_diagram_id(self):
        """SVG with diagram_id passes validation."""
        renderer = SVGDiagramRenderer()
        data = {"diagram_id": "layer_stack"}
        assert renderer.validate(data) is True

    def test_valid_with_svg_path(self):
        """SVG with svg_path passes validation."""
        renderer = SVGDiagramRenderer()
        data = {"svg_path": "/path/to/diagram.svg"}
        assert renderer.validate(data) is True

    def test_missing_both_fails(self):
        """SVG without diagram_id or svg_path fails."""
        renderer = SVGDiagramRenderer()
        data = {"highlight_layer": "L5"}
        with pytest.raises(ValueError):
            renderer.validate(data)


class TestScreenshotValidation:
    """Test ScreenshotRenderer validation."""

    def test_valid_screenshot(self):
        """Screenshot with URL passes validation."""
        renderer = ScreenshotRenderer()
        data = {"url": "http://localhost:8765/console/"}
        assert renderer.validate(data) is True

    def test_missing_url_fails(self):
        """Screenshot without URL fails."""
        renderer = ScreenshotRenderer()
        data = {"selector": ".panel"}
        with pytest.raises(ValueError):
            renderer.validate(data)


# ============================================================================
# Tier 2: Rendering (stubs are OK)
# ============================================================================

class TestAssetRendering:
    """Test asset rendering to PIL.Image."""

    def test_render_text_slide(self):
        """Text slide renders to PIL.Image."""
        manager = AssetManager()
        asset_def = {"type": "text_slide", "data": {"headline": "Test"}}

        image = manager.render_asset(asset_def)

        assert image is not None
        assert image.width == 1920
        assert image.height == 1080
        assert image.mode == "RGB"

    def test_render_svg_diagram(self):
        """SVG diagram renders to PIL.Image."""
        manager = AssetManager()
        asset_def = {"type": "svg_diagram", "data": {"diagram_id": "test"}}

        image = manager.render_asset(asset_def)

        assert image is not None
        assert image.width == 1920
        assert image.height == 1080

    def test_render_screenshot(self):
        """Screenshot renders to PIL.Image (stub)."""
        manager = AssetManager()
        asset_def = {"type": "screenshot", "data": {"url": "http://test.com"}}

        image = manager.render_asset(asset_def)

        assert image is not None
        assert image.width == 1920
        assert image.height == 1080

    def test_unknown_asset_type_fails(self):
        """Unknown asset type raises ValueError."""
        manager = AssetManager()
        asset_def = {"type": "unknown_type", "data": {}}

        with pytest.raises(ValueError):
            manager.render_asset(asset_def)

    def test_missing_type_fails(self):
        """Asset without type raises ValueError."""
        manager = AssetManager()
        asset_def = {"data": {}}

        with pytest.raises(ValueError):
            manager.render_asset(asset_def)


# ============================================================================
# Tier 2: Caching
# ============================================================================

class TestAssetCaching:
    """Test Asset Manager caching behavior."""

    def test_cache_hit(self):
        """Second render of same asset uses cache."""
        manager = AssetManager()
        asset_def = {"type": "text_slide", "data": {"headline": "Test"}}

        # First render
        img1 = manager.render_asset(asset_def)
        assert manager.cache.size() == 1

        # Second render (should hit cache)
        img2 = manager.render_asset(asset_def)
        assert manager.cache.size() == 1
        assert img1 is img2  # Same object (cached)

    def test_different_assets_not_cached_together(self):
        """Different assets don't share cache entries."""
        manager = AssetManager()

        asset1 = {"type": "text_slide", "data": {"headline": "A"}}
        asset2 = {"type": "text_slide", "data": {"headline": "B"}}

        img1 = manager.render_asset(asset1)
        img2 = manager.render_asset(asset2)

        assert manager.cache.size() == 2
        assert img1 is not img2

    def test_cache_clear(self):
        """Cache can be cleared."""
        manager = AssetManager()
        asset_def = {"type": "text_slide", "data": {"headline": "Test"}}

        manager.render_asset(asset_def)
        assert manager.cache.size() == 1

        manager.clear_cache()
        assert manager.cache.size() == 0


# ============================================================================
# Tier 2: Extensibility
# ============================================================================

class TestCustomRenderer:
    """Test registering custom renderers."""

    def test_register_custom_renderer(self):
        """Can register a custom renderer."""
        from assets.asset_manager import AssetRenderer

        class CustomRenderer(AssetRenderer):
            def validate(self, data):
                return "custom_field" in data

            def render(self, data):
                from PIL import Image
                return Image.new("RGB", (1920, 1080), color="red")

        manager = AssetManager()
        manager.register_renderer("custom", CustomRenderer)

        assert "custom" in manager.renderers

        # Render custom asset
        asset_def = {"type": "custom", "data": {"custom_field": "test"}}
        image = manager.render_asset(asset_def)

        assert image.width == 1920
        assert image.height == 1080

    def test_register_non_renderer_fails(self):
        """Cannot register non-AssetRenderer class."""
        manager = AssetManager()

        class NotARenderer:
            pass

        with pytest.raises(TypeError):
            manager.register_renderer("bad", NotARenderer)


# ============================================================================
# Tier 2: Convenience Functions
# ============================================================================

class TestConvenienceFunctions:
    """Test helper functions for common asset types."""

    def test_create_text_slide(self):
        """Helper creates valid text slide."""
        asset = create_text_slide("Headline", "Subtitle", "primary")

        assert asset.type == "text_slide"
        assert asset.data["headline"] == "Headline"
        assert asset.data["subtitle"] == "Subtitle"
        assert asset.data["background_color"] == "primary"

    def test_create_screenshot(self):
        """Helper creates valid screenshot asset."""
        asset = create_screenshot("http://test.com", selector=".panel")

        assert asset.type == "screenshot"
        assert asset.data["url"] == "http://test.com"
        assert asset.data["selector"] == ".panel"

    def test_create_svg_diagram(self):
        """Helper creates valid SVG diagram asset."""
        asset = create_svg_diagram("layer_stack", highlight_layer="L5")

        assert asset.type == "svg_diagram"
        assert asset.data["diagram_id"] == "layer_stack"
        assert asset.data["highlight_layer"] == "L5"


# ============================================================================
# Integration: Multiple Assets
# ============================================================================

class TestMultipleAssets:
    """Test rendering multiple assets in sequence."""

    def test_render_sequence(self):
        """Can render multiple assets in sequence."""
        manager = AssetManager()

        assets = [
            {"type": "text_slide", "data": {"headline": "Part 1"}},
            {"type": "screenshot", "data": {"url": "http://test.com"}},
            {"type": "svg_diagram", "data": {"diagram_id": "test"}},
            {"type": "text_slide", "data": {"headline": "Part 2"}},
        ]

        images = [manager.render_asset(a) for a in assets]

        assert len(images) == 4
        assert all(img.width == 1920 and img.height == 1080 for img in images)
        assert manager.cache.size() == 4


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
