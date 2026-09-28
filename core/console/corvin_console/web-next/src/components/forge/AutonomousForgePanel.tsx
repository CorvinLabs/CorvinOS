import React, { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Loader2, Play, RefreshCw, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { ApiError } from '@/lib/api/client';
import {
  canaryAction,
  forkCandidate,
  getCanaryHistory,
  getCanaryMetrics,
  getForgeStatus,
  getForkRun,
  runAutopilotTick,
  setAutopilot,
  type CanaryAction,
  type ForkRun,
} from '@/lib/api/autonomous-forge';
import StatusDisplay from './StatusDisplay';
import MetricsChart from './MetricsChart';
import OperatorControls from './OperatorControls';
import AuditHistoryDisplay from './AuditHistoryDisplay';
import { STATUS_LABEL, formatScore, isActive } from './autonomous-forge-encoding';

const KEY = ['autonomous-forge'];

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const d = (err.detail as { detail?: unknown } | null)?.detail;
    if (typeof d === 'string') return d;
    if (d && typeof d === 'object') {
      const o = d as { message?: unknown; reason?: unknown };
      if (typeof o.message === 'string') return o.message;
      if (o.reason !== undefined) return String(o.reason);
    }
    return `Request failed (${err.status})`;
  }
  return err instanceof Error ? err.message : 'Request failed';
}

/**
 * Autonomous Skill Forge: a candidate version of a skill is served to a share
 * of chats, both versions are rated by the users' next messages, and the
 * candidate is rolled out, deferred or rolled back on that evidence.
 */
