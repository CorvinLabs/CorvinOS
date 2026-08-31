'use client';

/**
 * CorvinFlow FlowRun console — ADR-0121 M4.
 *
 * Two views for the selected FlowRun:
 *   Timeline  — chronological audit event list (original view)
 *   Graph     — vis-network DAG with live step statuses + Human-in-the-Loop
 *
 * The Graph view fetches the flow definition (step ids, depends_on, node,
 * checkpoint) from GET /flows/definition/{flow_id} and overlays execution
 * status derived from the run's manifest events.
 */

import { useEffect, useState, lazy, Suspense } from 'react';
import { FlowCreatorPanel } from '@/components/FlowCreatorPanel';
import { useAuth } from '@/lib/auth';

const FlowGraphView = lazy(() => import('./FlowGraphView'));

interface FlowRun {
  run_id: string;
  flow_id: string | null;
  flow_version: string | null;
  status: string;
  started_at: number | null;
  completed_at: number | null;
  steps_done: number | null;
  paused_at_step: string | null;
  event_count: number;
}

interface FlowEvent {
  type: string;
  ts: number;
  step_id?: string;
  target_node?: string;
  output_sha256_prefix?: string;
  reason?: string;
  steps_done?: number;
}

interface FlowStep {
  id: string;
  node: string;
  depends_on: string[];
  checkpoint?: string;
}

const STATUS_COLORS: Record<string, string> = {
  completed:      'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  paused:         'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  budget_exceeded:'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  running:        'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
};

const EVENT_ICONS: Record<string, string> = {
  'mesh_flow.run_started':       '▶',
  'mesh_flow.step_dispatched':   '→',
  'mesh_flow.step_completed':    '✓',
  'mesh_flow.budget_checkpoint': '·',
  'mesh_flow.budget_exceeded':   '✗',
  'mesh_flow.checkpoint_paused': '⏸',
  'mesh_flow.checkpoint_resumed':'▶',
  'mesh_flow.run_paused':        '⏸',
  'mesh_flow.run_completed':     '■',
  'mesh_flow.run_cancelled':     '✗',
};

function fmtTs(ts: number | null): string {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString();
}

