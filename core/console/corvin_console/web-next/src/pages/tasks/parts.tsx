/** Small shared marks of the Tasks panel — every one carries text/icon, never colour alone. */
import {
  AlertTriangle, CheckCircle2, Circle, CircleDashed, Flag, Hourglass, Lock, PlayCircle, ShieldCheck, Archive,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Evidence, Item, ItemStatus, Priority } from "@/lib/api/task-tracking";
import { cn } from "@/lib/utils";
import { CATEGORY_LABEL, KIND_META, PRIORITY_META, STATUS_META, deadlineText } from "./encodings";
import { evidenceText, formatAgo } from "./format";

export function StatusIcon({ status, className }: { status: ItemStatus; className?: string }) {
  const c = cn("h-4 w-4 shrink-0", className);
  if (status === "complete") return <CheckCircle2 className={c} style={{ color: "var(--viz-status-complete)" }} aria-label="Complete" />;
  if (status === "in_progress") return <PlayCircle className={c} style={{ color: "var(--viz-status-progress)" }} aria-label="In progress" />;
  if (status === "blocked") return <Lock className={c} style={{ color: "var(--viz-status-blocked)" }} aria-label="Blocked" />;
  if (status === "archived") return <Archive className={cn(c, "text-muted-foreground")} aria-label="Archived" />;
  return <Circle className={cn(c, "text-muted-foreground")} aria-label="Open" />;
}

export function StatusBadge({ status }: { status: ItemStatus }) {
  const m = STATUS_META[status];
  return <Badge variant={m.badge} className="gap-1 whitespace-nowrap"><StatusIcon status={status} className="h-3 w-3" />{m.label}</Badge>;
}

export function PriorityChip({ priority, compact }: { priority: Priority; compact?: boolean }) {
  const m = PRIORITY_META[priority];
  return (
    <span data-testid="priority-chip" title={`${m.label} priority`}
      className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 text-[11px] font-medium leading-5",
        priority === "critical" ? "border-foreground/40 text-foreground" : "border-border text-muted-foreground")}>
      <span aria-hidden className="h-2 w-2 rounded-sm border border-border" style={{ background: m.stripe ?? "transparent" }} />
      {compact ? m.short : m.label}
    </span>
  );
}

export function KindTag({ item }: { item: Pick<Item, "kind" | "category"> }) {
  const cat = item.category ? CATEGORY_LABEL[item.category] ?? item.category : null;
  return (
    <span className="whitespace-nowrap text-[11px] uppercase tracking-wide text-muted-foreground">
      {cat && item.category === "gate" && <Flag className="mr-0.5 inline h-3 w-3" />}
      {cat ?? KIND_META[item.kind].label}
    </span>
  );
}

export function ProgressBar({ value, status, className }: { value: number; status: ItemStatus; className?: string }) {
  const fill = STATUS_META[status].fill ?? "hsl(var(--muted-foreground))";
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" role="progressbar"
        aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full rounded-full" style={{ width: `${value}%`, background: fill }} />
      </div>
      <span className="w-9 text-right text-xs tabular-nums text-muted-foreground">{value}%</span>
    </div>
  );
}

export function Deadline({ item, now }: { item: Pick<Item, "deadline" | "status" | "overdue">; now: number }) {
  const closed = item.status === "complete" || item.status === "archived";
  const text = deadlineText(item.deadline, now, closed);
  if (!text) return null;
  return (
    <span title={item.deadline ?? undefined}
      className={cn("inline-flex items-center gap-1 whitespace-nowrap text-xs",
        item.overdue ? "font-medium text-destructive" : "text-muted-foreground")}>
      {item.overdue ? <AlertTriangle className="h-3 w-3" /> : <Hourglass className="h-3 w-3" />}
      {text}
    </span>
  );
}

export function EvidenceBadge({ evidence, conflict }: { evidence: Evidence | null; conflict?: boolean }) {
  if (!evidence) return null;
  const ok = evidence.state === "ok";
  const tone = conflict ? "text-destructive" : ok ? "text-emerald-700 dark:text-emerald-400"
    : evidence.state === "unverified" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400";
  const label = conflict ? "Evidence disagrees" : ok ? "Verified" : evidence.state === "unverified" ? "Unverified"
    : `Evidence ${evidence.score_pct ?? 0}%`;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap text-xs", tone)}
      title={`${evidenceText(evidence)} · checked ${formatAgo(evidence.age_s)}${evidence.stale ? " (stale)" : ""}`}>
      {ok ? <ShieldCheck className="h-3 w-3" /> : evidence.state === "unverified" ? <CircleDashed className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
      {label}
    </span>
  );
}

export function ApprovalTag({ state }: { state: Item["approval_state"] }) {
  if (state === "none") return null;
  const map = {
    pending: { t: "Decision pending", v: "warn" as const },
    suggested: { t: "Suggested", v: "secondary" as const },
    approved: { t: "Go", v: "ok" as const },
    rejected: { t: "No-go", v: "danger" as const },
  };
  const m = map[state];
  return <Badge variant={m.v} className="whitespace-nowrap">{m.t}</Badge>;
}
