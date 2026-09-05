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
// @ts-ignore - react-cytoscapejs has no type definitions
import CytoscapeComponent from 'react-cytoscapejs'

interface GraphNode {
  id: string
  event_type: string
  ts: number
  severity: string
  run_id: string
  details: Record<string, any>
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
  const cyRef = useRef(null)

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
      <div className={`flex items-center justify-center h-96 ${darkMode ? 'bg-background' : 'bg-background-light'}`}>
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4" />
          <p className={`${darkMode ? 'text-foreground' : 'text-foreground-light'}`}>Loading audit graph...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className={`p-6 ${darkMode ? 'bg-red-950' : 'bg-red-50'} rounded-lg border ${darkMode ? 'border-red-800' : 'border-red-200'}`}>
        <h3 className={`font-semibold ${darkMode ? 'text-red-200' : 'text-red-900'}`}>Error Loading Graph</h3>
        <p className={`text-sm mt-2 ${darkMode ? 'text-red-300' : 'text-red-800'}`}>{error}</p>
      </div>
    )
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className={`p-6 text-center ${darkMode ? 'bg-card' : 'bg-card-light'} rounded-lg`}>
        <p className={`${darkMode ? 'text-foreground/60' : 'text-foreground-light/60'}`}>
          ℹ️ No audit events found. Audit chain will appear as events are recorded.
        </p>
      </div>
    )
  }

  const uniqueEventTypes = [...new Set(data.nodes.map(n => n.event_type))].sort()

  return (
    <div className={`space-y-4 ${darkMode ? 'bg-background' : 'bg-background-light'}`}>
      {/* Header & Controls */}
      <div className={`p-4 ${darkMode ? 'bg-card' : 'bg-card-light'} rounded-lg border ${darkMode ? 'border-border' : 'border-border-light'}`}>
        <h3 className={`font-semibold mb-3 ${darkMode ? 'text-foreground' : 'text-foreground-light'}`}>
          Audit Chain DAG
        </h3>

        <div className="flex flex-wrap gap-4 items-center">
          {/* Filter by Event Type */}
          <div className="flex-1 min-w-xs">
            <label className={`block text-xs font-medium mb-1 ${darkMode ? 'text-foreground/70' : 'text-foreground-light/70'}`}>
              Filter by Event Type
            </label>
            <select
              value={filterEventType}
              onChange={e => setFilterEventType(e.target.value)}
              className={`w-full px-3 py-2 rounded text-sm ${
                darkMode
                  ? 'bg-background border-border text-foreground'
                  : 'bg-background-light border-border-light text-foreground-light'
              } border`}
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
              <p className={`${darkMode ? 'text-foreground/70' : 'text-foreground-light/70'}`}>Total Events</p>
              <p className={`font-semibold ${darkMode ? 'text-foreground' : 'text-foreground-light'}`}>{data.total_events}</p>
            </div>
            <div>
              <p className={`${darkMode ? 'text-foreground/70' : 'text-foreground-light/70'}`}>Nodes</p>
              <p className={`font-semibold ${darkMode ? 'text-foreground' : 'text-foreground-light'}`}>{data.nodes.length}</p>
            </div>
            <div>
              <p className={`${darkMode ? 'text-foreground/70' : 'text-foreground-light/70'}`}>Edges</p>
              <p className={`font-semibold ${darkMode ? 'text-foreground' : 'text-foreground-light'}`}>{data.edges.length}</p>
            </div>
          </div>
        </div>

        {/* Critical Path */}
        {data.critical_path.length > 0 && (
          <div className={`mt-3 p-2 rounded text-xs ${darkMode ? 'bg-blue-950' : 'bg-blue-50'} border ${darkMode ? 'border-blue-800' : 'border-blue-200'}`}>
            <p className={`font-medium ${darkMode ? 'text-blue-200' : 'text-blue-900'}`}>
              Critical Path: {data.critical_path.length} events
            </p>
            <p className={`${darkMode ? 'text-blue-300' : 'text-blue-800'} truncate`} title={data.critical_path.join(' → ')}>
              {data.critical_path.slice(0, 3).join(' → ')} {data.critical_path.length > 3 ? '...' : ''}
            </p>
          </div>
        )}

        {/* Anomalies */}
        {data.anomalies.length > 0 && (
          <div className={`mt-3 p-2 rounded text-xs ${darkMode ? 'bg-yellow-950' : 'bg-yellow-50'} border ${darkMode ? 'border-yellow-800' : 'border-yellow-200'}`}>
            <p className={`font-medium ${darkMode ? 'text-yellow-200' : 'text-yellow-900'}`}>
              {data.anomalies.length} Anomaly/ies Detected
            </p>
            {data.anomalies.map((a, i) => (
              <p key={i} className={`text-xs ${darkMode ? 'text-yellow-300' : 'text-yellow-800'}`}>
                • {a.type}: {a.message}
              </p>
            ))}
          </div>
        )}
      </div>

      {/* Graph Container */}
      <div
        className={`relative w-full h-screen rounded-lg border overflow-hidden ${
          darkMode
            ? 'bg-background border-border'
            : 'bg-card-light border-border-light'
        }`}
      >
        {elements.length > 0 ? (
          <>
            <CytoscapeComponent
              elements={elements}
              style={{ width: '100%', height: '100%', backgroundColor: darkMode ? '#0f172a' : '#f9fafb' }}
              layout={layout}
              stylesheet={stylesheet}
              cy={(cy: any) => {
                cyRef.current = cy
                // Mouse events
                cy.on('mouseover', 'node', (e: any) => {
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
                className={`absolute z-50 p-2 rounded shadow-lg text-xs pointer-events-none ${
                  darkMode ? 'bg-card border-border' : 'bg-card-light border-border-light'
                } border`}
                style={{
                  left: `${tooltip.x}px`,
                  top: `${tooltip.y + 10}px`,
                  maxWidth: '200px',
                }}
              >
                <p className={`font-semibold ${darkMode ? 'text-foreground' : 'text-foreground-light'}`}>
                  {tooltip.node.event_type}
                </p>
                <p className={`text-xs ${darkMode ? 'text-foreground/70' : 'text-foreground-light/70'}`}>
                  {new Date(tooltip.node.ts * 1000).toLocaleString()}
                </p>
                {tooltip.node.severity && (
                  <p className={`text-xs mt-1 px-1 rounded inline-block ${
                    tooltip.node.severity === 'ERROR' ? 'bg-red-900 text-red-200' : 'bg-blue-900 text-blue-200'
                  }`}>
                    {tooltip.node.severity}
                  </p>
                )}
              </div>
            )}
          </>
        ) : (
          <div className={`flex items-center justify-center h-full ${darkMode ? 'text-foreground/50' : 'text-foreground-light/50'}`}>
            <p>No nodes match the selected filter</p>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className={`p-4 ${darkMode ? 'bg-card' : 'bg-card-light'} rounded-lg border ${darkMode ? 'border-border' : 'border-border-light'}`}>
        <p className={`text-xs font-semibold mb-2 ${darkMode ? 'text-foreground' : 'text-foreground-light'}`}>Legend</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          {Object.entries(EVENT_TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-2">
              <div
                className="w-3 h-3 rounded-full"
                style={{ backgroundColor: color }}
              />
              <span className={darkMode ? 'text-foreground/70' : 'text-foreground-light/70'}>
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
