'use client';

/**
 * FlowGraphView — vis-network DAG for a CorvinFlow FlowRun (ADR-0121 M4).
 *
 * Navigation:
 *   Toolbar  — zoom-in (+), zoom-out (−), fit-all (⊡) buttons
 *   Keyboard — +/= zoom in · − zoom out · F fit all · Esc deselect
 *   Click    — select step node → detail panel at bottom
 *   Scroll   — native vis-network zoom
 *   Drag     — pan the canvas
 *
 * Node colours:
 *   Gray    — not yet reached
 *   Blue    — dispatched (running)
 *   Green   — completed
 *   Yellow  — paused at checkpoint
 *   Red     — budget_exceeded / failed
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Network, Options, Node as VisNode, Edge as VisEdge } from 'vis-network/standalone';

export interface FlowStep {
  id: string;
  node: string;           // "local" or A2A endpoint id
  depends_on: string[];
  checkpoint?: string;    // "human_approval" | undefined
}

export interface FlowEvent {
  type: string;
  ts: number;
  step_id?: string;
  output_sha256_prefix?: string;
  reason?: string;
  budget_after?: Record<string, number>;
}

interface Props {
  steps: FlowStep[];
  events: FlowEvent[];
  runStatus: string;
  onApprove?: (pausedStepId: string) => void;
  approving?: boolean;
}

// ── Status derivation ─────────────────────────────────────────────────────────

type StepStatus = 'pending' | 'running' | 'completed' | 'paused' | 'failed';

function deriveStepStatuses(steps: FlowStep[], events: FlowEvent[]): Map<string, StepStatus> {
  const statuses = new Map<string, StepStatus>(steps.map((s) => [s.id, 'pending']));
  for (const ev of events) {
    if (!ev.step_id) continue;
    if (ev.type === 'mesh_flow.step_dispatched')  statuses.set(ev.step_id, 'running');
    if (ev.type === 'mesh_flow.step_completed')   statuses.set(ev.step_id, 'completed');
    if (ev.type === 'mesh_flow.budget_exceeded')  statuses.set(ev.step_id, 'failed');
    if (ev.type === 'mesh_flow.checkpoint_paused') statuses.set(ev.step_id, 'paused');
  }
  return statuses;
}

function pausedStepId(events: FlowEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === 'mesh_flow.checkpoint_paused') return events[i].step_id ?? null;
  }
  return null;
}

// ── Node styling ──────────────────────────────────────────────────────────────

const STATUS_NODE: Record<StepStatus, { color: string; border: string; font: string }> = {
  pending:   { color: '#21262d', border: '#484f58', font: '#6e7681' },
  running:   { color: '#0d419d', border: '#388bfd', font: '#79c0ff' },
  completed: { color: '#0a3622', border: '#238636', font: '#3fb950' },
  paused:    { color: '#2e1a00', border: '#e3b341', font: '#e3b341' },
  failed:    { color: '#3d0c0c', border: '#f85149', font: '#f85149' },
};

function nodeLabel(step: FlowStep, status: StepStatus): string {
  const icon =
    status === 'completed' ? '✓ ' :
    status === 'running'   ? '→ ' :
    status === 'paused'    ? '⏸ ' :
    status === 'failed'    ? '✗ ' : '○ ';
  const nodeTag = step.node === 'local' ? ' 🖥' : ' ⚡';
  return `${icon}${step.id}${nodeTag}`;
}

function checkpointLabel(status: StepStatus): string {
  return status === 'paused' ? '⏸ human_approval\nWaiting for operator' : '⏸ human_approval';
}

// ── Toolbar button ────────────────────────────────────────────────────────────

function ToolBtn({
  onClick,
  title,
  children,
  active,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`flex h-7 w-7 items-center justify-center rounded text-xs font-medium transition-colors ${
        active
          ? 'bg-blue-600 text-white'
          : 'bg-[#21262d] text-gray-400 hover:bg-[#30363d] hover:text-gray-200'
      }`}
    >
      {children}
    </button>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function FlowGraphView({ steps, events, runStatus, onApprove, approving }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef   = useRef<Network | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const statuses  = deriveStepStatuses(steps, events);
  const pausedSid = pausedStepId(events);

  // ── Zoom helpers ──────────────────────────────────────────────────────────

  const zoomIn = useCallback(() => {
    if (!networkRef.current) return;
    const s = networkRef.current.getScale();
    networkRef.current.moveTo({ scale: s * 1.3, animation: { duration: 200, easingFunction: 'easeInOutQuad' } });
  }, []);

  const zoomOut = useCallback(() => {
    if (!networkRef.current) return;
    const s = networkRef.current.getScale();
    networkRef.current.moveTo({ scale: s / 1.3, animation: { duration: 200, easingFunction: 'easeInOutQuad' } });
  }, []);

  const fitAll = useCallback(() => {
    networkRef.current?.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
  }, []);

  // ── Graph init ────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!containerRef.current || steps.length === 0) return;
    let destroyed = false;

    import('vis-network/standalone').then(({ Network, DataSet }) => {
      if (destroyed || !containerRef.current) return;

      const nodes: VisNode[] = [];
      const edges: VisEdge[] = [];

      // START sentinel
      nodes.push({
        id: '__start__', label: '▶  START', shape: 'ellipse',
        color: { background: '#21262d', border: '#388bfd' },
        font: { color: '#388bfd', size: 12 }, mass: 1,
      });

      for (const step of steps) {
        const status = statuses.get(step.id) ?? 'pending';
        const style  = STATUS_NODE[status];
        const isCheckpoint = step.checkpoint === 'human_approval';

        if (isCheckpoint) {
          nodes.push({
            id: `__cp_${step.id}`, label: checkpointLabel(status), shape: 'diamond', size: 28,
            color: { background: style.color, border: style.border },
            font: { color: style.font, size: 10, multi: 'html' },
            title: `Checkpoint: human_approval\nStep: ${step.id}\nStatus: ${status}`,
          });
          nodes.push({
            id: step.id, label: nodeLabel(step, status), shape: 'box',
            color: { background: style.color, border: style.border, highlight: { background: style.color, border: '#ffffff' } },
            font: { color: style.font, size: 11 },
            title: `Step: ${step.id}\nNode: ${step.node}\nStatus: ${status}`,
          });
          edges.push({
            id: `__cp_${step.id}__step`, from: `__cp_${step.id}`, to: step.id,
            dashes: status === 'paused',
            color: { color: style.border, opacity: 0.5 },
            arrows: { to: { enabled: true, scaleFactor: 0.6 } },
          });
        } else {
          nodes.push({
            id: step.id, label: nodeLabel(step, status), shape: 'box',
            color: { background: style.color, border: style.border, highlight: { background: style.color, border: '#ffffff' } },
            font: { color: style.font, size: 11 },
            title: `Step: ${step.id}\nNode: ${step.node}\nStatus: ${status}${step.depends_on.length ? '\nDeps: ' + step.depends_on.join(', ') : ''}`,
          });
        }
      }

      // END sentinel
      nodes.push({
        id: '__end__', label: '■  END', shape: 'ellipse',
        color: {
          background: '#21262d',
          border: runStatus === 'completed' ? '#238636' : '#484f58',
        },
        font: { color: runStatus === 'completed' ? '#3fb950' : '#484f58', size: 12 },
        mass: 1,
      });

      // Root steps → __start__
      for (const step of steps) {
        if (step.depends_on.length === 0) {
          const target = step.checkpoint === 'human_approval' ? `__cp_${step.id}` : step.id;
          edges.push({
            from: '__start__', to: target,
            arrows: { to: { enabled: true, scaleFactor: 0.6 } },
            color: { color: '#388bfd', opacity: 0.4 },
          });
        }
      }

      // Dependency edges
      for (const step of steps) {
        for (const dep of step.depends_on) {
          const depStatus = statuses.get(dep) ?? 'pending';
          const target = step.checkpoint === 'human_approval' ? `__cp_${step.id}` : step.id;
          edges.push({
            from: dep, to: target,
            arrows: { to: { enabled: true, scaleFactor: 0.6 } },
            dashes: depStatus !== 'completed',
            color: { color: depStatus === 'completed' ? '#238636' : '#484f58', opacity: 0.7 },
          });
        }
      }

      // Leaf steps → __end__
      const hasSuccessors = new Set(steps.flatMap((s) => s.depends_on));
      for (const step of steps) {
        if (!hasSuccessors.has(step.id)) {
          const status = statuses.get(step.id) ?? 'pending';
          edges.push({
            from: step.id, to: '__end__',
            arrows: { to: { enabled: true, scaleFactor: 0.6 } },
            dashes: status !== 'completed',
            color: { color: status === 'completed' ? '#238636' : '#484f58', opacity: 0.4 },
          });
        }
      }

      const options: Options = {
        layout: {
          hierarchical: {
            enabled: true, direction: 'UD', sortMethod: 'directed',
            nodeSpacing: 160, levelSeparation: 110,
          },
        },
        physics: { enabled: false },
        interaction: { hover: true, tooltipDelay: 100, zoomView: true, dragView: true },
        nodes: {
          borderWidth: 2, borderWidthSelected: 3,
          margin: { top: 8, bottom: 8, left: 12, right: 12 },
        },
        edges: {
          smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.4 },
          width: 1.5, selectionWidth: 2.5,
        },
      };

      const network = new Network(containerRef.current!, { nodes: new DataSet(nodes), edges: new DataSet(edges) }, options);
      networkRef.current = network;

      // Node selection → step detail panel
      network.on('selectNode', ({ nodes: sel }: { nodes: string[] }) => {
        const id = sel[0] ?? null;
        if (id && !id.startsWith('__')) setSelectedId(id);
        else setSelectedId(null);
      });
      network.on('deselectNode', () => setSelectedId(null));

      network.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
    });

    return () => {
      destroyed = true;
      networkRef.current?.destroy();
      networkRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(steps), JSON.stringify(events), runStatus]);

  // ── Keyboard shortcuts ────────────────────────────────────────────────────

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!networkRef.current) return;
      // Don't steal keys from inputs
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomIn(); }
      if (e.key === '-')                  { e.preventDefault(); zoomOut(); }
      if (e.key === 'f' || e.key === 'F') { e.preventDefault(); fitAll(); }
      if (e.key === 'Escape')             { setSelectedId(null); networkRef.current?.unselectAll(); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [zoomIn, zoomOut, fitAll]);

  // ── Derived detail ────────────────────────────────────────────────────────

  const selectedStep   = selectedId ? steps.find((s) => s.id === selectedId) ?? null : null;
  const selectedStatus = selectedId ? (statuses.get(selectedId) ?? 'pending') : null;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="relative w-full select-none" style={{ height: '480px' }}>
      {/* vis-network canvas */}
      <div ref={containerRef} className="w-full h-full rounded-lg bg-[#0d1117] border border-[#30363d]" />

      {/* Zoom toolbar — top left */}
      <div className="absolute top-2 left-2 z-10 flex gap-1">
        <ToolBtn onClick={zoomIn}  title="Zoom in (+)">＋</ToolBtn>
        <ToolBtn onClick={zoomOut} title="Zoom out (−)">－</ToolBtn>
        <ToolBtn onClick={fitAll}  title="Fit all (F)">⊡</ToolBtn>
      </div>

      {/* Keyboard hint */}
      <div className="absolute bottom-2 right-2 z-10 text-[9px] text-gray-600 pointer-events-none">
        +/− zoom · F fit · click node for details
      </div>

      {/* Human-approval overlay */}
      {runStatus === 'paused' && pausedSid && (
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10">
          <div className="flex items-center gap-3 rounded-lg border border-yellow-600/40 bg-yellow-900/30 px-4 py-2.5 shadow-lg">
            <span className="text-yellow-400 text-sm font-medium">
              ⏸ Paused: <span className="font-mono">{pausedSid}</span>
            </span>
            {onApprove && (
              <button
                onClick={() => onApprove(pausedSid)}
                disabled={approving}
                className="rounded bg-yellow-500 px-3 py-1 text-xs font-semibold text-black hover:bg-yellow-400 disabled:opacity-50 transition-colors"
              >
                {approving ? 'Approving…' : '▶ Approve'}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Step detail panel — bottom overlay, shown on node click */}
      {selectedStep && selectedStatus && (
        <div className="absolute bottom-6 left-2 right-2 z-10 rounded-lg border border-[#30363d] bg-[#161b22]/95 px-3 py-2 backdrop-blur-sm">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-semibold text-gray-200">{selectedStep.id}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  selectedStatus === 'completed' ? 'bg-green-900/50 text-green-400' :
                  selectedStatus === 'running'   ? 'bg-blue-900/50  text-blue-400'  :
                  selectedStatus === 'paused'    ? 'bg-yellow-900/50 text-yellow-400':
                  selectedStatus === 'failed'    ? 'bg-red-900/50   text-red-400'   :
                  'bg-gray-800 text-gray-500'
                }`}>{selectedStatus}</span>
                <span className="text-[10px] text-gray-500">
                  {selectedStep.node === 'local' ? '🖥 local' : `⚡ ${selectedStep.node}`}
                </span>
                {selectedStep.checkpoint && (
                  <span className="text-[10px] text-yellow-600">⏸ {selectedStep.checkpoint}</span>
                )}
              </div>
              {selectedStep.depends_on.length > 0 && (
                <div className="mt-0.5 text-[10px] text-gray-500">
                  Deps: <span className="font-mono text-gray-400">{selectedStep.depends_on.join(', ')}</span>
                </div>
              )}
            </div>
            <button
              onClick={() => { setSelectedId(null); networkRef.current?.unselectAll(); }}
              className="shrink-0 text-xs text-gray-600 hover:text-gray-400 transition-colors"
              title="Close (Esc)"
            >✕</button>
          </div>
        </div>
      )}

      {/* Legend strip — top right */}
      <div className="absolute top-2 right-2 z-10 flex gap-2 flex-wrap">
        {[
          { color: '#484f58', label: 'pending' },
          { color: '#388bfd', label: 'running' },
          { color: '#238636', label: 'done' },
          { color: '#e3b341', label: 'paused' },
          { color: '#f85149', label: 'failed' },
        ].map(({ color, label }) => (
          <span key={label} className="flex items-center gap-1 text-[10px] text-gray-400">
            <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
            {label}
          </span>
        ))}
        <span className="text-[10px] text-gray-500 ml-1">🖥 local · ⚡ A2A</span>
      </div>
    </div>
  );
}
