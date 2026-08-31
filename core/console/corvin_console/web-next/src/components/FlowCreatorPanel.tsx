/**
 * FlowCreatorPanel — ADR-0122 M1
 *
 * Slide-in right-panel for creating / updating flow definitions.
 * Three tabs: Form (guided) · Preview (live DAG) · YAML (raw).
 *
 * Writes via PUT /v1/console/flows/definition/{flow_id} with CSRF.
 */
import { useState, useEffect, lazy, Suspense } from 'react';

const FlowGraphView = lazy(() => import('../app/console/flows/FlowGraphView'));

// ── Types ──────────────────────────────────────────────────────────────────────

interface StepRow {
  step_id: string;
  node: string;
  prompt: string;
  depends_on: string[];
  checkpoint: boolean;
}

function emptyStep(): StepRow {
  return { step_id: '', node: 'local', prompt: '', depends_on: [], checkpoint: false };
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved: (flowId: string) => void;
  /** Pre-fill flow_id (e.g. when editing an existing flow) */
  initialFlowId?: string;
}

// ── Node options ───────────────────────────────────────────────────────────────

const NODE_OPTIONS = [
  { value: 'local',               label: 'local — this machine' },
  { value: 'delegate_claude_code', label: 'delegate_claude_code — fresh Claude' },
  { value: 'delegate_hermes',     label: 'delegate_hermes — local Ollama' },
  { value: 'delegate_opencode',   label: 'delegate_opencode — OpenCode' },
  { value: 'delegate_codex',      label: 'delegate_codex — Codex CLI' },
];

// ── YAML generator (client-side, no dependency) ────────────────────────────────

function buildYaml(flowId: string, budgetTokens: number, budgetSteps: number, steps: StepRow[]): string {
  const lines: string[] = [
    'flow:',
    `  id: ${flowId || '<flow_id>'}`,
    '  version: "1.0.0"',
    '  budget:',
    `    max_tokens: ${budgetTokens}`,
    `    max_steps: ${budgetSteps}`,
    '    require_audit: true',
    '  steps:',
  ];
  for (const s of steps) {
    const sid = s.step_id || '<step_id>';
    lines.push(`    ${sid}:`);
    lines.push(`      node: ${s.node}`);
    if (s.depends_on.length > 0) {
      lines.push(`      depends_on: [${s.depends_on.join(', ')}]`);
    }
    if (s.checkpoint) {
      lines.push('      checkpoint: human');
    }
    if (s.prompt) {
      lines.push(`      task: "${s.prompt.replace(/"/g, '\\"')}"`);
    }
  }
  return lines.join('\n');
}

// ── Main component ─────────────────────────────────────────────────────────────