export const AutonomousForgePanel: React.FC = () => {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [forkTarget, setForkTarget] = useState<string | null>(null);
  const [instruction, setInstruction] = useState('');
  const [forkRun, setForkRun] = useState<ForkRun | null>(null);

  const status = useQuery({
    queryKey: [...KEY, 'status'],
    queryFn: getForgeStatus,
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (d.canaries.length > 0 || d.fork_runs.length > 0) ? 5000 : 30000;
    },
  });
  const history = useQuery({ queryKey: [...KEY, 'history'], queryFn: () => getCanaryHistory(20), refetchInterval: 30000 });

  const canaries = status.data?.canaries ?? [];
  const historyItems = history.data?.attempts ?? [];
  const current =
    canaries.find((c) => c.skill_id === selected) ??
    historyItems.find((c) => c.skill_id === selected) ??
    canaries[0] ??
    null;

  const metrics = useQuery({
    queryKey: [...KEY, 'metrics', current?.skill_id],
    queryFn: () => getCanaryMetrics(current!.skill_id),
    enabled: !!current,
    refetchInterval: current && isActive(current.status) ? 5000 : false,
  });

  // Follow an operator fork run to its end.
  useEffect(() => {
    if (!forkRun || forkRun.status !== 'running') return;
    const t = setInterval(async () => {
      try {
        const run = await getForkRun(forkRun.run_id);
        setForkRun(run);
        if (run.status !== 'running') {
          setMessage(run.status === 'success'
            ? { kind: 'ok', text: `Candidate for ${run.skill_id} is live in a canary.` }
            : { kind: 'error', text: `Forging ${run.skill_id} failed: ${run.error ?? run.message}` });
          setSelected(run.skill_id);
          qc.invalidateQueries({ queryKey: KEY });
        }
      } catch (err) {
        setMessage({ kind: 'error', text: errorText(err) });
        setForkRun(null);
      }
    }, 2000);
    return () => clearInterval(t);
  }, [forkRun, qc]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      setMessage({ kind: 'ok', text: ok });
      await qc.invalidateQueries({ queryKey: KEY });
    } catch (err) {
      setMessage({ kind: 'error', text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  const onAction = (action: CanaryAction) =>
    act(() => canaryAction(action, current!.skill_id), `${current!.skill_id}: ${action} done.`);

  const startFork = async () => {
    const skill = forkTarget!;
    setForkTarget(null);
    setBusy(true);
    setMessage(null);
    try {
      const { run_id } = await forkCandidate(skill, instruction.trim());
      setForkRun({ run_id, skill_id: skill, status: 'running', phase: 'planning', progress: 5, message: 'Starting…' });
      setInstruction('');
    } catch (err) {
      setMessage({ kind: 'error', text: errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  if (status.isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (status.isError) {
    return (
      <Card>
        <CardContent className="py-8 text-sm text-destructive flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" /> {errorText(status.error)}
        </CardContent>
      </Card>
    );
  }

  const s = status.data!;
  const running = forkRun?.status === 'running' ? forkRun : null;

  return (
    <div className="space-y-6 pb-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1 max-w-3xl">
          <h2 className="text-2xl font-bold">Autonomous Skill Forge</h2>
          <p className="text-sm text-muted-foreground">
            A candidate version of a skill is served to a share of chats. Users&apos; next messages rate both versions;
            the candidate rolls out only when it rates at least as well as the live skill. Canary traffic reaches chats
            that inject skills per turn (messenger bridges and delegated tasks).
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => qc.invalidateQueries({ queryKey: KEY })}>
          <RefreshCw className={`w-4 h-4 mr-2 ${status.isFetching ? 'animate-spin' : ''}`} /> Refresh
        </Button>
      </div>

      {message && (
        <div role="status" className={`rounded-md border px-4 py-2 text-sm ${message.kind === 'error' ? 'border-destructive text-destructive' : ''}`}>
          {message.text}
        </div>
      )}

      <Card>
        <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3 py-4">
          <label className="flex items-center gap-3 text-sm font-medium">
            <Switch
              checked={s.autopilot.enabled}
              disabled={busy}
              onCheckedChange={(v) => act(() => setAutopilot(v), `Autopilot ${v ? 'on' : 'off'}.`)}
            />
            Autopilot
          </label>
          <span className="text-xs text-muted-foreground max-w-xl">
            Every {Math.round(s.autopilot.interval_s / 60)} min it moves canaries through their gates and forges a candidate
            for a skill whose last {s.autopilot.loss_rule.min_outcomes}+ ratings average below{' '}
            {s.autopilot.loss_rule.threshold} ({s.autopilot.loss_rule.window_days} days). Approval stays with you.
          </span>
          <span className="text-xs text-muted-foreground ml-auto">
            Last run: {s.autopilot.last_tick ? new Date(s.autopilot.last_tick * 1000).toLocaleString('en-US') : 'never'}
          </span>
          <Button size="sm" variant="outline" disabled={busy}
            onClick={() => act(runAutopilotTick, 'Autopilot pass started.')}>
            <Play className="w-4 h-4 mr-2" /> Run now
          </Button>
        </CardContent>
      </Card>

      {running && (
        <Card>
          <CardContent className="py-4 space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              Forging a candidate for <span className="font-mono">{running.skill_id}</span> — {running.message}
            </div>
            <div className="h-1.5 rounded bg-muted overflow-hidden">
              <div className="h-full bg-primary transition-all" style={{ width: `${running.progress}%` }} />
            </div>
          </CardContent>
        </Card>
      )}

      {canaries.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {canaries.map((c) => (
            <Button key={c.skill_id} size="sm" variant={c.skill_id === current?.skill_id ? 'default' : 'outline'}
              onClick={() => setSelected(c.skill_id)}>
              <span className="font-mono">{c.skill_id}</span>
            </Button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <StatusDisplay canary={current} />
          {current && <MetricsChart points={metrics.data?.points ?? []} stats={metrics.data?.stats ?? null} />}
        </div>
        <div>
          {current && <OperatorControls canary={current} busy={busy} onAction={onAction} />}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Skills</CardTitle>
        </CardHeader>
        <CardContent>
          {s.skills.length === 0 ? (
            <p className="text-sm text-muted-foreground">No skills yet — create one in the Skill Forge tab.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="font-normal pb-2">Skill</th>
                  <th className="font-normal pb-2 pl-4 text-right">Ratings</th>
                  <th className="font-normal pb-2 pl-4 text-right">Mean</th>
                  <th className="font-normal pb-2 pl-4 text-right">Used</th>
                  <th className="font-normal pb-2 pl-4">Canary</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody>
                {s.skills.map((row) => {
                  const blocked = isActive(row.canary_status) || s.forks_in_flight.includes(row.skill_id) || running?.skill_id === row.skill_id;
                  return (
                    <tr key={row.skill_id} className="border-t align-top">
                      <td className="py-2 pr-2">
                        <div className="font-mono">{row.skill_id}</div>
                        <div className="text-xs text-muted-foreground line-clamp-2">{row.description}</div>
                        {row.loss_signal && (
                          <div className="mt-1 inline-flex items-center gap-1 text-xs text-destructive">
                            <AlertTriangle className="w-3 h-3" /> Rated low — the autopilot will forge a candidate
                          </div>
                        )}
                      </td>
                      <td className="py-2 pl-4 text-right tabular-nums">{row.outcome_n}</td>
                      <td className="py-2 pl-4 text-right tabular-nums">{formatScore(row.outcome_mean)}</td>
                      <td className="py-2 pl-4 text-right tabular-nums">{row.usage_n}</td>
                      <td className="py-2 pl-4 text-xs whitespace-nowrap">
                        {row.canary_status ? (
                          <button className="underline underline-offset-2" onClick={() => setSelected(row.skill_id)}>
                            {STATUS_LABEL[row.canary_status]}
                          </button>
                        ) : '—'}
                      </td>
                      <td className="py-2 text-right">
                        <Button size="sm" variant="outline" disabled={busy || blocked} onClick={() => setForkTarget(row.skill_id)}>
                          <Sparkles className="w-4 h-4 mr-1" /> Forge candidate
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      <AuditHistoryDisplay attempts={historyItems} />

      <Dialog open={forkTarget !== null} onOpenChange={(o) => !o && setForkTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Forge a candidate for <span className="font-mono">{forkTarget}</span></DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            The Skill Creator writes an improved version (this takes a few minutes on your Claude subscription).
            It starts at {10}% of chats; the live skill is unchanged until you approve.
          </p>
          <Textarea
            placeholder="What should change? Leave empty to improve it generally."
            value={instruction}
            maxLength={2000}
            onChange={(e) => setInstruction(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setForkTarget(null)}>Cancel</Button>
            <Button onClick={startFork} disabled={busy}>Forge candidate</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default AutonomousForgePanel;
