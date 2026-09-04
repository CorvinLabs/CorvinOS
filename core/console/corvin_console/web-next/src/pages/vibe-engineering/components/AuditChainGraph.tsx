/**
 * AuditChainGraph — Cytoscape.js visualization of immutable audit events.
 *
 * Features:
 * - Interactive graph (zoom, pan, drag)
 * - Breadth-first layout (chronological)
 * - Color-coded by event type
 * - Real-time filtering (type, skill, outcome)
 * - Node inspection on click
 * - Right-click context menu
 * - Hash-chain verification
 *
 * ADR-0564 Phase 5, Graph Engineering Edition
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  AuditGraph,
  AnyAuditEvent,
  CytoscapeData,
  AuditEventType,
  auditGraphToCytoscape,
} from '@/types/audit-graph';
import { getSkillIdFromEvent } from '../hooks/useAuditQuery';
import { Loader2, ZoomIn, ZoomOut, RefreshCw, Filter, X } from 'lucide-react';
import cytoscape from 'cytoscape';

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface AuditChainGraphProps {
  graph: AuditGraph;
  isLoading?: boolean;
  onNodeSelected?: (event: AnyAuditEvent) => void;
  onRefresh?: () => void;
}

interface GraphFilters {
  types: AuditEventType[];
  skillIds: string[];
  outcomes: string[];
}

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  nodeId?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Node Color Scheme
// ─────────────────────────────────────────────────────────────────────────────

const NODE_COLORS: Record<AuditEventType, string> = {
  skill_executed: '#3b82f6', // Blue
  learning_event: '#22c55e', // Green
  decision: '#f59e0b', // Amber
  context_snapshot: '#8b5cf6', // Purple
  error: '#ef4444', // Red
};

// ─────────────────────────────────────────────────────────────────────────────
// Layout
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Cytoscape layout for the hash chain. The built-in BFS layout is registered as
 * `breadthfirst` (lowercase, no "Search"); until 2026-09-04 this said
 * `breadthFirstSearch`, and cytoscape() THREW "No such layout ... found" from
 * inside the mount effect, so the Graph View never rendered a single node.
 * Exported so tests/unit/audit-chain-graph-layout.test.ts can run it against a
 * headless cytoscape instance — jsdom cannot, and the mocks hid it.
 */
