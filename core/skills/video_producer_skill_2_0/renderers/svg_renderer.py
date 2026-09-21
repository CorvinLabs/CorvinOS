"""SVG Renderer — Phase 4 (Flowcharts, Diagrams)

Renders flowcharts, decision trees, and diagrams to SVG frames.
Supports: boxes, arrows, labels, colors, hierarchy.
"""

import os
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import json

logger = logging.getLogger(__name__)


@dataclass
class SvgNode:
    """SVG diagram node"""
    id: str
    label: str
    x: float
    y: float
    width: float = 120
    height: float = 60
    color: str = "#4A90E2"
    shape: str = "rect"  # rect, circle, diamond


@dataclass
class SvgEdge:
    """SVG diagram edge"""
    from_id: str
    to_id: str
    label: str = ""
    style: str = "solid"  # solid, dashed, dotted


class SvgRenderer:
    """SVG diagram renderer for flowcharts, decision trees, conceptual diagrams"""

    def __init__(self, output_dir: str = "/tmp/video_frames", width: int = 1920, height: int = 1080):
        self.output_dir = output_dir
        self.width = width
        self.height = height
        self.padding = 40
        os.makedirs(output_dir, exist_ok=True)

    def render_flowchart(self, nodes: List[SvgNode], edges: List[SvgEdge], scene_idx: int = 0) -> str:
        """Render flowchart to SVG frame

        Args:
            nodes: List of SvgNode objects
            edges: List of SvgEdge connections
            scene_idx: Frame number in video sequence

        Returns:
            Path to rendered PNG frame
        """
        try:
            # Generate SVG
            svg_content = self._generate_svg(nodes, edges)

            # Save SVG
            svg_path = Path(self.output_dir) / f"flowchart_scene_{scene_idx:04d}.svg"
            svg_path.write_text(svg_content)
            logger.info(f"✅ SVG flowchart rendered: {svg_path}")

            # Convert SVG → PNG
            png_path = self._svg_to_png(svg_path, scene_idx)
            return str(png_path)

        except Exception as e:
            logger.error(f"❌ SVG flowchart rendering failed: {e}")
            raise

    def render_conceptual_diagram(self, spec: Dict, scene_idx: int = 0) -> str:
        """Render conceptual diagram (hierarchy, mind map, network)

        Args:
            spec: Diagram spec (nodes, edges, layout algorithm)
            scene_idx: Frame number

        Returns:
            Path to rendered PNG frame
        """
        try:
            nodes = [SvgNode(**n) for n in spec.get("nodes", [])]
            edges = [SvgEdge(**e) for e in spec.get("edges", [])]
            layout = spec.get("layout", "hierarchical")

            if layout == "hierarchical":
                self._apply_hierarchical_layout(nodes, edges)
            elif layout == "circular":
                self._apply_circular_layout(nodes)

            return self.render_flowchart(nodes, edges, scene_idx)

        except Exception as e:
            logger.error(f"❌ Conceptual diagram rendering failed: {e}")
            raise

    def _generate_svg(self, nodes: List[SvgNode], edges: List[SvgEdge]) -> str:
        """Generate SVG markup for diagram"""

        svg = [
            f'<svg width="{self.width}" height="{self.height}" xmlns="http://www.w3.org/2000/svg">',
            '<defs>',
            '<style>',
            '.node-rect { stroke: #333; stroke-width: 2; }',
            '.node-circle { stroke: #333; stroke-width: 2; }',
            '.node-diamond { stroke: #333; stroke-width: 2; }',
            '.edge { stroke: #333; stroke-width: 2; marker-end: url(#arrowhead); }',
            '.edge-dashed { stroke: #666; stroke-width: 2; stroke-dasharray: 5,5; marker-end: url(#arrowhead-dashed); }',
            '.label { font-family: Arial, sans-serif; font-size: 14px; text-anchor: middle; }',
            '.label-edge { font-family: Arial, sans-serif; font-size: 12px; fill: #666; }',
            '</style>',
            '<marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">',
            '<polygon points="0 0, 10 3, 0 6" fill="#333" />',
            '</marker>',
            '<marker id="arrowhead-dashed" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">',
            '<polygon points="0 0, 10 3, 0 6" fill="#666" />',
            '</marker>',
            '</defs>',
        ]

        # Draw edges first (so nodes appear on top)
        for edge in edges:
            from_node = next((n for n in nodes if n.id == edge.from_id), None)
            to_node = next((n for n in nodes if n.id == edge.to_id), None)
            if from_node and to_node:
                svg.append(self._generate_edge_svg(from_node, to_node, edge))

        # Draw nodes
        for node in nodes:
            svg.append(self._generate_node_svg(node))

        svg.append('</svg>')
        return '\n'.join(svg)

    def _generate_node_svg(self, node: SvgNode) -> str:
        """Generate SVG for a single node"""

        x1 = node.x - node.width / 2
        y1 = node.y - node.height / 2
        x2 = node.x + node.width / 2
        y2 = node.y + node.height / 2

        if node.shape == "rect":
            return (
                f'<rect x="{x1}" y="{y1}" width="{node.width}" height="{node.height}" '
                f'fill="{node.color}" class="node-rect" rx="5" />'
                f'<text x="{node.x}" y="{node.y + 5}" class="label">{node.label}</text>'
            )
        elif node.shape == "circle":
            return (
                f'<circle cx="{node.x}" cy="{node.y}" r="{node.width/2}" '
                f'fill="{node.color}" class="node-circle" />'
                f'<text x="{node.x}" y="{node.y + 5}" class="label">{node.label}</text>'
            )
        elif node.shape == "diamond":
            points = f"{node.x},{y1} {x2},{node.y} {node.x},{y2} {x1},{node.y}"
            return (
                f'<polygon points="{points}" fill="{node.color}" class="node-diamond" />'
                f'<text x="{node.x}" y="{node.y + 5}" class="label">{node.label}</text>'
            )

    def _generate_edge_svg(self, from_node: SvgNode, to_node: SvgNode, edge: SvgEdge) -> str:
        """Generate SVG line from node to node"""

        # Simple straight line connection (could be improved with bezier curves)
        x1, y1 = from_node.x, from_node.y
        x2, y2 = to_node.x, to_node.y

        edge_class = "edge" if edge.style == "solid" else "edge-dashed"
        line = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{edge_class}" />'

        if edge.label:
            mid_x = (x1 + x2) / 2
            mid_y = (y1 + y2) / 2
            label = f'<text x="{mid_x}" y="{mid_y - 5}" class="label-edge">{edge.label}</text>'
            return line + label

        return line

    def _svg_to_png(self, svg_path: Path, frame_idx: int) -> Path:
        """Convert SVG to PNG using cairosvg or ImageMagick"""

        png_path = Path(self.output_dir) / f"flowchart_frame_{frame_idx:04d}.png"

        try:
            # Try cairosvg first (faster)
            import cairosvg
            cairosvg.svg2png(str(svg_path), write_to=str(png_path))
            logger.info(f"✅ SVG→PNG conversion (cairosvg): {png_path}")
        except ImportError:
            # Fallback to ImageMagick
            import subprocess
            result = subprocess.run(
                ["convert", str(svg_path), str(png_path)],
                capture_output=True,
                timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(f"ImageMagick conversion failed: {result.stderr.decode()}")
            logger.info(f"✅ SVG→PNG conversion (ImageMagick): {png_path}")

        return png_path

    def _apply_hierarchical_layout(self, nodes: List[SvgNode], edges: List[SvgEdge]):
        """Apply hierarchical/tree layout (top-to-bottom)"""

        # Simple layout: arrange nodes in rows based on depth
        levels = self._compute_levels(nodes, edges)

        for node_id, level in levels.items():
            node = next((n for n in nodes if n.id == node_id), None)
            if node:
                # Position by level (y) and order within level (x)
                y = 100 + level * 150
                nodes_at_level = [n for n in nodes if levels.get(n.id) == level]
                x_positions = self._distribute_x_positions(len(nodes_at_level))
                idx = nodes_at_level.index(node)
                node.x = x_positions[idx]
                node.y = y

    def _apply_circular_layout(self, nodes: List[SvgNode]):
        """Apply circular layout"""

        import math
        center_x = self.width / 2
        center_y = self.height / 2
        radius = min(self.width, self.height) / 2 - 100

        for i, node in enumerate(nodes):
            angle = 2 * math.pi * i / len(nodes)
            node.x = center_x + radius * math.cos(angle)
            node.y = center_y + radius * math.sin(angle)

    def _compute_levels(self, nodes: List[SvgNode], edges: List[SvgEdge]) -> Dict[str, int]:
        """Compute hierarchy levels for nodes based on edges"""

        levels = {node.id: 0 for node in nodes}
        changed = True

        while changed:
            changed = False
            for edge in edges:
                from_level = levels[edge.from_id]
                to_level = levels[edge.to_id]
                if to_level <= from_level:
                    levels[edge.to_id] = from_level + 1
                    changed = True

        return levels

    def _distribute_x_positions(self, count: int) -> List[float]:
        """Distribute X positions evenly across width"""

        if count == 1:
            return [self.width / 2]

        spacing = (self.width - 2 * self.padding) / (count - 1)
        return [self.padding + i * spacing for i in range(count)]


# Example usage
def example_svg_flowchart():
    """Example: render a simple flowchart"""

    renderer = SvgRenderer()

    nodes = [
        SvgNode("start", "START", 400, 100, color="#4CAF50"),
        SvgNode("decision", "Decision?", 400, 250, shape="diamond", color="#FF9800"),
        SvgNode("yes", "Execute Yes", 250, 400, color="#2196F3"),
        SvgNode("no", "Execute No", 550, 400, color="#F44336"),
        SvgNode("end", "END", 400, 550, color="#9C27B0"),
    ]

    edges = [
        SvgEdge("start", "decision"),
        SvgEdge("decision", "yes", label="Yes"),
        SvgEdge("decision", "no", label="No"),
        SvgEdge("yes", "end"),
        SvgEdge("no", "end"),
    ]

    return renderer.render_flowchart(nodes, edges, scene_idx=0)


__all__ = ["SvgRenderer", "SvgNode", "SvgEdge"]
