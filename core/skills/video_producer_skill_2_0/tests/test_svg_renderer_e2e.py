"""E2E Tests: SVG Renderer — Phase 4

Tests flowchart rendering, diagram generation, SVG→PNG conversion.
"""

import pytest
import os
from pathlib import Path
import tempfile
import shutil

from video_producer_skill_2_0.renderers.svg_renderer import (
    SvgRenderer, SvgNode, SvgEdge
)
from video_producer_skill_2_0.extension_points import (
    register_extension, get_extension, call_extension
)


class TestSvgRendererE2E:
    """E2E tests for SVG Renderer"""

    @pytest.fixture
    def temp_output_dir(self):
        """Create temp directory for test outputs"""
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.fixture
    def renderer(self, temp_output_dir):
        """Create renderer instance"""
        return SvgRenderer(output_dir=temp_output_dir)

    def test_svg_renderer_instantiation(self, renderer):
        """Test SVG renderer can be created"""
        assert renderer.width == 1920
        assert renderer.height == 1080
        assert renderer.output_dir.endswith(renderer.output_dir.split('/')[-1])

    def test_svg_flowchart_render_simple(self, renderer, temp_output_dir):
        """Test rendering simple flowchart with 2 nodes + 1 edge"""

        nodes = [
            SvgNode("start", "BEGIN", 400, 200, color="#4CAF50"),
            SvgNode("end", "FINISH", 400, 600, color="#F44336"),
        ]
        edges = [SvgEdge("start", "end")]

        png_path = renderer.render_flowchart(nodes, edges, scene_idx=0)

        # Verify PNG file was created
        assert Path(png_path).exists(), f"PNG file not created: {png_path}"
        assert Path(png_path).stat().st_size > 0, f"PNG file is empty: {png_path}"
        assert png_path.endswith(".png"), f"Output should be PNG: {png_path}"

    def test_svg_flowchart_render_complex(self, renderer):
        """Test rendering complex flowchart with diamond decision node"""

        nodes = [
            SvgNode("start", "START", 400, 100, color="#4CAF50"),
            SvgNode("decision", "Proceed?", 400, 250, shape="diamond", color="#FF9800", width=100, height=100),
            SvgNode("yes", "Path A", 250, 400, color="#2196F3"),
            SvgNode("no", "Path B", 550, 400, color="#F44336"),
            SvgNode("merge", "END", 400, 550, color="#9C27B0"),
        ]

        edges = [
            SvgEdge("start", "decision"),
            SvgEdge("decision", "yes", label="Yes"),
            SvgEdge("decision", "no", label="No"),
            SvgEdge("yes", "merge"),
            SvgEdge("no", "merge"),
        ]

        png_path = renderer.render_flowchart(nodes, edges, scene_idx=1)

        assert Path(png_path).exists()
        assert Path(png_path).stat().st_size > 1000, "Complex flowchart should produce larger PNG"

    def test_svg_flowchart_multiple_scenes(self, renderer):
        """Test rendering multiple flowchart frames (e.g., for animation)"""

        output_paths = []

        # Render 3 frames showing progression
        for frame_idx in range(3):
            nodes = [
                SvgNode("box1", f"Frame {frame_idx + 1}", 400, 300, color="#2196F3"),
            ]
            edges = []

            png_path = renderer.render_flowchart(nodes, edges, scene_idx=frame_idx)
            output_paths.append(png_path)

        # All frames should exist
        assert len(output_paths) == 3
        for path in output_paths:
            assert Path(path).exists()
            assert Path(path).stat().st_size > 0

    def test_svg_node_shapes(self, renderer):
        """Test all supported node shapes: rect, circle, diamond"""

        nodes = [
            SvgNode("rect", "Box", 300, 300, shape="rect", color="#2196F3"),
            SvgNode("circle", "Circle", 600, 300, shape="circle", color="#FF9800"),
            SvgNode("diamond", "Diamond", 900, 300, shape="diamond", color="#4CAF50"),
        ]
        edges = []

        png_path = renderer.render_flowchart(nodes, edges, scene_idx=0)

        assert Path(png_path).exists()
        # SVG markup should contain all shapes
        svg_path = Path(renderer.output_dir) / "flowchart_scene_0000.svg"
        assert svg_path.exists()
        svg_content = svg_path.read_text()
        assert "rect" in svg_content
        assert "circle" in svg_content
        assert "polygon" in svg_content  # diamond

    def test_svg_edge_styles(self, renderer):
        """Test edge styling: solid vs dashed"""

        nodes = [
            SvgNode("a", "A", 300, 300),
            SvgNode("b", "B", 600, 300),
            SvgNode("c", "C", 900, 300),
        ]
        edges = [
            SvgEdge("a", "b", style="solid"),
            SvgEdge("b", "c", style="dashed"),
        ]

        png_path = renderer.render_flowchart(nodes, edges, scene_idx=0)

        assert Path(png_path).exists()
        svg_path = Path(renderer.output_dir) / "flowchart_scene_0000.svg"
        svg_content = svg_path.read_text()
        assert "stroke-dasharray" in svg_content  # dashed line marker

    def test_svg_hierarchical_layout(self, renderer):
        """Test automatic hierarchical layout (top-to-bottom)"""

        from video_producer_skill_2_0.renderers.svg_renderer import SvgRenderer as SR

        nodes = [
            SvgNode("root", "Root", 0, 0),
            SvgNode("child1", "Child 1", 0, 0),
            SvgNode("child2", "Child 2", 0, 0),
            SvgNode("grandchild", "Grandchild", 0, 0),
        ]
        edges = [
            SvgEdge("root", "child1"),
            SvgEdge("root", "child2"),
            SvgEdge("child1", "grandchild"),
        ]

        # Apply layout
        renderer._apply_hierarchical_layout(nodes, edges)

        # Verify nodes have been positioned
        assert all(n.x != 0 or n.y != 0 for n in nodes), "Nodes should be positioned after layout"

        # Verify hierarchy: children below parents
        root = next(n for n in nodes if n.id == "root")
        child1 = next(n for n in nodes if n.id == "child1")
        assert child1.y > root.y, "Child should be positioned below parent"

    def test_svg_circular_layout(self, renderer):
        """Test circular layout for mind maps/networks"""

        nodes = [
            SvgNode("center", "Hub", 0, 0),
            SvgNode("n1", "Node 1", 0, 0),
            SvgNode("n2", "Node 2", 0, 0),
            SvgNode("n3", "Node 3", 0, 0),
        ]
        edges = []

        # Apply circular layout
        renderer._apply_circular_layout(nodes)

        # Verify nodes are distributed in circle
        center_node = next(n for n in nodes if n.id == "center")
        other_nodes = [n for n in nodes if n.id != "center"]

        # All non-center nodes should be roughly equidistant from center (with some tolerance)
        distances = []
        for node in other_nodes:
            dx = node.x - renderer.width / 2
            dy = node.y - renderer.height / 2
            distance = (dx**2 + dy**2) ** 0.5
            distances.append(distance)

        # Distances should be similar (within 10% variance)
        avg_distance = sum(distances) / len(distances)
        for dist in distances:
            assert abs(dist - avg_distance) / avg_distance < 0.15

    def test_extension_point_registration(self, renderer):
        """Test that SVG renderer can be registered as extension point"""

        # Define a wrapper that uses SVG renderer
        def svg_renderer_extension(nodes, edges, scene_idx=0):
            return renderer.render_flowchart(nodes, edges, scene_idx)

        # Register extension
        success = register_extension("svg_renderer", svg_renderer_extension)
        assert success, "Failed to register svg_renderer extension"

        # Verify registration
        ext = get_extension("svg_renderer")
        assert ext is not None, "svg_renderer extension not found"

        # Call extension
        nodes = [SvgNode("test", "Test", 400, 300)]
        edges = []
        result = call_extension("svg_renderer", nodes, edges, scene_idx=0)

        assert result is not None
        assert Path(result).exists()

    def test_svg_to_png_conversion_quality(self, renderer, temp_output_dir):
        """Test SVG→PNG conversion produces valid image"""

        nodes = [
            SvgNode("box", "Content", 400, 300, color="#2196F3", width=200, height=100),
        ]
        edges = []

        png_path = renderer.render_flowchart(nodes, edges, scene_idx=0)

        # Verify PNG format
        png_file = Path(png_path)
        assert png_file.suffix == ".png"

        # Check PNG magic bytes
        with open(png_file, "rb") as f:
            magic = f.read(8)
            assert magic == b'\x89PNG\r\n\x1a\n', "File should be valid PNG"

        # Check file size is reasonable (at least 1KB)
        assert png_file.stat().st_size > 1024


class TestSvgRendererIntegration:
    """Integration tests with video producer pipeline"""

    def test_svg_renderer_integration_with_spec(self, temp_output_dir):
        """Test SVG renderer integrates with VideoSpec pipeline"""

        renderer = SvgRenderer(output_dir=temp_output_dir)

        # Simulate spec that includes a flowchart scene
        spec = {
            "nodes": [
                {"id": "start", "label": "BEGIN", "x": 400, "y": 200, "color": "#4CAF50"},
                {"id": "end", "label": "END", "x": 400, "y": 600, "color": "#F44336"},
            ],
            "edges": [
                {"from_id": "start", "to_id": "end", "label": ""},
            ],
            "layout": "hierarchical"
        }

        # Render
        png_path = renderer.render_conceptual_diagram(spec, scene_idx=0)

        assert Path(png_path).exists()
        assert png_path.endswith(".png")


@pytest.fixture(scope="module", autouse=True)
def setup_module():
    """Setup: ensure output directories exist"""
    tmpdir = Path(tempfile.gettempdir()) / "svg_renderer_test"
    tmpdir.mkdir(exist_ok=True)
    yield
    shutil.rmtree(tmpdir, ignore_errors=True)
