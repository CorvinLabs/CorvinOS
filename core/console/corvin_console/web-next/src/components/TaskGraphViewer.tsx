/**
 * Task Graph Viewer Component
 * ADR-0400: Graph-Native Task Execution Model — Phase 2 Visualization
 *
 * D3.js force-directed DAG visualization with zoom, pan, filtering, and drill-down.
 * Responsive on mobile (375px), tablet (768px), and desktop (1920px).
 * Supports light/dark mode.
 */

import { useEffect, useRef, useState, useMemo, useCallback } from "react";
import * as d3 from "d3";
import { Download, RefreshCw, Filter, ZoomIn, ZoomOut } from "lucide-react";
import {
  createForceSimulation,
  toSvg,
  toDot,
  getNodePositions,
  LayoutNode,
  LayoutEdge,
  TaskGraph,
} from "@/lib/taskGraphViz";
import { TaskGraphNodeDetail } from "./TaskGraphNodeDetail";
import "@/styles/TaskGraphViewer.css";

interface TaskGraphViewerProps {
  taskId: string;
  graph: TaskGraph | null;
  loading: boolean;
  error: string | null;
  onRefresh?: () => void;
}

interface ZoomState {
  x: number;
  y: number;
  k: number;
}

const NODE_TYPE_COLORS: Record<string, string> = {
  decision: "#3b82f6",
  error: "#ef4444",
  checkpoint: "#10b981",
  context: "#a3a3a3",
  metric: "#f59e0b",
  subgoal: "#8b5cf6",
};