export const AUDIT_GRAPH_LAYOUT = {
  name: 'breadthfirst',
  circle: false,
  spacingFactor: 1.75,
  avoidOverlap: true,
  animationDuration: 300,
} as const;

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function AuditChainGraph({
  graph,
  isLoading = false,
  onNodeSelected,
  onRefresh,
}: AuditChainGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<any>(null);
  const [filters, setFilters] = useState<GraphFilters>({
    types: ['skill_executed', 'learning_event', 'decision', 'context_snapshot', 'error'],
    skillIds: [],
    outcomes: [],
  });
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    visible: false,
    x: 0,
    y: 0,
  });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // ─────────────────────────────────────────────────────────────────────────
  // Initialize Cytoscape
  // ─────────────────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!containerRef.current || !graph || cyRef.current) {
      return;
    }

    // Convert to Cytoscape format
    const cyData = auditGraphToCytoscape(graph);

    // Filter data based on current filters
    const filteredData = filterCytoscapeData(cyData, filters);

    // Initialize Cytoscape
    const rootNodeId = graph.nodes[graph.nodes.length - 1]?.id;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [...filteredData.nodes, ...filteredData.edges],
      style: getCytoscapeStylesheet(),
      layout: {
        ...AUDIT_GRAPH_LAYOUT,
        roots: rootNodeId ? '#' + rootNodeId : undefined,
      } as any,
    });

    cyRef.current = cy;

    // Event handlers
    cy.on('tap', 'node', (event: any) => {
      const nodeId = event.target.id();
      setSelectedNodeId(nodeId);

      // Find full event data
      const node = graph.nodes.find((n) => n.id === nodeId);
      if (node && onNodeSelected) {
        onNodeSelected(node.data);
      }
    });

    cy.on('cxttap', 'node', (event: any) => {
      // Right-click
      event.preventDefault();
      setContextMenu({
        visible: true,
        x: event.renderedPosition.x,
        y: event.renderedPosition.y,
        nodeId: event.target.id(),
      });
    });

    cy.on('tap', (event: any) => {
      // Click on background
      if (event.target === cy) {
        setSelectedNodeId(null);
        setContextMenu({ ...contextMenu, visible: false });
      }
    });

    // Cleanup
    return () => {
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }
    };
  }, [graph, filters, onNodeSelected]);

  // ─────────────────────────────────────────────────────────────────────────
  // Filter Data
  // ─────────────────────────────────────────────────────────────────────────

  const filterCytoscapeData = (data: CytoscapeData, filters: GraphFilters): CytoscapeData => {
    const filteredNodeIds = new Set<string>();

    // Filter nodes
    const filteredNodes = data.nodes.filter((node) => {
      const event = node.data.event;

      // Filter by type
      if (!filters.types.includes(event.type)) {
        return false;
      }

      // Filter by skill ID
      if (filters.skillIds.length > 0) {
        const skillId = getSkillIdFromEvent(node.data.event);
        if (!skillId || !filters.skillIds.includes(skillId)) {
          return false;
        }
      }

      filteredNodeIds.add(node.data.id);
      return true;
    });

    // Filter edges (only keep edges between filtered nodes)
    const filteredEdges = data.edges.filter(
      (edge) => filteredNodeIds.has(edge.data.source) && filteredNodeIds.has(edge.data.target)
    );

    return { nodes: filteredNodes, edges: filteredEdges };
  };

  // ─────────────────────────────────────────────────────────────────────────
  // Toolbar Actions
  // ─────────────────────────────────────────────────────────────────────────

  const handleZoomIn = useCallback(() => {
    if (cyRef.current) {
      cyRef.current.zoom(cyRef.current.zoom() * 1.2);
    }
  }, []);

  const handleZoomOut = useCallback(() => {
    if (cyRef.current) {
      cyRef.current.zoom(cyRef.current.zoom() * 0.8);
    }
  }, []);

  const handleFitView = useCallback(() => {
    if (cyRef.current) {
      cyRef.current.fit(cyRef.current.elements(), 50);
    }
  }, []);

  const toggleEventType = useCallback((type: AuditEventType) => {
    setFilters((prev) => ({
      ...prev,
      types: prev.types.includes(type)
        ? prev.types.filter((t) => t !== type)
        : [...prev.types, type],
    }));
  }, []);

  const clearFilters = useCallback(() => {
    setFilters({
      types: ['skill_executed', 'learning_event', 'decision', 'context_snapshot', 'error'],
      skillIds: [],
      outcomes: [],
    });
  }, []);

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
        {/* Main toolbar */}
        <div className="flex items-center justify-between">
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={handleZoomIn}>
              <ZoomIn className="h-4 w-4" /> Zoom In
            </Button>
            <Button size="sm" variant="outline" onClick={handleZoomOut}>
              <ZoomOut className="h-4 w-4" /> Zoom Out
            </Button>
            <Button size="sm" variant="outline" onClick={handleFitView}>
              Fit View
            </Button>
            <Button size="sm" variant="outline" onClick={onRefresh}>
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
          </div>

          <div className="text-sm text-muted-foreground">
            Nodes: {graph.metadata.nodeCount} | Edges: {graph.metadata.edgeCount} | Freshness:{' '}
            {graph.metadata.snapshotFreshness_ms}ms
          </div>
        </div>

        {/* Filter toolbar */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4" />
            <span className="text-sm font-medium">Filter by Type:</span>
          </div>

          <div className="flex flex-wrap gap-2">
            {['skill_executed', 'learning_event', 'decision', 'context_snapshot', 'error'].map(
              (type) => (
                <button
                  key={type}
                  onClick={() => toggleEventType(type as AuditEventType)}
                  className={`rounded px-3 py-1 text-sm transition-all ${
                    filters.types.includes(type as AuditEventType)
                      ? 'bg-opacity-100 text-white'
                      : 'bg-opacity-30 text-muted-foreground'
                  }`}
                  style={{
                    backgroundColor: NODE_COLORS[type as AuditEventType],
                  }}
                >
                  {type.replace('_', ' ')}
                </button>
              )
            )}

            {(filters.skillIds.length > 0 || filters.outcomes.length > 0) && (
              <Button size="sm" variant="ghost" onClick={clearFilters}>
                <X className="h-4 w-4" /> Clear Filters
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Graph Container */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Audit Chain Graph</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="relative">
            {isLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-background/80">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            )}

            <div
              ref={containerRef}
              className="h-[600px] w-full rounded-lg border border-border bg-background"
            />

            {/* Context Menu */}
            {contextMenu.visible && (
              <div
                className="absolute z-20 rounded-lg border border-border bg-card p-2 shadow-lg"
                style={{
                  left: `${contextMenu.x}px`,
                  top: `${contextMenu.y}px`,
                }}
              >
                <button
                  onClick={() => {
                    if (cyRef.current && contextMenu.nodeId) {
                      cyRef.current.elements(`#${contextMenu.nodeId}`).select();
                      cyRef.current.fit(cyRef.current.elements(`#${contextMenu.nodeId}`), 100);
                    }
                    setContextMenu({ ...contextMenu, visible: false });
                  }}
                  className="block w-full px-3 py-2 text-left text-sm hover:bg-muted"
                >
                  [Isolate This Node]
                </button>
                <button
                  onClick={() => {
                    // TODO: Highlight dependencies
                    setContextMenu({ ...contextMenu, visible: false });
                  }}
                  className="block w-full px-3 py-2 text-left text-sm hover:bg-muted"
                >
                  [Show Dependencies]
                </button>
                <button
                  onClick={() => {
                    // TODO: Copy event ID
                    setContextMenu({ ...contextMenu, visible: false });
                  }}
                  className="block w-full px-3 py-2 text-left text-sm hover:bg-muted"
                >
                  [Copy Event ID]
                </button>
                <button
                  onClick={() => {
                    setContextMenu({ ...contextMenu, visible: false });
                  }}
                  className="block w-full px-3 py-2 text-left text-sm hover:bg-muted"
                >
                  [Close]
                </button>
              </div>
            )}
          </div>

          {/* Selected Node Info */}
          {selectedNodeId && (
            <div className="mt-4 rounded-lg border border-border bg-muted/50 p-4">
              <p className="text-sm text-muted-foreground">
                <span className="font-semibold">Selected Node:</span> {selectedNodeId}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                [Inspector panel on right] • [Hash: {selectedNodeId.substring(0, 16)}...]
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Cytoscape Stylesheet
// ─────────────────────────────────────────────────────────────────────────────

function getCytoscapeStylesheet() {
  const baseNodeStyle = {
    'content': 'data(label)',
    'text-valign': 'center',
    'text-halign': 'center',
    'width': '40px',
    'height': '40px',
    'font-size': '10px',
    'font-weight': 'bold',
    'color': '#fff',
    'text-outline-width': '1px',
    'text-outline-color': '#000',
    'border-width': '2px',
    'border-color': '#fff',
    'transition-property': 'all',
    'transition-duration': '200ms',
  };

  return [
    // Base node style
    {
      selector: 'node',
      style: baseNodeStyle,
    },
    // Type-specific styles
    {
      selector: 'node[type="skill_executed"]',
      style: {
        'background-color': '#3b82f6',
        'shape': 'circle',
      },
    },
    {
      selector: 'node[type="learning_event"]',
      style: {
        'background-color': '#22c55e',
        'shape': 'diamond',
      },
    },
    {
      selector: 'node[type="decision"]',
      style: {
        'background-color': '#f59e0b',
        'shape': 'square',
      },
    },
    {
      selector: 'node[type="context_snapshot"]',
      style: {
        'background-color': '#8b5cf6',
        'shape': 'hexagon',
      },
    },
    {
      selector: 'node[type="error"]',
      style: {
        'background-color': '#ef4444',
        'shape': 'circle',
      },
    },
    {
      selector: 'node:selected',
      style: {
        'border-width': '3px',
        'border-color': '#fbbf24',
        'box-shadow': '0 0 20px rgba(251, 191, 36, 0.5)',
      },
    },
    {
      selector: 'edge',
      style: {
        'target-arrow-shape': 'triangle',
        'target-arrow-color': '#999',
        'line-color': '#999',
        'width': '1px',
        'curve-style': 'straight',
        'opacity': 0.6,
        'transition-property': 'all',
        'transition-duration': '200ms',
      },
    },
    {
      selector: 'edge.hash-chain',
      style: {
        'line-style': 'dashed',
        'line-color': '#d1d5db',
      },
    },
  ] as any;
}
