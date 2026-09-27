/**
 * Runs next to the work items, in each work view's own form (ADR-2081 P4).
 * Read-only: a run can be LINKED to an item, never edited or dragged.
 * Mappings (column, grouping, lane positions) live in run-encodings.ts.
 */
import { useState } from "react";
import { ChevronDown, ChevronRight, Link2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { UnifiedTask } from "@/lib/api/initiatives";
import { cn } from "@/lib/utils";
import { StepsList, TypeBadge } from "./activity-parts";
import { STATUS_META } from "./encodings";
import { UNIFIED_STATUS, formatUtc, taskDuration } from "./format";
import { groupRuns, isActiveRun, runBoardColumn, runLane } from "./run-encodings";

type OnLink = (t: UnifiedTask) => void;

export function RunStatus({ run }: { run: UnifiedTask }) {
  const st = UNIFIED_STATUS[run.status] ?? { label: run.status, tone: "secondary" as const };
  return <Badge variant={st.tone} title={run.raw_status ? `source status: ${run.raw_status}` : undefined}>{st.label}</Badge>;
}

function LinkButton({ run, onLink }: { run: UnifiedTask; onLink: OnLink }) {
  return (
    <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={(e) => { e.stopPropagation(); onLink(run); }}
      aria-label={`Link ${run.title} to a task`}>
      <Link2 className="mr-1 h-3 w-3" />Link
    </Button>
  );
}

function RunLine({ run, now, onLink }: { run: UnifiedTask; now: number; onLink: OnLink }) {
  return (
    <li className="grid grid-cols-[1fr_auto] items-start gap-x-3 px-2 py-1.5 md:grid-cols-[minmax(0,1fr)_6rem_6rem_auto]" data-testid={`run-${run.id}`}>
      <div className="min-w-0 pl-7">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm">{run.title}</span>
        </div>
        <div className="text-xs text-muted-foreground">{run.detail}</div>
        {run.stale_reason && <div className="text-xs text-amber-700 dark:text-amber-400">{run.stale_reason}</div>}
        {run.steps && run.steps.total > 0 && <StepsList steps={run.steps} now={now} />}
      </div>
      <div className="hidden md:block"><RunStatus run={run} /></div>
      <div className="hidden text-right text-xs tabular-nums text-muted-foreground md:block">{taskDuration(run, now)}</div>
      <LinkButton run={run} onLink={onLink} />
    </li>
  );
}

/** Tree: one collapsible group per type and channel, below the item tree. */
export function RunsTree({ runs, now, onLink }: { runs: UnifiedTask[]; now: number; onLink: OnLink }) {
  const groups = groupRuns(runs);
  const [open, setOpen] = useState<Set<string>>(() => new Set(groups.filter((g) => g.active > 0).map((g) => g.key)));
  if (groups.length === 0) return null;
  const toggle = (k: string) => setOpen((cur) => { const n = new Set(cur); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  return (
    <ul className="divide-y rounded-lg border" data-testid="runs-tree" aria-label="Runs">
      {groups.map((g) => {
        const isOpen = open.has(g.key);
        return (
          <li key={g.key} data-testid={`run-group-${g.key}`}>
            <button type="button" aria-expanded={isOpen} onClick={() => toggle(g.key)}
              className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-muted/40">
              {isOpen ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
              <TypeBadge type={g.type} label={g.label} />
              <span className="text-xs text-muted-foreground tabular-nums">
                {g.runs.length} {g.runs.length === 1 ? "run" : "runs"}{g.active > 0 && ` · ${g.active} active`}
              </span>
            </button>
            {isOpen && <ul className="divide-y border-t bg-muted/10">{g.runs.map((r) => <RunLine key={r.id} run={r} now={now} onLink={onLink} />)}</ul>}
          </li>
        );
      })}
    </ul>
  );
}

/** Board: a run card — dashed and not draggable, so it never reads as a work item. */
export function RunCard({ run, now, onLink }: { run: UnifiedTask; now: number; onLink: OnLink }) {
  return (
    <div data-testid={`run-card-${run.id}`} className="rounded-md border border-dashed bg-background/60 p-2 text-left">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm leading-snug">{run.title}</span>
        <TypeBadge type={run.type} label={run.type_label} />
      </div>
      <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
        <span className="tabular-nums">{taskDuration(run, now)}</span>
        {run.steps && run.steps.total > 0 && <span>{run.steps.total} {run.steps.total === 1 ? "step" : "steps"}</span>}
        <span className="ml-auto"><LinkButton run={run} onLink={onLink} /></span>
      </div>
    </div>
  );
}

/** Timeline: the runs of the last 24 h on their own axis. */
export function RunsTimeline({ runs, now, onLink }: { runs: UnifiedTask[]; now: number; onLink: OnLink }) {
  const lane = runLane(runs, now);
  if (lane.rows.length === 0) return null;
  const pct = (v: number) => `${(v * 100).toFixed(3)}%`;
  const ticks = Array.from({ length: 9 }, (_, i) => ({ x: i / 8, label: i === 8 ? "now" : `-${24 - i * 3} h` }));
  return (
    <section className="space-y-1" data-testid="run-lane" aria-label="Runs, last 24 hours">
      <h3 className="text-xs font-medium text-muted-foreground">Runs — last 24 h (own time axis)</h3>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-5 rounded-sm border-[1.5px] border-muted-foreground" />Queued</span>
        {([["in_progress", "Running"], ["blocked", "Failed / stale"], ["complete", "Done"]] as const).map(([s, label]) => (
          <span key={s} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-5 rounded-sm" style={{ background: STATUS_META[s].fill ?? undefined }} />{label}</span>
        ))}
      </div>
      <div className="overflow-x-auto rounded-lg border">
        <div className="grid min-w-[720px] grid-cols-[minmax(12rem,18rem)_1fr]">
          <div className="border-b border-r bg-muted/30 px-2 py-1 text-xs text-muted-foreground">Run</div>
          <div className="relative h-7 border-b bg-muted/30">
            {ticks.map((t) => (
              <span key={t.x} style={{ left: pct(t.x) }}
                className={cn("absolute top-1.5 whitespace-nowrap px-1 text-[11px] text-muted-foreground",
                  t.x === 0 ? "" : t.x === 1 ? "-translate-x-full" : "-translate-x-1/2")}>{t.label}</span>
            ))}
          </div>
          {lane.rows.map(({ run, x0, x1 }) => {
            const fill = STATUS_META[runBoardColumn(run.status)].fill;
            return (
              <div key={run.id} className="contents">
                <button type="button" onClick={() => onLink(run)} title="Link to a task"
                  className="flex min-w-0 items-center gap-1.5 border-r px-2 text-left text-xs hover:bg-muted/40" style={{ height: 26 }}>
                  <TypeBadge type={run.type} label={run.type_label} />
                  <span className="truncate">{run.title}</span>
                </button>
                <div className="relative" style={{ height: 26 }}>
                  {ticks.map((t) => <span key={t.x} aria-hidden className="absolute inset-y-0 border-l" style={{ left: pct(t.x), borderColor: "var(--viz-grid)" }} />)}
                  <span role="img" data-testid={`run-bar-${run.id}`}
                    title={`${run.title}\n${UNIFIED_STATUS[run.status]?.label ?? run.status} · ${taskDuration(run, now)}\n${formatUtc(run.started_at ?? run.created_at)} → ${run.ended_at ? formatUtc(run.ended_at) : "now"}`}
                    aria-label={`${run.title}: ${UNIFIED_STATUS[run.status]?.label ?? run.status}, ${formatUtc(run.started_at ?? run.created_at)} to ${run.ended_at ? formatUtc(run.ended_at) : "now"}`}
                    className={cn("absolute top-1/2 h-3 -translate-y-1/2 rounded", !fill && "border-[1.5px] border-muted-foreground bg-background")}
                    style={{ left: pct(x0), width: `max(4px, ${pct(x1 - x0)})`, background: fill ?? undefined }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {lane.hidden > 0 && <p className="text-xs text-muted-foreground">{lane.hidden} older runs of the last 24 h are listed in Activity.</p>}
    </section>
  );
}

/** Table: runs as a second body of the item table, same columns where they apply. */
export function RunsTableBody({ runs, now, onLink }: { runs: UnifiedTask[]; now: number; onLink: OnLink }) {
  if (runs.length === 0) return null;
  const dash = <span className="text-muted-foreground">—</span>;
  return (
    <tbody className="divide-y border-t-2" data-testid="runs-table">
      <tr className="bg-muted/30"><th colSpan={8} className="px-3 py-1.5 text-left text-xs font-medium text-muted-foreground">
        Runs — active and last 24 h ({runs.length})</th></tr>
      {runs.map((r) => (
        <tr key={r.id} className={cn(isActiveRun(r) && "bg-cyan-500/5")}>
          <td className="max-w-[22rem] px-3 py-2">
            <div className="truncate">{r.title}</div>
            {r.detail && <div className="truncate text-xs text-muted-foreground">{r.detail}</div>}
          </td>
          <td className="px-3 py-2"><TypeBadge type={r.type} label={r.type_label} /></td>
          <td className="px-3 py-2"><RunStatus run={r} /></td>
          <td className="px-3 py-2">{dash}</td>
          <td className="px-3 py-2 text-xs">{r.subtype ?? dash}</td>
          <td className="px-3 py-2 text-xs tabular-nums">{taskDuration(r, now)}</td>
          <td className="px-3 py-2 text-xs">{r.steps && r.steps.total > 0 ? `${r.steps.total} steps` : dash}</td>
          <td className="whitespace-nowrap px-3 py-2 text-right text-xs text-muted-foreground">
            {formatUtc(r.ended_at ?? r.started_at ?? r.created_at)} <LinkButton run={r} onLink={onLink} />
          </td>
        </tr>
      ))}
    </tbody>
  );
}