export function FlowCreatorPanel({ open, onClose, onSaved, initialFlowId }: Props) {
  const [tab, setTab]                 = useState<'form' | 'preview' | 'yaml'>('form');
  const [flowId, setFlowId]           = useState(initialFlowId ?? '');
  const [budgetTokens, setBudgetTokens] = useState(50_000);
  const [budgetSteps, setBudgetSteps]   = useState(10);
  const [steps, setSteps]             = useState<StepRow[]>([emptyStep()]);
  const [saving, setSaving]           = useState(false);
  const [error, setError]             = useState<string | null>(null);
  const [csrf, setCsrf]               = useState<string>('');

  // Fetch CSRF token on open
  useEffect(() => {
    if (!open) return;
    fetch('/v1/console/auth/whoami', { credentials: 'include' })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setCsrf(d.csrf_token ?? ''))
      .catch(() => setCsrf(''));
  }, [open]);

  // Reset or load definition when panel opens
  useEffect(() => {
    if (!open) return;
    if (!initialFlowId) {
      setFlowId('');
      setBudgetTokens(50_000);
      setBudgetSteps(10);
      setSteps([emptyStep()]);
      setError(null);
      setTab('form');
      return;
    }
    // Editing an existing flow — load definition from backend
    setFlowId(initialFlowId);
    setError(null);
    setTab('form');
    fetch(`/v1/console/flows/definition/${encodeURIComponent(initialFlowId)}?full=1`, {
      credentials: 'include',
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: {
        budget_tokens?: number;
        budget_steps?: number;
        steps: Array<{ id: string; node: string; task?: string; depends_on: string[]; checkpoint?: string }>;
      }) => {
        if (d.budget_tokens) setBudgetTokens(d.budget_tokens);
        if (d.budget_steps)  setBudgetSteps(d.budget_steps);
        setSteps(
          d.steps.map(s => ({
            step_id:    s.id,
            node:       s.node ?? 'local',
            prompt:     s.task ?? '',
            depends_on: s.depends_on ?? [],
            checkpoint: s.checkpoint === 'human',
          }))
        );
      })
      .catch(() => setError('Could not load existing definition — starting with empty form'));
  }, [open, initialFlowId]);

  // ── Step helpers ─────────────────────────────────────────────────────────────

  const addStep = () => setSteps(s => [...s, emptyStep()]);
  const removeStep = (i: number) => setSteps(s => s.filter((_, j) => j !== i));
  const updateStep = (i: number, patch: Partial<StepRow>) =>
    setSteps(s => s.map((r, j) => j === i ? { ...r, ...patch } : r));

  const toggleDepend = (stepIdx: number, depId: string) => {
    const cur = steps[stepIdx].depends_on;
    const next = cur.includes(depId)
      ? cur.filter(x => x !== depId)
      : [...cur, depId];
    updateStep(stepIdx, { depends_on: next });
  };

  // ── Graph props ───────────────────────────────────────────────────────────────

  const graphSteps = steps
    .filter(s => s.step_id.trim())
    .map(s => ({
      id: s.step_id,
      node: s.node,
      depends_on: s.depends_on,
      checkpoint: s.checkpoint ? 'human' : undefined,
    }));

  // ── Save ──────────────────────────────────────────────────────────────────────

  const save = async () => {
    setError(null);
    if (!flowId.trim()) { setError('Flow ID is required'); return; }
    if (steps.some(s => !s.step_id.trim())) { setError('All steps need a Step ID'); return; }

    setSaving(true);
    try {
      const resp = await fetch(`/v1/console/flows/definition/${encodeURIComponent(flowId)}`, {
        method: 'PUT',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
        },
        body: JSON.stringify({
          version: '1.0.0',
          budget_tokens: budgetTokens,
          budget_steps: budgetSteps,
          budget_wall_time_s: 600,
          steps: steps.map(s => ({
            step_id: s.step_id,
            node: s.node,
            prompt: s.prompt,
            depends_on: s.depends_on,
            checkpoint: s.checkpoint ? 'human' : null,
          })),
        }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        setError(body.detail ?? `HTTP ${resp.status}`);
        return;
      }
      onSaved(flowId);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────────

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 z-40"
        onClick={onClose}
        aria-hidden
      />

      {/* Panel */}
      <div
        data-testid="flow-creator-panel"
        className="fixed right-0 top-0 bottom-0 w-full max-w-2xl bg-background border-l border-border flex flex-col z-50 shadow-2xl"
        role="dialog"
        aria-label="New Flow"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border flex-shrink-0">
          <div>
            <h2 className="font-semibold text-lg text-foreground">
              {initialFlowId ? `Edit Flow — ${initialFlowId}` : 'New Flow'}
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              CorvinFlow multi-step pipeline
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close panel"
            className="text-muted-foreground hover:text-foreground transition-colors p-1"
          >
            ✕
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-6 pt-3 pb-0 flex-shrink-0 border-b border-border">
          {(['form', 'preview', 'yaml'] as const).map(t => (
            <button
              key={t}
              data-testid={`tab-${t}`}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 text-sm font-medium rounded-t border-b-2 transition-colors ${
                tab === t
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {t === 'form' ? '⚙ Form' : t === 'preview' ? '⬡ Preview' : '{ } YAML'}
            </button>
          ))}
        </div>

        {/* Body — scrollable */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">

          {/* ── FORM TAB ───────────────────────────────────────────────────── */}
          {tab === 'form' && (
            <>
              {/* Flow metadata */}
              <div className="grid gap-3">
                <div>
                  <label className="block text-xs font-semibold text-muted-foreground mb-1">
                    Flow ID <span className="text-red-500">*</span>
                  </label>
                  <input
                    data-testid="input-flow-id"
                    type="text"
                    value={flowId}
                    onChange={e => setFlowId(e.target.value.replace(/[^A-Za-z0-9_-]/g, ''))}
                    placeholder="e.g. standup-summarizer"
                    className="w-full rounded border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary font-mono"
                  />
                  <p className="text-xs text-muted-foreground mt-1">Letters, digits, hyphens, underscores only.</p>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">
                      Budget — max tokens
                    </label>
                    <input
                      data-testid="input-budget-tokens"
                      type="number"
                      value={budgetTokens}
                      onChange={e => setBudgetTokens(Number(e.target.value))}
                      min={1000} max={10_000_000} step={1000}
                      className="w-full rounded border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-muted-foreground mb-1">
                      Budget — max steps
                    </label>
                    <input
                      data-testid="input-budget-steps"
                      type="number"
                      value={budgetSteps}
                      onChange={e => setBudgetSteps(Number(e.target.value))}
                      min={1} max={100}
                      className="w-full rounded border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                  </div>
                </div>
              </div>

              {/* Steps */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    Steps ({steps.length})
                  </span>
                  <button
                    data-testid="btn-add-step"
                    onClick={addStep}
                    className="text-xs px-2 py-1 rounded border border-border hover:bg-muted transition-colors"
                  >
                    + Add step
                  </button>
                </div>

                <div className="space-y-3">
                  {steps.map((step, i) => {
                    const prevIds = steps.slice(0, i).map(s => s.step_id).filter(Boolean);
                    return (
                      <div
                        key={i}
                        data-testid={`step-card-${i}`}
                        className="rounded-lg border border-border bg-card p-4 space-y-3"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-muted-foreground">
                            Step {i + 1}
                          </span>
                          {steps.length > 1 && (
                            <button
                              data-testid={`btn-remove-step-${i}`}
                              onClick={() => removeStep(i)}
                              className="text-xs text-muted-foreground hover:text-destructive transition-colors"
                            >
                              Remove
                            </button>
                          )}
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="block text-xs text-muted-foreground mb-1">
                              Step ID <span className="text-red-500">*</span>
                            </label>
                            <input
                              data-testid={`input-step-id-${i}`}
                              type="text"
                              value={step.step_id}
                              onChange={e => updateStep(i, { step_id: e.target.value.replace(/[^A-Za-z0-9_-]/g, '') })}
                              placeholder="e.g. fetch"
                              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                            />
                          </div>
                          <div>
                            <label className="block text-xs text-muted-foreground mb-1">Node</label>
                            <select
                              data-testid={`select-node-${i}`}
                              value={step.node}
                              onChange={e => updateStep(i, { node: e.target.value })}
                              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                            >
                              {NODE_OPTIONS.map(o => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                              ))}
                            </select>
                          </div>
                        </div>

                        <div>
                          <label className="block text-xs text-muted-foreground mb-1">
                            Prompt / task template
                          </label>
                          <textarea
                            data-testid={`textarea-prompt-${i}`}
                            value={step.prompt}
                            onChange={e => updateStep(i, { prompt: e.target.value })}
                            rows={2}
                            placeholder='e.g. "Summarise the standup notes: {flow.input.text}"'
                            className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs resize-none focus:outline-none focus:ring-1 focus:ring-primary"
                          />
                        </div>

                        {prevIds.length > 0 && (
                          <div>
                            <span className="block text-xs text-muted-foreground mb-1">Depends on</span>
                            <div className="flex flex-wrap gap-2">
                              {prevIds.map(pid => (
                                <label key={pid} className="flex items-center gap-1 text-xs cursor-pointer">
                                  <input
                                    data-testid={`dep-${i}-${pid}`}
                                    type="checkbox"
                                    checked={step.depends_on.includes(pid)}
                                    onChange={() => toggleDepend(i, pid)}
                                    className="rounded"
                                  />
                                  <code className="font-mono">{pid}</code>
                                </label>
                              ))}
                            </div>
                          </div>
                        )}

                        <label className="flex items-center gap-2 text-xs cursor-pointer">
                          <input
                            data-testid={`checkbox-checkpoint-${i}`}
                            type="checkbox"
                            checked={step.checkpoint}
                            onChange={e => updateStep(i, { checkpoint: e.target.checked })}
                            className="rounded"
                          />
                          <span>Human-approval checkpoint — pause here for operator sign-off</span>
                        </label>
                      </div>
                    );
                  })}
                </div>
              </div>
            </>
          )}

          {/* ── PREVIEW TAB ────────────────────────────────────────────────── */}
          {tab === 'preview' && (
            <div>
              {graphSteps.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground text-sm">
                  Add at least one step with a Step ID to see the graph.
                </div>
              ) : (
                <div className="rounded border border-border overflow-hidden" style={{ height: 420 }}>
                  <Suspense fallback={<div className="text-muted-foreground text-sm p-4">Loading graph…</div>}>
                    <FlowGraphView
                      steps={graphSteps}
                      events={[]}
                      runStatus="running"
                    />
                  </Suspense>
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-2">
                Live DAG — updates as you edit steps in the Form tab.
              </p>
            </div>
          )}

          {/* ── YAML TAB ───────────────────────────────────────────────────── */}
          {tab === 'yaml' && (
            <div>
              <div className="rounded-lg bg-muted border border-border p-4">
                <p className="text-xs font-semibold text-muted-foreground mb-2">
                  Generated YAML — read-only preview
                </p>
                <pre
                  data-testid="yaml-preview"
                  className="text-xs font-mono text-foreground leading-relaxed whitespace-pre overflow-x-auto"
                >
                  {buildYaml(flowId, budgetTokens, budgetSteps, steps)}
                </pre>
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                This is the exact YAML written to <code className="font-mono">.corvin/flows/{flowId || '<flow_id>'}/flow.yaml</code>
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 border-t border-border px-6 py-4 flex items-center justify-between gap-3">
          {error && (
            <div
              data-testid="panel-error"
              className="flex-1 text-xs text-destructive bg-destructive/10 border border-destructive/20 rounded px-3 py-2"
            >
              {error}
            </div>
          )}
          <div className="flex gap-2 ml-auto">
            <button
              onClick={onClose}
              disabled={saving}
              className="px-4 py-2 text-sm rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              data-testid="btn-save-flow"
              onClick={() => void save()}
              disabled={saving}
              className="px-5 py-2 text-sm rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50 font-medium"
            >
              {saving ? 'Saving…' : 'Save flow'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
