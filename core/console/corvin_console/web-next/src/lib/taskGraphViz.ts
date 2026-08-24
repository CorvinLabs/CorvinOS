/**
 * D3.js Layout Module for Task Graph Visualization
 * ADR-0400: Graph-Native Task Execution Model — Phase 2 Visualization
 *
 * Provides force-directed DAG layout, SVG export, and Graphviz DOT export.
 * Stable layout convergence in < 1 second for 100+ nodes.
 */

import * as d3 from "d3";

export interface TaskGraphNode {
  id: string;
  type: string;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface TaskGraphEdge {
  from_id: string;
  to_id: string;
  edge_type: string;
  label: string;
  metadata: Record<string, unknown>;
}

export interface TaskGraph {
  task_id: string;
  created_at: string;
  nodes: Record<string, TaskGraphNode>;
  edges: TaskGraphEdge[];
  nodes_by_type: Record<string, string[]>;
  iterations: Record<number, string>;
}

export interface LayoutNode extends d3.SimulationNodeDatum {
  id: string;
  type: string;
  timestamp: string;
  data: Record<string, unknown>;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
}

export interface LayoutEdge {
  source: LayoutNode | string;
  target: LayoutNode | string;
  edge_type: string;
  label: string;
  metadata: Record<string, unknown>;
}

export interface NodePositions {
  [nodeId: string]: { x: number; y: number };
}

// Node type styling
const NODE_STYLES: Record<string, { color: string; radius: number }> = {
  decision: { color: "#3b82f6", radius: 14 },
  error: { color: "#ef4444", radius: 13 },
  checkpoint: { color: "#10b981", radius: 16 },
  context: { color: "#a3a3a3", radius: 11 },
  metric: { color: "#f59e0b", radius: 10 },
  subgoal: { color: "#8b5cf6", radius: 14 },
};

/**
 * Create and run D3 force simulation for task graph layout
 */
export function createForceSimulation(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  width: number = 1200,
  height: number = 800
): {
  simulation: d3.Simulation<LayoutNode, LayoutEdge>;
  nodes: LayoutNode[];
  edges: LayoutEdge[];
} {
  // Map edge source/target to node references
  const nodeMap = new Map<string, LayoutNode>();
  nodes.forEach((n) => nodeMap.set(n.id, n));

  const linkedEdges = edges.map((e) => ({
    ...e,
    source: typeof e.source === "string" ? nodeMap.get(e.source as string) : e.source,
    target: typeof e.target === "string" ? nodeMap.get(e.target as string) : e.target,
  })) as d3.SimulationLinkDatum<LayoutNode>[];

  // Create force simulation
  const simulation = d3
    .forceSimulation<LayoutNode>(nodes)
    .force("charge", d3.forceManyBody().strength(-300)) // Repulsion: keep nodes apart
    .force(
      "link",
      d3
        .forceLink<LayoutNode, d3.SimulationLinkDatum<LayoutNode>>(linkedEdges as any)
        .strength(0.1) // Weak links allow spread-out layout
        .distance(60)
    )
    .force("center", d3.forceCenter(width / 2, height / 2)) // Keep centered
    .force(
      "collision",
      d3
        .forceCollide<LayoutNode>()
        .radius((d) => {
          const style = NODE_STYLES[d.type] || NODE_STYLES.context;
          return style.radius + 8; // Padding for collision detection
        })
        .iterations(2)
    )
    .stop();

  // Run simulation to convergence (300 ticks ≈ 1 second)
  for (let i = 0; i < 300; i++) {
    simulation.tick();
  }

  return { simulation, nodes, edges: linkedEdges as any };
}

/**
 * Export task graph as SVG string
 */
export function toSvg(
  graph: TaskGraph,
  positions: NodePositions,
  width: number = 1200,
  height: number = 800,
  selectedNodeId?: string
): string {
  // Calculate bounds
  const xs = Object.values(positions).map((p) => p.x);
  const ys = Object.values(positions).map((p) => p.y);
  const minX = Math.min(...xs) - 40;
  const maxX = Math.max(...xs) + 40;
  const minY = Math.min(...ys) - 40;
  const maxY = Math.max(...ys) + 40;

  const viewBox = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;

  let svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" width="${width}" height="${height}">
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#999" />
    </marker>
    <style>
      .edge { stroke: #999; stroke-width: 1.5; fill: none; }
      .edge-label { font-size: 10px; fill: #666; }
      .node { stroke-width: 2; cursor: pointer; }
      .node-label { font-size: 11px; text-anchor: middle; dy: 0.3em; }
    </style>
  </defs>

  <!-- Edges -->
`;

  // Draw edges
  graph.edges.forEach((edge) => {
    const from = positions[edge.from_id];
    const to = positions[edge.to_id];
    if (from && to) {
      const isSelected =
        selectedNodeId === edge.from_id || selectedNodeId === edge.to_id;
      const strokeWidth = isSelected ? 2.5 : 1.5;
      svg += `  <line class="edge" x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}"
        stroke="${isSelected ? "#3b82f6" : "#999"}" stroke-width="${strokeWidth}" marker-end="url(#arrowhead)" />
`;
      // Edge label
      if (edge.label) {
        const midX = (from.x + to.x) / 2;
        const midY = (from.y + to.y) / 2;
        svg += `  <text class="edge-label" x="${midX}" y="${midY - 5}">${escapeHtml(edge.label.substring(0, 20))}</text>
`;
      }
    }
  });

  svg += `  <!-- Nodes -->
`;

  // Draw nodes
  Object.entries(graph.nodes).forEach(([nodeId, node]) => {
    const pos = positions[nodeId];
    if (!pos) return;

    const style = NODE_STYLES[node.type] || NODE_STYLES.context;
    const isSelected = selectedNodeId === nodeId;
    const strokeColor = isSelected ? "#1f2937" : "#fff";
    const strokeWidth = isSelected ? 3 : 2;

    svg += `  <circle class="node" cx="${pos.x}" cy="${pos.y}" r="${style.radius}"
      fill="${style.color}" stroke="${strokeColor}" stroke-width="${strokeWidth}"
      data-node-id="${nodeId}" />
`;
    svg += `  <text class="node-label" x="${pos.x}" y="${pos.y + 12}">${node.type.substring(0, 3)}</text>
`;
  });

  svg += `</svg>`;
  return svg;
}

/**
 * Export task graph as Graphviz DOT format
 */
export function toDot(graph: TaskGraph, _positions: NodePositions): string {
  let dot = `digraph TaskGraph {
  rankdir=TB;
  graph [bgcolor="transparent" fontname="Courier New"];
  node [shape=circle, fontname="Courier New", fontsize=10];
  edge [fontname="Courier New", fontsize=9];

`;

  // Add nodes
  Object.entries(graph.nodes).forEach(([nodeId, node]) => {
    const style = NODE_STYLES[node.type] || NODE_STYLES.context;
    const shortId = nodeId.substring(0, 8);
    const label = `${node.type}\\n${shortId}`;

    dot += `  "${nodeId}" [label="${label}", fillcolor="${style.color}", style="filled"];
`;
  });

  dot += "\n";

  // Add edges
  graph.edges.forEach((edge) => {
    const label = edge.label.replace('"', '\\"');
    dot += `  "${edge.from_id}" -> "${edge.to_id}" [label="${label}"];
`;
  });

  dot += "}\n";
  return dot;
}

/**
 * Extract node positions from simulation after convergence
 */
export function getNodePositions(nodes: LayoutNode[]): NodePositions {
  const positions: NodePositions = {};
  nodes.forEach((node) => {
    if (node.x !== undefined && node.y !== undefined) {
      positions[node.id] = { x: node.x, y: node.y };
    }
  });
  return positions;
}

/**
 * Get node styling
 */
export function getNodeStyle(
  nodeType: string
): { color: string; radius: number } {
  return NODE_STYLES[nodeType] || NODE_STYLES.context;
}

/**
 * Get node type color
 */
export function getNodeColor(nodeType: string): string {
  return getNodeStyle(nodeType).color;
}

/**
 * Calculate node radius
 */
export function getNodeRadius(nodeType: string): number {
  return getNodeStyle(nodeType).radius;
}

/**
 * HTML escape utility for SVG export
 */
function escapeHtml(text: string): string {
  const map: Record<string, string> = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  };
  return text.replace(/[&<>"']/g, (m) => map[m]);
}

/**
 * Find all paths from node to target using BFS
 */
export function findPaths(
  nodeId: string,
  targetId: string,
  graph: TaskGraph
): string[][] {
  const paths: string[][] = [];
  const queue: string[][] = [[nodeId]];

  while (queue.length > 0) {
    const path = queue.shift()!;
    const current = path[path.length - 1];

    if (current === targetId) {
      paths.push(path);
      continue;
    }

    // Find outgoing edges
    const outgoing = graph.edges.filter((e) => e.from_id === current);
    for (const edge of outgoing) {
      if (!path.includes(edge.to_id)) {
        queue.push([...path, edge.to_id]);
      }
    }
  }

  return paths;
}

/**
 * Get all reachable nodes from a starting node (forward reachability)
 */
export function getReachableNodes(
  nodeId: string,
  graph: TaskGraph
): Set<string> {
  const reachable = new Set<string>([nodeId]);
  const queue = [nodeId];

  while (queue.length > 0) {
    const current = queue.shift()!;
    const outgoing = graph.edges.filter((e) => e.from_id === current);

    for (const edge of outgoing) {
      if (!reachable.has(edge.to_id)) {
        reachable.add(edge.to_id);
        queue.push(edge.to_id);
      }
    }
  }

  return reachable;
}

/**
 * Get all nodes that can reach a target node (backward reachability)
 */
export function getReachingNodes(
  nodeId: string,
  graph: TaskGraph
): Set<string> {
  const reaching = new Set<string>([nodeId]);
  const queue = [nodeId];

  while (queue.length > 0) {
    const current = queue.shift()!;
    const incoming = graph.edges.filter((e) => e.to_id === current);

    for (const edge of incoming) {
      if (!reaching.has(edge.from_id)) {
        reaching.add(edge.from_id);
        queue.push(edge.from_id);
      }
    }
  }

  return reaching;
}
