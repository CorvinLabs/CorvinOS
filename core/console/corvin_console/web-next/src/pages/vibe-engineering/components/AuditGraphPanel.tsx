/**
 * AuditGraphPanel — DAG visualization of audit-chain events
 *
 * Shows real audit events as an acyclic directed graph:
 * - Nodes: events (colored by event_type)
 * - Edges: hash-chain causality (prev_hash → hash)
 * - Critical path: longest chain highlighted
 * - Anomalies: cycles, disconnected components flagged
 *
 * Uses Cytoscape.js for layout (better than D3 for acyclic graphs).
 * Dark/light mode consistent with ContextLayersPanel.
 */

'use client'

import { useEffect, useRef, useState } from 'react'
import CytoscapeComponent from 'react-cytoscapejs' // typed in src/types/react-cytoscapejs.d.ts
import type cytoscape from 'cytoscape'

interface GraphNode {
  id: string
  event_type: string
  ts: number
  severity: string
  run_id: string
  details: Record<string, unknown>
}

interface GraphEdge {
  from_node: string
  to_node: string
  type: string
}

interface AuditGraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  total_events: number
  critical_path: string[]
  anomalies: Array<{ type: string; severity: string; message: string }>
}

interface TooltipState {
  visible: boolean
  x: number
  y: number
  nodeId: string
  node?: GraphNode
}

const EVENT_TYPE_COLORS: Record<string, string> = {
  'boot.self_test_passed': '#3b82f6',      // blue
  'boot.plugin_loaded': '#06b6d4',          // cyan
  'compliance.manifest_check': '#10b981',   // green
  'layer_integrity.manifest_check': '#f59e0b', // amber
  'acs_x.classified': '#8b5cf6',            // violet
  'skill_executed': '#ec4899',              // pink
  'consent_granted': '#14b8a6',             // teal
  'error': '#ef4444',                       // red
  'default': '#6b7280'                      // gray
}

