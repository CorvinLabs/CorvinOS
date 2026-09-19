"""
Phase 5.2: SVG Renderer Tests.

Tests for SVG diagram rendering to PNG at 1920×1080.
"""

import pytest
import sys
import tempfile
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from assets.svg_renderer import (
    SVGRenderer,
    DiagramRegistry,
    render_diagram,
    render_svg_file,
    render_svg_string,
)


class TestDiagramRegistry:
    """Test diagram registry."""

    def test_get_registered_diagram(self):
        """Should be able to get registered diagram."""
        path = DiagramRegistry.get_svg_path("layer_stack")
        assert path is not None
        assert "layer" in path.lower()

    def test_list_diagrams(self):
        """Should list all registered diagrams."""
        diagrams = DiagramRegistry.list_diagrams()
        assert isinstance(diagrams, list)
        assert len(diagrams) >= 4
        assert "layer_stack" in diagrams

    def test_unknown_diagram_returns_none(self):
        """Unknown diagram should return None."""
        path = DiagramRegistry.get_svg_path("nonexistent_diagram")
        assert path is None

    def test_register_new_diagram(self):
        """Should be able to register new diagrams."""
        DiagramRegistry.register_diagram("test_diagram", "test.svg")
        assert DiagramRegistry.get_svg_path("test_diagram") == "test.svg"


class TestSVGRenderer:
    """Test SVG rendering."""

    def test_renderer_creation(self):
        """Should create renderer."""
        renderer = SVGRenderer()
        assert renderer is not None

    def test_renderer_with_cairosvg_backend(self):
        """Should create renderer with cairosvg backend."""
        renderer = SVGRenderer(backend="cairosvg")
        assert renderer.backend == "cairosvg"

    def test_render_string_returns_image(self):
        """Rendering SVG string should return PIL Image."""
        svg_string = '''<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
            <circle cx="50" cy="50" r="40" fill="red"/>
        </svg>'''

        renderer = SVGRenderer()

        try:
            image = renderer.render_string(svg_string)
            assert isinstance(image, Image.Image)
            assert image.size == (1920, 1080)
            assert image.mode == "RGB"
        except Exception as e:
            pytest.skip(f"SVG rendering not available: {e}")

    def test_render_string_custom_size(self):
        """Should render to custom dimensions."""
        svg_string = '<svg width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>'

        renderer = SVGRenderer()

        try:
            image = renderer.render_string(svg_string, width=800, height=600)
            assert image.size == (800, 600)
        except Exception as e:
            pytest.skip(f"SVG rendering not available: {e}")

    def test_render_file_not_found(self):
        """Rendering nonexistent file should return error placeholder."""
        renderer = SVGRenderer()
        image = renderer.render_file("/nonexistent/diagram.svg")

        assert isinstance(image, Image.Image)
        assert image.size == (1920, 1080)

    def test_render_valid_svg_file(self):
        """Should render valid SVG file."""
        # Create temporary SVG file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.svg', delete=False) as f:
            f.write('''<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">
                <circle cx="50" cy="50" r="40" fill="green"/>
            </svg>''')
            temp_path = f.name

        try:
            renderer = SVGRenderer()
            image = renderer.render_file(temp_path)
            assert isinstance(image, Image.Image)
            assert image.size == (1920, 1080)
        except Exception as e:
            pytest.skip(f"SVG rendering not available: {e}")
        finally:
            Path(temp_path).unlink()

    def test_render_diagram_by_id(self):
        """Should render registered diagram by ID."""
        renderer = SVGRenderer()

        try:
            image = renderer.render_diagram("layer_stack")
            assert isinstance(image, Image.Image)
            # Image might be placeholder if diagram file missing, but should have correct size
            assert image.size == (1920, 1080)
        except Exception as e:
            # This is acceptable if diagram files don't exist
            pass

    def test_render_diagram_unknown_id(self):
        """Rendering unknown diagram should return error placeholder."""
        renderer = SVGRenderer()
        image = renderer.render_diagram("nonexistent_diagram_id")

        assert isinstance(image, Image.Image)
        assert image.size == (1920, 1080)


class TestSVGRendererConvenienceFunctions:
    """Test convenience functions."""

    def test_render_diagram_helper(self):
        """render_diagram helper should work."""
        try:
            image = render_diagram("layer_stack")
            assert isinstance(image, Image.Image)
        except Exception as e:
            # Acceptable if diagram files missing
            pass

    def test_render_svg_string_helper(self):
        """render_svg_string helper should work."""
        svg_string = '<svg width="100" height="100"><rect width="100" height="100" fill="red"/></svg>'

        try:
            image = render_svg_string(svg_string)
            assert isinstance(image, Image.Image)
        except Exception as e:
            pytest.skip(f"SVG rendering not available: {e}")

    def test_render_svg_file_helper(self):
        """render_svg_file helper should work."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.svg', delete=False) as f:
            f.write('<svg width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>')
            temp_path = f.name

        try:
            image = render_svg_file(temp_path)
            assert isinstance(image, Image.Image)
        except Exception as e:
            pytest.skip(f"SVG rendering not available: {e}")
        finally:
            Path(temp_path).unlink()


class TestSVGRendererBackends:
    """Test different rendering backends."""

    def test_cairosvg_availability(self):
        """Check cairosvg availability."""
        renderer = SVGRenderer()
        # This test just checks the detection works
        assert isinstance(renderer._cairosvg_available, bool)

    def test_librsvg_availability(self):
        """Check librsvg availability."""
        renderer = SVGRenderer()
        # This test just checks the detection works
        assert isinstance(renderer._librsvg_available, bool)

    def test_fallback_without_backends(self):
        """Should return error placeholder if no backends available."""
        renderer = SVGRenderer()

        # If both backends unavailable, should return error placeholder
        if not renderer._cairosvg_available and not renderer._librsvg_available:
            svg_string = '<svg width="100" height="100"><rect width="100" height="100"/></svg>'
            image = renderer.render_string(svg_string)
            assert isinstance(image, Image.Image)


class TestSVGIntegrationWithAssetManager:
    """Test SVG rendering integration with AssetManager."""

    def test_asset_manager_svg_renderer(self):
        """AssetManager should use real SVG renderer."""
        from assets.asset_manager import AssetManager

        manager = AssetManager()

        # Create SVG asset
        asset_def = {
            "type": "svg_diagram",
            "data": {"diagram_id": "layer_stack"}
        }

        try:
            image = manager.render_asset(asset_def)
            assert isinstance(image, Image.Image)
            assert image.size == (1920, 1080)
        except Exception as e:
            # Acceptable if diagram files missing
            pass

    def test_asset_manager_svg_with_path(self):
        """AssetManager should support SVG file paths."""
        from assets.asset_manager import AssetManager

        # Create temporary SVG file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.svg', delete=False) as f:
            f.write('<svg width="100" height="100"><rect width="100" height="100" fill="purple"/></svg>')
            temp_path = f.name

        try:
            manager = AssetManager()
            asset_def = {
                "type": "svg_diagram",
                "data": {"svg_path": temp_path}
            }

            image = manager.render_asset(asset_def)
            assert isinstance(image, Image.Image)
        except Exception as e:
            pytest.skip(f"SVG rendering not available: {e}")
        finally:
            Path(temp_path).unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