export function TaskGraphViewer({
  taskId,
  graph,
  loading,
  error,
  onRefresh,
}: TaskGraphViewerProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [filteredTypes, setFilteredTypes] = useState<Set<string>>(new Set());
  const [filteredEdgeTypes, setFilteredEdgeTypes] = useState<Set<string>>(
    new Set()
  );
  const [zoom, setZoom] = useState<ZoomState>({ x: 0, y: 0, k: 1 });
  const zoomPercent = Math.round(zoom.k * 100);
  const simulationRef = useRef<d3.Simulation<LayoutNode, LayoutEdge> | null>(null);
  const positionsRef = useRef<Record<string, { x: number; y: number }>>({});
  const zoomBehaviorRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(
    null
  );

  // Get dimensions
  const width = containerRef.current?.clientWidth || 1200;
  const height = containerRef.current?.clientHeight || 800;

  // Process graph data
  const processedData = useMemo(() => {
    if (!graph) return null;

    const nodeTypes = Object.keys(graph.nodes_by_type || {});
    const edgeTypes = Array.from(
      new Set(graph.edges.map((e) => e.edge_type))
    );

    return { nodeTypes, edgeTypes };
  }, [graph]);

  // Create simulation and layout
  useEffect(() => {
    if (!graph || !svgRef.current) return;

    const nodes: LayoutNode[] = Object.entries(graph.nodes).map(
      ([id, node]) => ({
        id,
        type: node.type,
        timestamp: node.timestamp,
        data: node.data,
      })
    );

    const edges: LayoutEdge[] = graph.edges.map((e) => ({
      source: e.from_id,
      target: e.to_id,
      edge_type: e.edge_type,
      label: e.label,
      metadata: e.metadata,
    }));

    // Create simulation
    const { simulation, nodes: layoutNodes } = createForceSimulation(
      nodes,
      edges,
      width,
      height
    );

    simulationRef.current = simulation;
    positionsRef.current = getNodePositions(layoutNodes);

    // Render initial graph
    renderGraph(graph, layoutNodes);
  }, [graph, width, height]);

  // Render graph with D3
  const renderGraph = useCallback(
    (taskGraph: TaskGraph, nodes: LayoutNode[]) => {
      if (!svgRef.current) return;

      const svg = d3.select(svgRef.current);
      const positions = positionsRef.current;

      // Clear previous content
      svg.selectAll("*").remove();

      // Background first, main group second. SVG paints in document order,
      // so appending this rect AFTER the nodes (as it was) put a full-size
      // transparent sheet on top of the graph: every node click landed on
      // .graph-background instead, and the node detail modal never opened.
      svg
        .append("rect")
        .attr("class", "graph-background")
        .attr("width", width)
        .attr("height", height)
        .attr("fill", "transparent")
        .on("click", () => setSelectedNodeId(null));

      // Create main group
      const mainGroup = svg.append("g").attr("class", "main-group");

      // Add zoom behavior
      const zoomBehavior = d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.5, 5])
        .on("zoom", (event) => {
          mainGroup.attr("transform", event.transform);
          setZoom({
            x: event.transform.x,
            y: event.transform.y,
            k: event.transform.k,
          });
        });

      zoomBehaviorRef.current = zoomBehavior;
      svg.call(zoomBehavior);

      // Define arrowhead marker
      svg
        .append("defs")
        .append("marker")
        .attr("id", "arrowhead")
        .attr("markerWidth", 10)
        .attr("markerHeight", 10)
        .attr("refX", 9)
        .attr("refY", 3)
        .attr("orient", "auto")
        .append("polygon")
        .attr("points", "0 0, 10 3, 0 6")
        .attr("fill", "#999");

      // Filter edges and nodes based on selection
      const visibleEdgeTypes =
        filteredEdgeTypes.size === 0
          ? new Set(taskGraph.edges.map((e) => e.edge_type))
          : filteredEdgeTypes;
      const visibleNodeTypes =
        filteredTypes.size === 0
          ? new Set(Object.keys(taskGraph.nodes_by_type))
          : filteredTypes;

      const filteredEdges = taskGraph.edges.filter(
        (e) => visibleEdgeTypes.has(e.edge_type)
      );
      const filteredNodeIds = new Set(
        Object.entries(taskGraph.nodes)
          .filter(([_, node]) => visibleNodeTypes.has(node.type))
          .map(([id]) => id)
      );

      // Draw edges
      mainGroup
        .selectAll(".edge")
        .data(filteredEdges)
        .enter()
        .append("line")
        .attr("class", "edge")
        .attr("x1", (d) => positions[d.from_id]?.x || 0)
        .attr("y1", (d) => positions[d.from_id]?.y || 0)
        .attr("x2", (d) => positions[d.to_id]?.x || 0)
        .attr("y2", (d) => positions[d.to_id]?.y || 0)
        .attr("stroke", (d) => {
          const isSelected =
            selectedNodeId === d.from_id || selectedNodeId === d.to_id;
          return isSelected ? "#3b82f6" : "#999";
        })
        .attr("stroke-width", (d) => {
          const isSelected =
            selectedNodeId === d.from_id || selectedNodeId === d.to_id;
          return isSelected ? 2.5 : 1.5;
        })
        .attr("marker-end", "url(#arrowhead)")
        .on("mouseenter", (_, d) => {
          setHoveredNodeId(d.from_id);
        })
        .on("mouseleave", () => {
          setHoveredNodeId(null);
        });

      // Draw edge labels on hover
      mainGroup
        .selectAll(".edge-label")
        .data(filteredEdges.filter((e) => e.label))
        .enter()
        .append("text")
        .attr("class", "edge-label")
        .attr("x", (d) => {
          const from = positions[d.from_id];
          const to = positions[d.to_id];
          return from && to ? (from.x + to.x) / 2 : 0;
        })
        .attr("y", (d) => {
          const from = positions[d.from_id];
          const to = positions[d.to_id];
          return from && to ? (from.y + to.y) / 2 - 5 : 0;
        })
        .attr("text-anchor", "middle")
        .text((d) => d.label.substring(0, 20))
        .style("opacity", 0)
        .on("mouseenter", function () {
          d3.select(this).style("opacity", 1);
        })
        .on("mouseleave", function () {
          d3.select(this).style("opacity", 0);
        });

      // Node type icons (inspired by Workflows panel)
      const nodeTypeIcons: Record<string, string> = {
        decision: "⟡",
        error: "⊗",
        checkpoint: "◆",
        context: "⊙",
        metric: "📊",
        subgoal: "◈",
      };

      // Draw nodes as large, interactive rectangles with icons
      const nodeGroups = mainGroup
        .selectAll(".node-group")
        .data(
          nodes.filter((n) => filteredNodeIds.has(n.id)),
          (d: any) => d.id
        )
        .enter()
        .append("g")
        .attr("class", "node-group")
        .attr("transform", (d) => {
          const pos = positions[d.id];
          return `translate(${pos?.x || 0}, ${pos?.y || 0})`;
        });

      // Background rectangle with rounded corners
      nodeGroups
        .append("rect")
        .attr("class", "node-bg")
        .attr("x", -18)
        .attr("y", -18)
        .attr("width", 36)
        .attr("height", 36)
        .attr("rx", 6)
        .attr("ry", 6)
        .attr("fill", (d) => NODE_TYPE_COLORS[d.type] || "#a3a3a3")
        .attr("opacity", 0.15)
        .attr("stroke", (d) => NODE_TYPE_COLORS[d.type] || "#a3a3a3")
        .attr("stroke-width", (d) => {
          if (selectedNodeId === d.id) return 2.5;
          if (hoveredNodeId === d.id) return 2;
          return 1.5;
        })
        .style("cursor", "pointer")
        .transition()
        .duration(200);

      // Icon circle (solid)
      nodeGroups
        .append("circle")
        .attr("class", "node-icon-bg")
        .attr("r", 11)
        .attr("fill", (d) => NODE_TYPE_COLORS[d.type] || "#a3a3a3")
        .attr("stroke", (d) => {
          if (selectedNodeId === d.id) return "#fff";
          if (hoveredNodeId === d.id) return "#f0f0f0";
          return "none";
        })
        .attr("stroke-width", (d) => {
          if (selectedNodeId === d.id) return 2;
          if (hoveredNodeId === d.id) return 1.5;
          return 0;
        })
        .style("cursor", "pointer")
        .transition()
        .duration(200);

      // Icon glyph (large, readable)
      nodeGroups
        .append("text")
        .attr("class", "node-icon")
        .attr("text-anchor", "middle")
        .attr("dominant-baseline", "central")
        .attr("font-size", "16px")
        .attr("font-weight", "600")
        .attr("fill", "white")
        .attr("pointer-events", "none")
        .text((d) => nodeTypeIcons[d.type] || "◉");

      // Interactive layer (for click/hover handling)
      nodeGroups
        .append("circle")
        .attr("class", "node")
        .attr("r", 20)
        .attr("fill", "transparent")
        .attr("stroke", "transparent")
        .attr("stroke-width", 0)
        .style("cursor", "pointer")
        .on("click", (event, d) => {
          event.stopPropagation();
          setSelectedNodeId(d.id);
        })
        .on("mouseenter", (_, d) => {
          setHoveredNodeId(d.id);
        })
        .on("mouseleave", () => {
          setHoveredNodeId(null);
        });

      // Tooltip
      nodeGroups
        .append("title")
        .text(
          (d) =>
            `${d.type}\nID: ${d.id.substring(0, 12)}\nTime: ${new Date(d.timestamp).toLocaleTimeString()}`
        );

      // Node type label BELOW the icon (readable size)
      nodeGroups
        .append("text")
        .attr("class", "node-label")
        .attr("y", 26)
        .attr("text-anchor", "middle")
        .attr("font-size", "12px")
        .attr("font-weight", "600")
        .attr("fill", (d) => NODE_TYPE_COLORS[d.type] || "#a3a3a3")
        .attr("pointer-events", "none")
        .text((d) => d.type);
    },
    [selectedNodeId, hoveredNodeId, filteredTypes, filteredEdgeTypes, width, height]
  );

  // Render graph when filters change
  useEffect(() => {
    if (graph && svgRef.current) {
      const nodes: LayoutNode[] = Object.entries(graph.nodes).map(
        ([id, node]) => ({
          id,
          type: node.type,
          timestamp: node.timestamp,
          data: node.data,
        })
      );
      renderGraph(graph, nodes);
    }
  }, [graph, filteredTypes, filteredEdgeTypes, renderGraph]);

  const handleExportSVG = () => {
    if (!graph) return;
    const svgString = toSvg(
      graph,
      positionsRef.current,
      width,
      height,
      selectedNodeId ?? undefined
    );
    downloadFile(svgString, `task-graph-${taskId}.svg`, "image/svg+xml");
  };

  const handleExportDOT = () => {
    if (!graph) return;
    const dotString = toDot(graph, positionsRef.current);
    downloadFile(dotString, `task-graph-${taskId}.dot`, "text/plain");
  };

  const handleResetZoom = () => {
    if (svgRef.current && zoomBehaviorRef.current) {
      const svg = d3.select(svgRef.current);
      svg
        .transition()
        .duration(750)
        .call(zoomBehaviorRef.current.transform, d3.zoomIdentity.translate(50, 50));
    }
  };

  const toggleNodeTypeFilter = (type: string) => {
    const newFiltered = new Set(filteredTypes);
    if (newFiltered.has(type)) {
      newFiltered.delete(type);
    } else {
      newFiltered.add(type);
    }
    setFilteredTypes(newFiltered);
  };

  const toggleEdgeTypeFilter = (type: string) => {
    const newFiltered = new Set(filteredEdgeTypes);
    if (newFiltered.has(type)) {
      newFiltered.delete(type);
    } else {
      newFiltered.add(type);
    }
    setFilteredEdgeTypes(newFiltered);
  };

  if (loading) {
    return (
      <div className="task-graph-loading">
        <div className="flex flex-col items-center justify-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-300 border-t-blue-600"></div>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Loading task graph...
          </p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="task-graph-error">
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950">
          <p className="font-semibold text-red-800 dark:text-red-200">
            Error loading task graph
          </p>
          <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p>
          {onRefresh && (
            <button
              onClick={onRefresh}
              className="mt-3 flex items-center gap-2 rounded bg-red-800 px-3 py-1 text-sm text-white hover:bg-red-900 dark:bg-red-700 dark:hover:bg-red-600"
            >
              <RefreshCw className="h-4 w-4" />
              Retry
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!graph) {
    return (
      <div className="task-graph-empty">
        <div className="text-center text-gray-600 dark:text-gray-400">
          <p className="text-sm">No graph data available</p>
        </div>
      </div>
    );
  }

  const nodeCount = Object.keys(graph.nodes).length;
  const edgeCount = graph.edges.length;

  return (
    <div className="task-graph-container" ref={containerRef}>
      {/* Controls */}
      <div className="task-graph-controls">
        <div className="flex flex-wrap items-center gap-2">
          {/* Zoom controls */}
          <button
            onClick={() => {
              if (svgRef.current && zoomBehaviorRef.current) {
                const svg = d3.select(svgRef.current);
                svg
                  .transition()
                  .duration(300)
                  .call(zoomBehaviorRef.current.scaleBy, 1.5);
              }
            }}
            className="task-graph-btn"
            title="Zoom in"
            aria-label="Zoom in"
          >
            <ZoomIn className="h-4 w-4" />
          </button>

          <button
            onClick={() => {
              if (svgRef.current && zoomBehaviorRef.current) {
                const svg = d3.select(svgRef.current);
                svg
                  .transition()
                  .duration(300)
                  .call(zoomBehaviorRef.current.scaleBy, 0.75);
              }
            }}
            className="task-graph-btn"
            title="Zoom out"
            aria-label="Zoom out"
          >
            <ZoomOut className="h-4 w-4" />
          </button>

          <span className="task-graph-zoom-level" aria-live="polite">
            {zoomPercent}%
          </span>

          <button
            onClick={handleResetZoom}
            className="task-graph-btn"
            title="Reset zoom"
            aria-label="Reset zoom"
          >
            Reset
          </button>

          {/* Separator */}
          <div className="h-6 w-px bg-gray-300 dark:bg-slate-600"></div>

          {/* Export */}
          <button
            onClick={handleExportSVG}
            className="task-graph-btn"
            title="Export as SVG"
            aria-label="Export as SVG"
          >
            <Download className="h-4 w-4" />
            SVG
          </button>

          <button
            onClick={handleExportDOT}
            className="task-graph-btn"
            title="Export as DOT"
            aria-label="Export as DOT"
          >
            <Download className="h-4 w-4" />
            DOT
          </button>

          {/* Refresh */}
          {onRefresh && (
            <>
              <div className="h-6 w-px bg-gray-300 dark:bg-slate-600"></div>
              <button
                onClick={onRefresh}
                className="task-graph-btn"
                title="Refresh graph"
                aria-label="Refresh graph"
              >
                <RefreshCw className="h-4 w-4" />
              </button>
            </>
          )}

          {/* Stats */}
          <div className="h-6 w-px bg-gray-300 dark:bg-slate-600"></div>
          <span className="text-xs text-gray-600 dark:text-gray-400">
            {nodeCount} nodes, {edgeCount} edges
          </span>
        </div>

        {/* Filters */}
        <details className="task-graph-filters">
          <summary className="cursor-pointer flex items-center gap-1 text-xs font-semibold text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100">
            <Filter className="h-4 w-4" />
            Filters
          </summary>

          <div className="mt-2 space-y-3">
            {/* Node Type Filters */}
            {processedData && processedData.nodeTypes.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1">
                  Node Types
                </p>
                <div className="flex flex-wrap gap-2">
                  {processedData.nodeTypes.map((type) => (
                    <label
                      key={type}
                      className="flex items-center gap-1 text-xs cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={!filteredTypes.has(type)}
                        onChange={() => toggleNodeTypeFilter(type)}
                        className="h-3 w-3"
                      />
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{
                          backgroundColor: NODE_TYPE_COLORS[type] || "#a3a3a3",
                        }}
                      ></span>
                      {type}
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* Edge Type Filters */}
            {processedData && processedData.edgeTypes.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1">
                  Edge Types
                </p>
                <div className="flex flex-wrap gap-2">
                  {processedData.edgeTypes.map((type) => (
                    <label
                      key={type}
                      className="flex items-center gap-1 text-xs cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={!filteredEdgeTypes.has(type)}
                        onChange={() => toggleEdgeTypeFilter(type)}
                        className="h-3 w-3"
                      />
                      {type}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        </details>
      </div>

      {/* SVG Canvas */}
      {/*
        viewBox, not width/height attributes. The CSS sizes this element to
        100% of its container (~1040px here) while the layout is computed in a
        fixed 1200x800 space — without a viewBox the graph is drawn 1:1 and
        everything past the container edge is unreachable: Playwright saw
        <main> intercept the pointer on nodes that had drifted off-canvas.
      */}
      <svg
        ref={svgRef}
        className="task-graph-svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
      />

      {/* Node Detail Modal */}
      <TaskGraphNodeDetail
        graph={graph}
        nodeId={selectedNodeId}
        onClose={() => setSelectedNodeId(null)}
      />
    </div>
  );
}

/**
 * Helper: Download file
 */
function downloadFile(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