function AuditGraphPanel() {
  const [data, setData] = useState<AuditGraphResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tooltip, setTooltip] = useState<TooltipState>({ visible: false, x: 0, y: 0, nodeId: '' })
  const [darkMode, setDarkMode] = useState(false)
  const [filterEventType, setFilterEventType] = useState<string>('')
  const cyRef = useRef<cytoscape.Core | null>(null)

  // Detect dark mode from system
  useEffect(() => {
    const isDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? true
    setDarkMode(isDark)

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handleChange = (e: MediaQueryListEvent) => setDarkMode(e.matches)
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [])

  // Fetch DAG data
  useEffect(() => {
    const fetchGraph = async () => {
      try {
        setLoading(true)
        const res = await fetch('/v1/console/audit/graph?limit=500')
        if (!res.ok) throw new Error(`API error: ${res.status}`)
        const json = (await res.json()) as AuditGraphResponse
        setData(json)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoading(false)
      }
    }

    fetchGraph()
  }, [])

  // Build Cytoscape elements
  const buildElements = (graphData: AuditGraphResponse, eventTypeFilter: string) => {
    const filteredNodes = eventTypeFilter
      ? graphData.nodes.filter(n => n.event_type === eventTypeFilter)
      : graphData.nodes

    const filteredNodeIds = new Set(filteredNodes.map(n => n.id))

    const elements = [
      ...filteredNodes.map(node => ({
        data: {
          id: node.id,
          label: node.event_type.split('.').pop() || node.event_type,
          event_type: node.event_type,
          ts: node.ts,
          severity: node.severity,
        },
        style: {
          'background-color': EVENT_TYPE_COLORS[node.event_type] || EVENT_TYPE_COLORS.default,
          'border-color':
            graphData.critical_path.includes(node.id) ? '#fbbf24' : 'transparent',
          'border-width': graphData.critical_path.includes(node.id) ? 3 : 1,
          'font-size': '12px',
          'text-valign': 'center',
          'text-halign': 'center',
          'width': '50px',
          'height': '50px',
        },
      })),

      ...graphData.edges
        .filter(e => filteredNodeIds.has(e.from_node) && filteredNodeIds.has(e.to_node))
        .map(edge => ({
          data: {
            id: `${edge.from_node}-${edge.to_node}`,
            source: edge.from_node,
            target: edge.to_node,
          },
          style: {
            'line-color': darkMode ? '#9ca3af' : '#d1d5db',
            'target-arrow-color': darkMode ? '#9ca3af' : '#d1d5db',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'width': 2,
          },
        })),
    ]

    return elements
  }

  const elements = data ? buildElements(data, filterEventType) : []

  // Cytoscape layout & style
  const layout = {
    name: 'dagre',
    directed: true,
    rankDir: 'LR' as const,
    align: 'UL' as const,
    padding: 10,
    spacingFactor: 1.2,
  }

  const stylesheet = [
    {
      selector: 'node',
      style: {
        'background-color': '#6b7280',
        'border-width': 2,
        'border-color': 'transparent',
        'font-size': '12px',
        'text-valign': 'center',
        'text-halign': 'center',
        'width': '50px',
        'height': '50px',
        'label': 'data(label)',
        'color': '#fff',
        'text-background-color': darkMode ? '#1f2937' : '#fff',
        'text-background-opacity': 0.8,
        'text-background-padding': '4px',
      },
    },
    {
      selector: 'edge',
      style: {
        'line-color': darkMode ? '#9ca3af' : '#d1d5db',
        'target-arrow-color': darkMode ? '#9ca3af' : '#d1d5db',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'width': 2,
      },
    },
    {
      selector: 'node:hover',
      style: {
        'border-width': 3,
        'border-color': darkMode ? '#fbbf24' : '#f59e0b',
        'box-shadow': darkMode
          ? '0 0 8px rgba(251, 191, 36, 0.5)'
          : '0 0 8px rgba(245, 158, 11, 0.5)',
      },
    },
  ]

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96 bg-background">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-accent mx-auto mb-4" />
          <p className="text-foreground">Loading audit graph...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 bg-destructive/10 rounded-lg border border-destructive/30">
        <h3 className="font-semibold text-destructive">Error Loading Graph</h3>
        <p className="text-sm mt-2 text-destructive">{error}</p>
      </div>
    )
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className="p-6 text-center bg-card rounded-lg">
        <p className="text-foreground/60">
          ℹ️ No audit events found. Audit chain will appear as events are recorded.
        </p>
      </div>
    )
  }

  const uniqueEventTypes = [...new Set(data.nodes.map(n => n.event_type))].sort()

  return (
    <div className="space-y-4 bg-background">
      {/* Header & Controls */}
      <div className="p-4 bg-card rounded-lg border border-border">
        <h3 className="font-semibold mb-3 text-foreground">
          Audit Chain DAG
        </h3>

        <div className="flex flex-wrap gap-4 items-center">
          {/* Filter by Event Type */}
          <div className="flex-1 min-w-xs">
            <label className="block text-xs font-medium mb-1 text-muted-foreground">
              Filter by Event Type
            </label>
            <select
              value={filterEventType}
              onChange={e => setFilterEventType(e.target.value)}
              className="w-full px-3 py-2 rounded text-sm bg-background border border-border text-foreground"
            >
              <option value="">All Events</option>
              {uniqueEventTypes.map(et => (
                <option key={et} value={et}>
                  {et}
                </option>
              ))}
            </select>
          </div>

          {/* Stats */}
          <div className="flex gap-4 text-xs">
            <div>
              <p className="text-muted-foreground">Total Events</p>
              <p className="font-semibold text-foreground">{data.total_events}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Nodes</p>
              <p className="font-semibold text-foreground">{data.nodes.length}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Edges</p>
              <p className="font-semibold text-foreground">{data.edges.length}</p>
            </div>
          </div>
        </div>

        {/* Critical Path */}
        {data.critical_path.length > 0 && (
          <div className="mt-3 p-2 rounded text-xs bg-accent/10 border border-accent/30">
            <p className="font-medium text-foreground">
              Critical Path: {data.critical_path.length} events
            </p>
            <p className="text-muted-foreground truncate" title={data.critical_path.join(' → ')}>
              {data.critical_path.slice(0, 3).join(' → ')} {data.critical_path.length > 3 ? '...' : ''}
            </p>
          </div>
        )}

        {/* Anomalies */}
        {data.anomalies.length > 0 && (
          <div className="mt-3 p-2 rounded text-xs bg-amber-500/10 border border-amber-500/30">
            <p className="font-medium text-amber-700 dark:text-amber-300">
              {data.anomalies.length} Anomaly/ies Detected
            </p>
            {data.anomalies.map((a, i) => (
              <p key={i} className="text-xs text-amber-700 dark:text-amber-300">
                • {a.type}: {a.message}
              </p>
            ))}
          </div>
        )}
      </div>

      {/* Graph Container */}
      <div className="relative w-full h-screen rounded-lg border overflow-hidden bg-background border-border">
        {elements.length > 0 ? (
          <>
            <CytoscapeComponent
              elements={elements}
              style={{ width: '100%', height: '100%', backgroundColor: darkMode ? '#0f172a' : '#f9fafb' }}
              layout={layout}
              stylesheet={stylesheet}
              cy={(cy: cytoscape.Core) => {
                cyRef.current = cy
                // Mouse events
                cy.on('mouseover', 'node', (e: cytoscape.EventObject) => {
                  const node = e.target
                  const rect = node.renderedBoundingBox()
                  const nodeData = data.nodes.find(n => n.id === node.id())
                  setTooltip({
                    visible: true,
                    x: rect.x1,
                    y: rect.y1,
                    nodeId: node.id(),
                    node: nodeData,
                  })
                })
                cy.on('mouseout', 'node', () => {
                  setTooltip({ visible: false, x: 0, y: 0, nodeId: '' })
                })
              }}
            />

            {/* Tooltip */}
            {tooltip.visible && tooltip.node && (
              <div
                className="absolute z-50 p-2 rounded shadow-lg text-xs pointer-events-none bg-card border border-border"
                style={{
                  left: `${tooltip.x}px`,
                  top: `${tooltip.y + 10}px`,
                  maxWidth: '200px',
                }}
              >
                <p className="font-semibold text-foreground">
                  {tooltip.node.event_type}
                </p>
                <p className="text-xs text-muted-foreground">
                  {new Date(tooltip.node.ts * 1000).toLocaleString()}
                </p>
                {tooltip.node.severity && (
                  <p className={`text-xs mt-1 px-1 rounded inline-block ${
                    tooltip.node.severity === 'ERROR' ? 'bg-destructive/15 text-destructive' : 'bg-accent/15 text-accent-foreground/90'
                  }`}>
                    {tooltip.node.severity}
                  </p>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="flex items-center justify-center h-full text-foreground/50">
            <p>No nodes match the selected filter</p>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="p-4 bg-card rounded-lg border border-border">
        <p className="text-xs font-semibold mb-2 text-foreground">Legend</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          {Object.entries(EVENT_TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-2">
              <div
                className="w-3 h-3 rounded-full"
                style={{ backgroundColor: color }}
              />
              <span className="text-muted-foreground">
                {type === 'default' ? 'Other' : type.split('.').pop()}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default AuditGraphPanel
