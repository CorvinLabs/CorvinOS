"""SVG Graphics Generator — Pure Python, no dependencies."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass


@dataclass(frozen=True)
class NodeSpec:
    """Node specification for diagrams."""
    node_id: str
    label: str
    color: str = "#4F46E5"
    shape: str = "rect"  # rect, circle, diamond


@dataclass(frozen=True)
class EdgeSpec:
    """Edge specification for diagrams."""
    source_id: str
    target_id: str
    label: Optional[str] = None
    color: str = "#999999"


class SVGGraphicsGenerator:
    """Pure Python SVG generator for architecture diagrams."""

    def __init__(self, output_dir: Path | str = Path("/tmp")):
        """Initialize."""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def generate_skill_tree(
        self,
        title: str,
        nodes: list[NodeSpec],
        edges: list[EdgeSpec],
        output_filename: str = "skill_tree.svg",
    ) -> dict[str, Any]:
        """Generate Skill 2.0 architecture tree."""

        # Simple grid layout
        svg_width, svg_height = 1200, 800
        node_width, node_height = 200, 100

        # Build nodes SVG
        nodes_svg = ""
        node_positions = {}

        for i, node in enumerate(nodes):
            x = 100 + (i % 3) * 350
            y = 100 + (i // 3) * 250
            node_positions[node.node_id] = (x, y)

            nodes_svg += f'''
  <!-- Node: {node.label} -->
  <g id="{node.node_id}">
    <rect x="{x}" y="{y}" width="{node_width}" height="{node_height}"
          fill="{node.color}" stroke="#333" stroke-width="2" rx="4"/>
    <text x="{x + node_width/2}" y="{y + node_height/2 + 5}"
          text-anchor="middle" font-size="14" font-weight="bold" fill="white">
      {node.label}
    </text>
  </g>'''

        # Build edges SVG
        edges_svg = ""
        for edge in edges:
            if edge.source_id in node_positions and edge.target_id in node_positions:
                x1, y1 = node_positions[edge.source_id]
                x2, y2 = node_positions[edge.target_id]

                x1 += node_width / 2
                y1 += node_height / 2
                x2 += node_width / 2
                y2 += node_height / 2

                edges_svg += f'''
  <!-- Edge: {edge.source_id} -> {edge.target_id} -->
  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"
        stroke="{edge.color}" stroke-width="2" marker-end="url(#arrowhead)"/>'''

        # Build SVG
        svg_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}">
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#999999" />
    </marker>
  </defs>

  <rect width="{svg_width}" height="{svg_height}" fill="#ffffff"/>

  <!-- Title -->
  <text x="{svg_width/2}" y="30" text-anchor="middle" font-size="24" font-weight="bold" fill="#1F2937">
    {title}
  </text>

  <!-- Edges (background) -->{edges_svg}

  <!-- Nodes (foreground) -->{nodes_svg}
</svg>'''

        # Save
        output_path = self.output_dir / output_filename
        output_path.write_text(svg_content)

        return {
            "status": "success",
            "output_path": str(output_path),
            "svg_content": svg_content,
            "file_size_bytes": len(svg_content),
        }

    async def generate_acp_loop(
        self,
        output_filename: str = "acp_loop.svg",
    ) -> dict[str, Any]:
        """Generate ACP feedback loop diagram."""

        svg_width, svg_height = 800, 800
        center_x, center_y = svg_width / 2, svg_height / 2
        radius = 250

        stages = [
            ("Entscheidung", "#FF6B6B"),
            ("Audit", "#4ECDC4"),
            ("Feedback", "#FFE66D"),
            ("Optimierung", "#95E1D3"),
            ("Verbesserung", "#F38181"),
        ]

        svg_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}">
  <rect width="{svg_width}" height="{svg_height}" fill="#ffffff"/>

  <!-- Title -->
  <text x="{center_x}" y="30" text-anchor="middle" font-size="24" font-weight="bold" fill="#1F2937">
    ACP Vision: Learning Loop
  </text>

  <!-- Center text -->
  <text x="{center_x}" y="{center_y + 10}" text-anchor="middle" font-size="16" font-weight="bold" fill="#666666">
    Feedback-driven Learning
  </text>

  <!-- Loop stages (pentagon) -->'''

        import math
        for i, (stage_name, color) in enumerate(stages):
            angle = (i * 2 * math.pi / len(stages)) - math.pi / 2
            x = center_x + radius * math.cos(angle)
            y = center_y + radius * math.sin(angle)

            svg_content += f'''
  <circle cx="{x}" cy="{y}" r="50" fill="{color}" stroke="#333" stroke-width="2"/>
  <text x="{x}" y="{y + 5}" text-anchor="middle" font-size="12" font-weight="bold" fill="white">
    {stage_name}
  </text>'''

        # Draw arrows between stages
        for i in range(len(stages)):
            angle1 = (i * 2 * math.pi / len(stages)) - math.pi / 2
            angle2 = ((i + 1) * 2 * math.pi / len(stages)) - math.pi / 2

            x1 = center_x + (radius - 50) * math.cos(angle1)
            y1 = center_y + (radius - 50) * math.sin(angle1)
            x2 = center_x + (radius - 50) * math.cos(angle2)
            y2 = center_y + (radius - 50) * math.sin(angle2)

            svg_content += f'''
  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"
        stroke="#999" stroke-width="2" marker-end="url(#arrowhead)"/>'''

        svg_content += '''
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#999" />
    </marker>
  </defs>
</svg>'''

        output_path = self.output_dir / output_filename
        output_path.write_text(svg_content)

        return {
            "status": "success",
            "output_path": str(output_path),
            "svg_content": svg_content,
            "file_size_bytes": len(svg_content),
        }

    async def generate_audit_chain(
        self,
        output_filename: str = "audit_chain.svg",
    ) -> dict[str, Any]:
        """Generate audit chain illustration."""

        svg_width, svg_height = 1000, 300

        events = [
            ("skill_executed", "#4F46E5"),
            ("feedback_received", "#F59E0B"),
            ("config_optimized", "#10B981"),
            ("learning_event", "#8B5CF6"),
        ]

        svg_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}">
  <rect width="{svg_width}" height="{svg_height}" fill="#ffffff"/>

  <!-- Title -->
  <text x="{svg_width/2}" y="30" text-anchor="middle" font-size="20" font-weight="bold" fill="#1F2937">
    Audit-First: Hash-Chained Events
  </text>

  <!-- Chain -->'''

        spacing = (svg_width - 100) / len(events)
        y_center = svg_height / 2

        for i, (event_name, color) in enumerate(events):
            x = 50 + i * spacing

            # Event box
            svg_content += f'''
  <rect x="{x - 40}" y="{y_center - 30}" width="80" height="60"
        fill="{color}" stroke="#333" stroke-width="2" rx="4"/>
  <text x="{x}" y="{y_center + 5}" text-anchor="middle" font-size="11" font-weight="bold" fill="white">
    {event_name}
  </text>'''

            # Arrow to next
            if i < len(events) - 1:
                svg_content += f'''
  <line x1="{x + 40}" y1="{y_center}" x2="{x + spacing - 40}" y2="{y_center}"
        stroke="#999" stroke-width="2" marker-end="url(#arrowhead)"/>'''

        svg_content += '''
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#999" />
    </marker>
  </defs>

  <!-- Hash chain info -->
  <text x="50" y="280" font-size="12" fill="#666" font-style="italic">
    Each event: immutable, hash-chained, cryptographically verifiable
  </text>
</svg>'''

        output_path = self.output_dir / output_filename
        output_path.write_text(svg_content)

        return {
            "status": "success",
            "output_path": str(output_path),
            "svg_content": svg_content,
            "file_size_bytes": len(svg_content),
        }
