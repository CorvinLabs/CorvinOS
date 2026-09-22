/**
 * Every task on this install, typed — the "All tasks" part of the
 * Initiatives page. Data: GET /v1/console/initiatives/tasks (task_sources.py),
 * which reads each subsystem's own store (chat, background, ACS, workflow,
 * flow, gateway, forge, compute, scheduled, skill creator, initiative).
 */
import { AlertTriangle, Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { TaskType, TaskTypeSummary, UnifiedTask } from "@/lib/api/initiatives";
import { cn } from "@/lib/utils";
import { TYPE_CLASS, UNIFIED_STATUS, formatUtc, taskDuration } from "./initiatives-format";

export function TypeBadge({ type, label }: { type: string; label: string }) {
  return (
    <span data-testid="type-badge" className={cn("inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium", TYPE_CLASS[type] ?? "bg-muted")}>
      {label}
    </span>
  );
}

/** Filter chips, one per type, with the count for the current tab. */
export function TypeChips({ types, view, selected, onToggle, onAll }: {
  types: TaskTypeSummary[]; view: "active" | "finished";
  selected: Set<TaskType>; onToggle: (t: TaskType) => void; onAll: () => void;
}) {
  const count = (t: TaskTypeSummary) => (view === "active" ? t.active + t.stale : t.finished);
  return (
    <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Task types">
      <Button size="sm" variant={selected.size === 0 ? "default" : "outline"} className="h-7" onClick={onAll}>All types</Button>
      {types.map((t) => {
        const on = selected.has(t.type);
        const n = count(t);
        return (
          <button key={t.type} type="button" aria-pressed={on} data-testid={`type-chip-${t.type}`}
            title={t.note ?? t.error ?? undefined}
            onClick={() => onToggle(t.type)}
            className={cn("inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors",
              on ? "border-foreground/40 ring-1 ring-foreground/30" : "border-transparent",
              TYPE_CLASS[t.type], n === 0 && !on && "opacity-50")}>
            {t.label}<span className="tabular-nums font-semibold">{n}</span>
            {t.error && <AlertTriangle className="h-3 w-3" />}
          </button>
        );
      })}
    </div>
  );
}

export function TaskTable({ tasks, now, emptyText }: { tasks: UnifiedTask[]; now: number; emptyText: string }) {
  if (tasks.length === 0) return <p className="text-sm text-muted-foreground">{emptyText}</p>;
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Type</th>
            <th className="px-3 py-2 font-medium">Task</th>
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Started</th>
            <th className="px-3 py-2 font-medium">Ended</th>
            <th className="px-3 py-2 text-right font-medium">Duration</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {tasks.map((t) => {
            const st = UNIFIED_STATUS[t.status] ?? { label: t.status, tone: "secondary" as const };
            return (
              <tr key={t.id} data-testid={`utask-${t.id}`} className={cn(t.status === "stale" && "bg-amber-500/5")}>
                <td className="px-3 py-2 align-top"><TypeBadge type={t.type} label={t.type_label} /></td>
                <td className="px-3 py-2 align-top">
                  <div className="font-medium">{t.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {[t.subtype, t.detail].filter(Boolean).join(" · ")}
                  </div>
                  {t.stale_reason && <div className="text-xs text-amber-700 dark:text-amber-400">{t.stale_reason}</div>}
                </td>
                <td className="px-3 py-2 align-top">
                  <Badge variant={st.tone} title={t.raw_status ? `source status: ${t.raw_status}` : undefined}>{st.label}</Badge>
                </td>
                <td className="whitespace-nowrap px-3 py-2 align-top text-xs">{formatUtc(t.started_at ?? t.created_at)}</td>
                <td className="whitespace-nowrap px-3 py-2 align-top text-xs">{formatUtc(t.ended_at)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right align-top tabular-nums">{taskDuration(t, now)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Sources that are empty for a reason worth knowing (e.g. not loaded). */
export function SourceNotes({ types }: { types: TaskTypeSummary[] }) {
  const notes = types.filter((t) => t.note || t.error);
  if (notes.length === 0) return null;
  return (
    <div className="space-y-1 rounded-lg border px-3 py-2 text-xs text-muted-foreground">
      {notes.map((t) => (
        <div key={t.type} className="flex items-start gap-1.5">
          {t.error ? <AlertTriangle className="mt-0.5 h-3.5 w-3.5 text-destructive" /> : <Info className="mt-0.5 h-3.5 w-3.5" />}
          <span><span className="font-medium text-foreground">{t.label}:</span> {t.error ? `could not be read — ${t.error}` : t.note}</span>
        </div>
      ))}
    </div>
  );
}