function fmtDuration(start: number | null, end: number | null): string {
  if (!start || !end) return '—';
  const ms = Math.round((end - start) * 1000);
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

type DetailView = 'timeline' | 'graph' | 'results';

// ── Results Panel ──────────────────────────────────────────────────────────────

function ResultsPanel({
  runId,
  outputs,
  events,
  onRefresh,
}: {
  runId: string;
  outputs: Record<string, string>;
  events: FlowEvent[];
  onRefresh: () => void;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Derive step order from step_completed events in the timeline
  const completedSteps = events
    .filter(e => e.type === 'mesh_flow.step_completed' && e.step_id)
    .map(e => e.step_id as string);

  const outputEntries = completedSteps.length > 0
    ? completedSteps.filter(sid => outputs[sid] !== undefined)
    : Object.keys(outputs);

  if (outputEntries.length === 0) {
    const isRunning = events.some(
      e => e.type === 'mesh_flow.step_dispatched' && !events.some(
        c => c.type === 'mesh_flow.step_completed' && c.step_id === e.step_id
      )
    );
    return (
      <div className="py-8 text-center space-y-3">
        <div className="text-3xl select-none">⬡</div>
        <p className="text-sm text-muted-foreground">
          {isRunning ? 'Steps are running — results appear here as each step completes.' : 'No step outputs yet.'}
        </p>
        <button
          onClick={onRefresh}
          className="text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors text-muted-foreground"
        >
          ↺ Refresh
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{outputEntries.length} step{outputEntries.length !== 1 ? 's' : ''} completed</span>
        <button
          onClick={onRefresh}
          className="text-xs px-2 py-1 rounded border border-border hover:bg-muted transition-colors text-muted-foreground"
        >
          ↺
        </button>
      </div>
      {outputEntries.map((stepId, idx) => {
        const output = outputs[stepId] ?? '';
        const truncated = output.length > 800;
        const preview = truncated ? output.slice(0, 800) : output;
        const isExpanded = expanded[stepId] ?? false;
        const stepEvent = events.find(e => e.type === 'mesh_flow.step_completed' && e.step_id === stepId);
        const dispatchEvent = events.find(e => e.type === 'mesh_flow.step_dispatched' && e.step_id === stepId);

        return (
          <div key={stepId} className="rounded-lg border border-border bg-card overflow-hidden">
            {/* Step header */}
            <div className="flex items-center justify-between px-4 py-2.5 bg-muted/40 border-b border-border">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono font-semibold text-foreground">
                  {idx + 1}. {stepId}
                </span>
                {dispatchEvent?.target_node && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted border border-border text-muted-foreground font-mono">
                    {dispatchEvent.target_node}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                {stepEvent && (
                  <span>{fmtTs(stepEvent.ts)}</span>
                )}
                <span className="text-green-600 dark:text-green-400 font-medium">✓ done</span>
              </div>
            </div>

            {/* Output body */}
            <div className="px-4 py-3">
              {output.trim() === '' ? (
                <span className="text-xs text-muted-foreground italic">Empty output</span>
              ) : (
                <>
                  <pre className="text-xs font-mono whitespace-pre-wrap break-words text-foreground leading-relaxed max-h-64 overflow-y-auto">
                    {isExpanded ? output : preview}
                  </pre>
                  {truncated && (
                    <button
                      onClick={() => setExpanded(e => ({ ...e, [stepId]: !isExpanded }))}
                      className="mt-2 text-xs text-primary hover:underline"
                    >
                      {isExpanded ? '▲ Show less' : `▼ Show full (${(output.length / 1024).toFixed(1)} KB)`}
                    </button>
                  )}
                  {stepEvent?.output_sha256_prefix && (
                    <div className="mt-2 text-[10px] text-muted-foreground/60 font-mono">
                      sha256: {stepEvent.output_sha256_prefix}…
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        );
      })}

      {/* Run ID footer */}
      <div className="text-[10px] text-muted-foreground/50 font-mono pt-1">
        run: {runId}
      </div>
    </div>
  );
}

function FlowsEmptyState({ onNewFlow }: { onNewFlow?: () => void }) {
  return (
    <div className="rounded-xl border border-border bg-card p-5 space-y-4">
      <div>
        <h3 className="font-semibold text-sm text-foreground mb-1">No flows yet</h3>
        <p className="text-xs text-muted-foreground leading-relaxed">
          CorvinFlow orchestrates multi-step AI pipelines across agents — with human-approval
          checkpoints, budget gates, and a live DAG view.
        </p>
      </div>

      <div className="space-y-1.5">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Get started in 3 steps</p>
        <div className="space-y-2 text-sm">
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 flex-shrink-0 w-5 h-5 rounded-full border border-border bg-muted text-xs flex items-center justify-center text-muted-foreground font-medium">1</span>
            <span className="text-foreground text-xs">
              Create a flow definition under{' '}
              <code className="font-mono bg-muted border border-border rounded px-1 py-0.5">.corvin/flows/my-flow.yaml</code>
            </span>
          </div>
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 flex-shrink-0 w-5 h-5 rounded-full border border-border bg-muted text-xs flex items-center justify-center text-muted-foreground font-medium">2</span>
            <span className="text-foreground text-xs">
              Trigger via chat:{' '}
              <code className="font-mono bg-muted border border-border rounded px-1 py-0.5">/mesh-flow run my-flow</code>
            </span>
          </div>
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 flex-shrink-0 w-5 h-5 rounded-full border border-border bg-muted text-xs flex items-center justify-center text-muted-foreground font-medium">3</span>
            <span className="text-foreground text-xs">
              Approve human-in-the-loop checkpoints in this panel in real time
            </span>
          </div>
        </div>
      </div>

      <div className="rounded-lg bg-muted border border-border p-3">
        <p className="text-xs font-semibold text-muted-foreground mb-2">Example: summarise-and-post.yaml</p>
        <pre className="text-xs font-mono text-foreground leading-relaxed overflow-x-auto">{`flow_id: summarise-and-post
budget_tokens: 50000
steps:
  - id: fetch
    node: delegate_claude_code
    prompt: "Fetch latest docs and summarise"
  - id: post
    node: delegate_hermes
    depends_on: [fetch]
    checkpoint: human    # pauses here for approval
    prompt: "Post summary to Discord"`}</pre>
      </div>

      {onNewFlow && (
        <button
          data-testid="btn-new-flow-empty"
          onClick={() => { if (onNewFlow) onNewFlow(); }}
          className="w-full mt-2 text-sm px-4 py-2 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity font-medium"
        >
          + Create your first flow
        </button>
      )}
    </div>
  );
}

interface FlowDefinition {
  flow_id: string;
  version: string;
  step_count: number;
  mtime: number;
}

export default function FlowsPage() {
  const { session } = useAuth();
  const [runs, setRuns]               = useState<FlowRun[]>([]);
  const [definitions, setDefinitions] = useState<FlowDefinition[]>([]);
  const [selected, setSelected]       = useState<string | null>(null);
  const [events, setEvents]           = useState<FlowEvent[]>([]);
  const [steps, setSteps]             = useState<FlowStep[]>([]);
  const [loading, setLoading]         = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError]             = useState<string | null>(null);
  const [approving, setApproving]     = useState(false);
  const [triggering, setTriggering]   = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [view, setView]               = useState<DetailView>('timeline');
  const [outputs, setOutputs]         = useState<Record<string, string>>({});
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [editFlowId, setEditFlowId]   = useState<string | undefined>(undefined);

  // ── Fetch helpers ──────────────────────────────────────────────────────────

  const fetchRuns = async () => {
    try {
      setLoading(true);
      const [runsResp, defsResp] = await Promise.all([
        fetch('/v1/console/flows/runs', { credentials: 'include' }),
        fetch('/v1/console/flows/definitions', { credentials: 'include' }),
      ]);
      if (!runsResp.ok) throw new Error(`HTTP ${runsResp.status}`);
      const runsData = (await runsResp.json()) as { runs: FlowRun[] };
      setRuns(runsData.runs);
      if (defsResp.ok) {
        const defsData = (await defsResp.json()) as { definitions: FlowDefinition[] };
        setDefinitions(defsData.definitions);
      }
      setError(null);
    } catch (err) {
      setError(`Failed to load flows: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const fetchDetail = async (runId: string) => {
    try {
      setDetailLoading(true);
      const resp = await fetch(`/v1/console/flows/runs/${runId}`, { credentials: 'include' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as { events: FlowEvent[] };
      setEvents(data.events);
    } catch {
      setEvents([]);
    } finally {
      setDetailLoading(false);
    }
  };

  const fetchDefinition = async (flowId: string) => {
    try {
      const resp = await fetch(`/v1/console/flows/definition/${flowId}`, { credentials: 'include' });
      if (!resp.ok) return;
      const data = (await resp.json()) as { steps: FlowStep[] };
      setSteps(data.steps ?? []);
    } catch {
      setSteps([]);
    }
  };

  const approveCheckpoint = async (runId: string) => {
    setApproving(true);
    try {
      const resp = await fetch(`/v1/console/flows/runs/${runId}/approve`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': session?.csrf_token ?? '' },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await fetchRuns();
      await fetchDetail(runId);
    } catch (err) {
      setError(`Approval failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setApproving(false);
    }
  };

  const fetchOutputs = async (runId: string) => {
    try {
      const resp = await fetch(`/v1/console/flows/runs/${runId}/outputs`, { credentials: 'include' });
      if (!resp.ok) return;
      const data = (await resp.json()) as { outputs: Record<string, string> };
      setOutputs(data.outputs ?? {});
    } catch {
      setOutputs({});
    }
  };

  const triggerFlow = async (flowId: string) => {
    setTriggering(flowId);
    setError(null);
    try {
      const resp = await fetch(`/v1/console/flows/trigger/${flowId}`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': session?.csrf_token ?? '',
        },
        body: JSON.stringify({}),
      });
      if (!resp.ok) {
        const data = (await resp.json().catch(() => ({}))) as { detail?: string };
        throw new Error(data.detail ?? `HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as { run_id: string };
      await fetchRuns();
      setSelected(data.run_id);
      setView('timeline');
    } catch (err) {
      setError(`Failed to start flow: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setTriggering(null);
    }
  };

  const deleteDefinition = async (flowId: string) => {
    setError(null);
    try {
      const resp = await fetch(`/v1/console/flows/definition/${flowId}`, {
        method: 'DELETE',
        credentials: 'include',
        headers: { 'X-CSRF-Token': session?.csrf_token ?? '' },
      });
      if (!resp.ok) {
        const data = (await resp.json().catch(() => ({}))) as { detail?: string };
        throw new Error(data.detail ?? `HTTP ${resp.status}`);
      }
      setDeleteConfirm(null);
      await fetchRuns();
    } catch (err) {
      setError(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
      setDeleteConfirm(null);
    }
  };

  // ── Effects ────────────────────────────────────────────────────────────────

  useEffect(() => {
    void fetchRuns();
    const interval = setInterval(() => void fetchRuns(), 5000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!selected) return;
    setEvents([]);
    setSteps([]);
    void fetchDetail(selected);
  }, [selected]);

  // Fetch definition when switching to graph view
  useEffect(() => {
    if (view !== 'graph') return;
    const run = runs.find((r) => r.run_id === selected);
    if (run?.flow_id) void fetchDefinition(run.flow_id);
  }, [view, selected, runs]);

  // Fetch outputs when switching to results view (or when selected run changes in results view)
  useEffect(() => {
    if (view !== 'results' || !selected) return;
    void fetchOutputs(selected);
  }, [view, selected]);

  // ── Derived state ──────────────────────────────────────────────────────────

  const selectedRun = runs.find((r) => r.run_id === selected);

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <>
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Pipelines</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Multi-step AI pipelines with human-approval checkpoints.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            data-testid="btn-new-flow"
            onClick={() => { setEditFlowId(undefined); setCreatorOpen(true); }}
            className="text-sm px-4 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity font-medium"
          >
            + New flow
          </button>
          <button
            onClick={() => void fetchRuns()}
            className="text-sm px-3 py-1 rounded border border-border hover:bg-muted transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm dark:bg-red-900/20 dark:border-red-800 dark:text-red-400">
          {error}
        </div>
      )}

      <div className="flex gap-6">
        {/* ── Run list ── */}
        <div className="w-72 flex-shrink-0">
          {/* ── Definitions (never-run flows) ── */}
          {definitions.length > 0 && (
            <div className="mb-5">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
                Saved Definitions
              </h2>
              <ul className="space-y-2">
                {definitions.map((def) => {
                  const hasRun = runs.some((r) => r.flow_id === def.flow_id);
                  return (
                    <li
                      key={def.flow_id}
                      className="p-3 rounded-lg border border-border bg-card"
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-medium text-foreground truncate">{def.flow_id}</span>
                        <span className="text-xs text-muted-foreground">v{def.version}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-muted-foreground">
                          {def.step_count} step{def.step_count !== 1 ? 's' : ''}
                          {' · '}
                          {new Date(def.mtime * 1000).toLocaleDateString()}
                        </span>
                        {!hasRun && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground border border-border">
                            never run
                          </span>
                        )}
                      </div>
                      {deleteConfirm === def.flow_id ? (
                        <div className="mt-2 flex gap-1">
                          <span className="text-xs text-red-600 flex-1">Delete &ldquo;{def.flow_id}&rdquo;?</span>
                          <button
                            onClick={() => void deleteDefinition(def.flow_id)}
                            className="text-xs px-2 py-0.5 rounded bg-red-600 text-white hover:bg-red-700 transition-colors"
                          >
                            Yes
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(null)}
                            className="text-xs px-2 py-0.5 rounded border border-border hover:bg-muted transition-colors text-muted-foreground"
                          >
                            No
                          </button>
                        </div>
                      ) : (
                        <div className="mt-2 flex gap-1">
                          <button
                            onClick={() => void triggerFlow(def.flow_id)}
                            disabled={triggering === def.flow_id}
                            className="flex-1 text-xs px-2 py-1 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50 font-medium"
                            title="Start a new run of this flow"
                          >
                            {triggering === def.flow_id ? '…' : '▶ Run'}
                          </button>
                          <button
                            onClick={() => {
                              setEditFlowId(def.flow_id);
                              setCreatorOpen(true);
                            }}
                            className="text-xs px-2 py-1 rounded border border-border hover:bg-muted transition-colors text-muted-foreground"
                            title="Edit this flow definition"
                          >
                            ✎
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(def.flow_id)}
                            className="text-xs px-2 py-1 rounded border border-border hover:bg-red-50 hover:border-red-200 hover:text-red-600 transition-colors text-muted-foreground dark:hover:bg-red-950/30"
                            title="Delete this flow definition"
                          >
                            🗑
                          </button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">
            Recent Runs
          </h2>
          {loading && runs.length === 0 ? (
            <div className="text-muted-foreground text-sm">Loading…</div>
          ) : runs.length === 0 && definitions.length === 0 ? (
            <FlowsEmptyState onNewFlow={() => setCreatorOpen(true)} />
          ) : runs.length === 0 ? (
            <div className="space-y-2 py-2">
              <p className="text-xs text-muted-foreground">No runs yet — click ▶ Run on a definition above, or trigger via chat:</p>
              <code className="block text-xs font-mono bg-muted px-2 py-1 rounded text-muted-foreground">
                /mesh-flow run &lt;flow_id&gt;
              </code>
            </div>
          ) : (
            <ul className="space-y-2">
              {runs.map((run) => (
                <li
                  key={run.run_id}
                  onClick={() => {
                    setSelected(run.run_id);
                    setView('timeline');
                  }}
                  className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                    selected === run.run_id
                      ? 'border-blue-400 bg-blue-50 dark:bg-blue-950/30 dark:border-blue-600'
                      : 'border-border hover:border-muted-foreground/40 bg-card'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-mono text-xs text-muted-foreground truncate max-w-[120px]">
                      {run.run_id}
                    </span>
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                        STATUS_COLORS[run.status] ?? 'bg-muted text-muted-foreground'
                      }`}
                    >
                      {run.status}
                    </span>
                  </div>
                  <div className="text-sm font-medium text-foreground truncate">
                    {run.flow_id ?? '—'}
                    {run.flow_version && (
                      <span className="text-muted-foreground font-normal ml-1">
                        v{run.flow_version}
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {fmtTs(run.started_at)} · {run.event_count} events
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* ── Detail panel ── */}
        <div className="flex-1 min-w-0">
          {!selected ? (
            <div className="mt-16 text-center space-y-2">
              <div className="text-3xl select-none">⬡</div>
              <p className="text-sm font-medium text-foreground">Select a run</p>
              <p className="text-xs text-muted-foreground max-w-xs mx-auto">
                Choose a FlowRun from the left panel to inspect its timeline,
                event graph, and approve human-in-the-loop checkpoints.
              </p>
            </div>
          ) : (
            <>
              {/* Run summary card */}
              {selectedRun && (
                <div className="mb-4 p-4 bg-card border border-border rounded-lg">
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="font-semibold text-foreground">{selectedRun.flow_id}</span>
                      <span className="text-muted-foreground ml-2 text-sm">
                        v{selectedRun.flow_version}
                      </span>
                      <span className="font-mono text-xs text-muted-foreground ml-3">
                        {selectedRun.run_id}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      {selectedRun.status === 'paused' && (
                        <button
                          onClick={() => void approveCheckpoint(selectedRun.run_id)}
                          disabled={approving}
                          className="text-sm px-4 py-1.5 rounded bg-yellow-500 text-black font-medium hover:bg-yellow-400 disabled:opacity-50 transition-colors"
                        >
                          {approving ? 'Approving…' : '▶ Approve checkpoint'}
                        </button>
                      )}
                      {selectedRun.flow_id && selectedRun.status !== 'running' && (
                        <button
                          onClick={() => void triggerFlow(selectedRun.flow_id!)}
                          disabled={triggering === selectedRun.flow_id}
                          className="text-xs px-3 py-1 rounded border border-border hover:bg-muted transition-colors text-muted-foreground disabled:opacity-50"
                          title="Start a new run of this flow"
                        >
                          {triggering === selectedRun.flow_id ? '…' : '▶ Run again'}
                        </button>
                      )}
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                          STATUS_COLORS[selectedRun.status] ?? 'bg-muted text-muted-foreground'
                        }`}
                      >
                        {selectedRun.status}
                      </span>
                    </div>
                  </div>
                  <div className="text-xs text-muted-foreground mt-2 flex flex-wrap gap-4">
                    <span>Started: {fmtTs(selectedRun.started_at)}</span>
                    <span>Duration: {fmtDuration(selectedRun.started_at, selectedRun.completed_at)}</span>
                    {selectedRun.steps_done != null && (
                      <span>Steps: {selectedRun.steps_done}</span>
                    )}
                    {selectedRun.paused_at_step && (
                      <span className="text-yellow-600 dark:text-yellow-400">
                        Paused at: {selectedRun.paused_at_step}
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* View toggle: Timeline | Graph | Results */}
              <div className="flex items-center gap-1 mb-4">
                <button
                  onClick={() => setView('timeline')}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    view === 'timeline'
                      ? 'bg-accent text-accent-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  }`}
                >
                  ≡  Timeline
                </button>
                <button
                  onClick={() => setView('graph')}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    view === 'graph'
                      ? 'bg-accent text-accent-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  }`}
                >
                  ⬡  Graph
                </button>
                <button
                  onClick={() => setView('results')}
                  className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    view === 'results'
                      ? 'bg-accent text-accent-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  }`}
                >
                  ✦  Results
                </button>
              </div>

              {/* ── Timeline view ── */}
              {view === 'timeline' && (
                <>
                  {detailLoading ? (
                    <div className="text-muted-foreground text-sm">Loading events…</div>
                  ) : events.length === 0 ? (
                    <div className="text-muted-foreground text-sm">No events.</div>
                  ) : (
                    <ol className="relative border-l border-border space-y-3 ml-2">
                      {events.map((ev, i) => (
                        <li key={i} className="ml-5">
                          <span className="absolute -left-2 flex items-center justify-center w-4 h-4 rounded-full bg-muted text-xs">
                            {EVENT_ICONS[ev.type] ?? '·'}
                          </span>
                          <div className="p-2 bg-card border border-border rounded">
                            <div className="flex items-center justify-between">
                              <span className="font-mono text-xs text-foreground">{ev.type}</span>
                              <span className="text-xs text-muted-foreground">{fmtTs(ev.ts)}</span>
                            </div>
                            {ev.step_id && (
                              <div className="text-xs text-muted-foreground mt-0.5">
                                step: <span className="font-mono">{ev.step_id}</span>
                                {ev.target_node && (
                                  <> → <span className="font-mono">{ev.target_node}</span></>
                                )}
                              </div>
                            )}
                            {ev.output_sha256_prefix && (
                              <div className="text-xs text-muted-foreground font-mono mt-0.5">
                                sha256: {ev.output_sha256_prefix}…
                              </div>
                            )}
                            {ev.reason && (
                              <div className="text-xs text-red-600 dark:text-red-400 mt-0.5">
                                {ev.reason}
                              </div>
                            )}
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )}

              {/* ── Graph view ── */}
              {view === 'graph' && (
                <Suspense fallback={<div className="text-muted-foreground text-sm">Loading graph…</div>}>
                  {steps.length === 0 ? (
                    <div className="text-muted-foreground text-sm">
                      {selectedRun?.flow_id
                        ? `Flow definition for "${selectedRun.flow_id}" not found — install the bundle first.`
                        : 'No flow definition available.'}
                    </div>
                  ) : (
                    <FlowGraphView
                      steps={steps}
                      events={events}
                      runStatus={selectedRun?.status ?? 'running'}
                      onApprove={() => void approveCheckpoint(selectedRun!.run_id)}
                      approving={approving}
                    />
                  )}
                </Suspense>
              )}

              {/* ── Results view ── */}
              {view === 'results' && (
                <ResultsPanel
                  runId={selected!}
                  outputs={outputs}
                  events={events}
                  onRefresh={() => { void fetchOutputs(selected!); void fetchDetail(selected!); }}
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>

    {/* Flow Creator Panel — ADR-0122 M1 */}
    <FlowCreatorPanel
      open={creatorOpen}
      onClose={() => { setCreatorOpen(false); setEditFlowId(undefined); }}
      onSaved={() => {
        setCreatorOpen(false);
        setEditFlowId(undefined);
        void fetchRuns();
      }}
      initialFlowId={editFlowId}
    />
    </>
  );
}
