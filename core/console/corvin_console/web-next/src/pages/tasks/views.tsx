/** Tree · Board · Timeline · Table views of the Task-Tracking SSOT. Layout only — mappings live in encodings.ts. */
import { useMemo, useState, type DragEvent, type KeyboardEvent } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Link2, User } from "lucide-react";
import type { Item, ItemStatus } from "@/lib/api/task-tracking";
import { cn } from "@/lib/utils";
import {
  BOARD_COLUMNS, KIND_META, PRIORITY_META, STATUS_META, boardColumns, buildTimeline, displayProgress, formatDeadline,
  itemSpan, type Filters, type TreeRow,
} from "./encodings";
import { formatUtc } from "./format";
import { ApprovalTag, Deadline, EvidenceBadge, KindTag, PriorityChip, ProgressBar, StatusBadge, StatusIcon } from "./parts";

type Select = (id: string) => void;

/** Enter / Space open a row that is otherwise click-only. */
const activate = (fn: () => void) => (e: KeyboardEvent) => {
  if (e.target !== e.currentTarget) return;
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); }
};

function Empty({ text }: { text: string }) {
  return <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">{text}</p>;
}

// ── Tree ────────────────────────────────────────────────────────────────────

export function TreeView({ rows, now, selected, onSelect, collapsed, onToggle, filterActive, compact }: {
  rows: TreeRow[]; now: number; selected: string | null; onSelect: Select;
  collapsed: Set<string>; onToggle: (id: string) => void;
  /** While a filter is active every path to a hit is expanded and collapsing is off. */
  filterActive: boolean;
  /** Drawer open: drop the priority column so titles keep their width. */
  compact?: boolean;
}) {
  if (rows.length === 0) return <Empty text="No items match these filters." />;
  return (
    <ul className="divide-y rounded-lg border" data-testid="tree-view" role="tree">
      {rows.map(({ item, depth, hasChildren, matched }) => {
        const container = KIND_META[item.kind].container || hasChildren;
        const counts = item.rollup?.counts ?? {};
        const total = item.rollup?.descendants ?? 0;
        const done = counts.complete ?? 0;
        const isCollapsed = hasChildren && !filterActive && collapsed.has(item.id);
        return (
          <li key={item.id} role="treeitem" aria-level={depth + 1} aria-selected={selected === item.id}
            aria-expanded={hasChildren ? !isCollapsed : undefined}
            tabIndex={0} onKeyDown={activate(() => onSelect(item.id))}
            data-testid={`tree-row-${item.id}`}
            className={cn("grid cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 px-2 py-1.5 hover:bg-muted/40",
              compact ? "md:grid-cols-[minmax(0,1fr)_7rem_6.5rem]" : "md:grid-cols-[minmax(0,1fr)_5.5rem_9rem_7rem]",
              selected === item.id && "bg-muted/60", !matched && "opacity-60",
              item.kind === "initiative" && "bg-muted/20")}
            onClick={() => onSelect(item.id)}>
            <div className="flex min-w-0 items-center gap-1.5" style={{ paddingLeft: depth * 18 }}>
              <button type="button" aria-label={hasChildren ? (isCollapsed ? "Expand" : "Collapse") : undefined}
                disabled={filterActive}
                title={filterActive ? "Clear the filters to collapse" : undefined}
                className={cn("flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted disabled:opacity-40", !hasChildren && "invisible")}
                onClick={(e) => { e.stopPropagation(); onToggle(item.id); }} tabIndex={hasChildren && !filterActive ? 0 : -1}>
                {isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </button>
              <StatusIcon status={item.status} />
              <div className="min-w-0">
                <div className="flex min-w-0 items-center gap-2">
                  <span className={cn("truncate text-sm", container ? "font-semibold" : "font-medium",
                    item.status === "complete" && !container && "text-muted-foreground line-through decoration-muted-foreground/50")}>
                    {item.title}
                  </span>
                  <KindTag item={item} />
                </div>
                <div className="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                  {container && total > 0 && <span className="tabular-nums">{done}/{total} done</span>}
                  {item.rollup && item.rollup.overdue > 0 && <span className="text-destructive">{item.rollup.overdue} overdue</span>}
                  {item.assignee && <span className="inline-flex items-center gap-0.5"><User className="h-3 w-3" />{item.assignee}</span>}
                  {item.waiting_on.length > 0 && <span className="inline-flex items-center gap-0.5 text-amber-700 dark:text-amber-400"><Link2 className="h-3 w-3" />waiting on {item.waiting_on.length}</span>}
                  <ApprovalTag state={item.approval_state} />
                  <EvidenceBadge evidence={item.evidence} conflict={item.claim_conflict} />
                </div>
              </div>
            </div>
            {!compact && <div className="hidden md:block"><PriorityChip priority={item.priority} /></div>}
            <ProgressBar value={displayProgress(item)} status={item.status} className="col-start-1 md:col-start-auto" />
            <div className="text-right"><Deadline item={item} now={now} /></div>
          </li>
        );
      })}
    </ul>
  );
}

// ── Board ───────────────────────────────────────────────────────────────────

export function BoardView({ items, filters, now, byId, onSelect, onMove, busy }: {
  items: Item[]; filters: Filters; now: number; byId: Map<string, Item>; onSelect: Select;
  onMove: (item: Item, status: ItemStatus) => void; busy: boolean;
}) {
  const cols = useMemo(() => boardColumns(items, filters), [items, filters]);
  const [over, setOver] = useState<ItemStatus | null>(null);
  const top = (it: Item): Item | undefined => {
    let cur = it.parent_id ? byId.get(it.parent_id) : undefined;
    for (let i = 0; cur?.parent_id && i < 64; i++) cur = byId.get(cur.parent_id);
    return cur;
  };
  const drop = (status: ItemStatus) => (e: DragEvent) => {
    e.preventDefault();
    setOver(null);
    const it = byId.get(e.dataTransfer.getData("text/plain"));
    if (it && it.status !== status) onMove(it, status);
  };
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" data-testid="board-view">
      {BOARD_COLUMNS.map((s) => (
        <section key={s} aria-label={STATUS_META[s].label}
          className={cn("flex min-h-[8rem] flex-col rounded-lg border bg-muted/20", over === s && "ring-2 ring-ring")}
          onDragOver={(e) => { e.preventDefault(); setOver(s); }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(null); }}
          onDrop={drop(s)}>
          <header className="flex items-center justify-between border-b px-3 py-2">
            <span className="flex items-center gap-1.5 text-sm font-medium"><StatusIcon status={s} />{STATUS_META[s].label}</span>
            <span className="text-xs tabular-nums text-muted-foreground">{cols[s].length}</span>
          </header>
          <div className="flex flex-1 flex-col gap-2 p-2">
            {cols[s].length === 0 && <p className="px-1 py-3 text-center text-xs text-muted-foreground">Nothing here</p>}
            {cols[s].map((it) => {
              const root = top(it);
              return (
                <button key={it.id} type="button" draggable={!busy} data-testid={`card-${it.id}`}
                  onDragStart={(e) => e.dataTransfer.setData("text/plain", it.id)}
                  onClick={() => onSelect(it.id)}
                  className="rounded-md border border-l-4 bg-background p-2 text-left shadow-sm transition-colors hover:bg-muted/40"
                  style={{ borderLeftColor: PRIORITY_META[it.priority].stripe ?? "hsl(var(--border))" }}>
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-sm font-medium leading-snug">{it.title}</span>
                    <PriorityChip priority={it.priority} compact />
                  </div>
                  {root && <div className="mt-0.5 truncate text-xs text-muted-foreground">{root.title}</div>}
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <KindTag item={it} />
                    <Deadline item={it} now={now} />
                    {it.assignee && <span className="inline-flex items-center gap-0.5 text-xs text-muted-foreground"><User className="h-3 w-3" />{it.assignee}</span>}
                    <ApprovalTag state={it.approval_state} />
                    <EvidenceBadge evidence={it.evidence} conflict={it.claim_conflict} />
                  </div>
                  {it.status === "in_progress" && it.progress != null && <ProgressBar value={it.progress} status={it.status} className="mt-1.5" />}
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

// ── Timeline ────────────────────────────────────────────────────────────────

const ROW_H = 30;

export function TimelineView({ rows, now, onSelect, selected }: {
  rows: TreeRow[]; now: number; onSelect: Select; selected: string | null;
}) {
  const tl = useMemo(() => buildTimeline(rows, now), [rows, now]);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  if (rows.length === 0) return <Empty text="No items match these filters." />;
  if (!tl.domain) {
    return <Empty text="Not enough dated items for a timeline — give at least two items a deadline. The tree view lists everything." />;
  }
  const hovered = hover ? tl.rows.find((r) => r.item.id === hover.id)?.item : undefined;
  const pct = (v: number) => `${(v * 100).toFixed(3)}%`;
  return (
    <div className="space-y-2" data-testid="timeline-view">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-5 rounded-sm border-[1.5px] border-muted-foreground" />Open</span>
        {(["in_progress", "blocked", "complete"] as ItemStatus[]).map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-5 rounded-sm" style={{ background: STATUS_META[s].fill ?? undefined }} />{STATUS_META[s].label}</span>
        ))}
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rotate-45 border-[1.5px] border-foreground" />Gate / checkpoint</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-foreground" />Due date, no start</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-3 border-l-2 border-dashed border-foreground/70" />Now</span>
      </div>
      <div className="relative overflow-x-auto rounded-lg border">
        <div className="grid min-w-[720px] grid-cols-[minmax(12rem,18rem)_1fr]">
          <div className="border-b border-r bg-muted/30 px-2 py-1 text-xs text-muted-foreground">Item</div>
          <div className="relative h-7 border-b bg-muted/30">
            {tl.ticks.map((t) => (
              <span key={t.at} className="absolute top-1.5 -translate-x-1/2 whitespace-nowrap text-[11px] text-muted-foreground" style={{ left: pct(t.x) }}>{t.label}</span>
            ))}
          </div>
          {tl.rows.map((r) => {
            const { start, end } = itemSpan(r.item);
            const fill = STATUS_META[r.item.status].fill;
            const container = KIND_META[r.item.kind].container;
            return (
              <div key={r.item.id} className="contents">
                <button type="button" onClick={() => onSelect(r.item.id)}
                  className={cn("flex min-w-0 items-center gap-1.5 border-r px-2 text-left text-xs hover:bg-muted/40", selected === r.item.id && "bg-muted/60")}
                  style={{ height: ROW_H, paddingLeft: 8 + r.depth * 14 }}>
                  <StatusIcon status={r.item.status} className="h-3.5 w-3.5" />
                  <span className={cn("truncate", container && "font-semibold")}>{r.item.title}</span>
                </button>
                <div className="relative border-b border-dashed border-b-transparent" style={{ height: ROW_H }}>
                  {tl.ticks.map((t) => <span key={t.at} aria-hidden className="absolute inset-y-0 border-l" style={{ left: pct(t.x), borderColor: "var(--viz-grid)" }} />)}
                  {r.x0 !== null && r.x1 !== null && (
                    <span role="img" data-testid={`bar-${r.item.id}`}
                      aria-label={`${r.item.title}: ${STATUS_META[r.item.status].label}, ${formatUtc(start ? new Date(start).toISOString() : null)} to ${formatUtc(end ? new Date(end).toISOString() : null)}`}
                      onMouseEnter={(e) => setHover({ id: r.item.id, x: e.clientX, y: e.clientY })}
                      onMouseMove={(e) => setHover({ id: r.item.id, x: e.clientX, y: e.clientY })}
                      onMouseLeave={() => setHover(null)}
                      onClick={() => onSelect(r.item.id)}
                      className={cn("absolute top-1/2 -translate-y-1/2 cursor-pointer rounded", container ? "h-2.5" : "h-4",
                        !fill && "border-[1.5px] border-muted-foreground bg-background",
                        r.item.overdue && "outline outline-2 outline-offset-1 outline-destructive")}
                      style={{ left: pct(r.x0), width: `max(4px, ${pct(r.x1 - r.x0)})`, background: fill ?? undefined }} />
                  )}
                  {r.point !== null && (
                    <span role="img" data-testid={`point-${r.item.id}`}
                      aria-label={`${r.item.title}: ${STATUS_META[r.item.status].label}, ${formatUtc(end ? new Date(end).toISOString() : null)}`}
                      onMouseEnter={(e) => setHover({ id: r.item.id, x: e.clientX, y: e.clientY })}
                      onMouseLeave={() => setHover(null)}
                      onClick={() => onSelect(r.item.id)}
                      className={cn("absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 cursor-pointer border-[1.5px] border-foreground",
                        r.pointKind === "milestone" ? "rotate-45" : "rounded-full",
                        r.item.overdue && "outline outline-2 outline-offset-1 outline-destructive")}
                      style={{ left: pct(r.point), background: fill ?? "hsl(var(--background))" }} />
                  )}
                  {tl.now !== null && <span aria-hidden className="pointer-events-none absolute inset-y-0 border-l-2 border-dashed border-foreground/70" style={{ left: pct(tl.now) }} />}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {hover && hovered && (
        <div role="tooltip" className="pointer-events-none fixed z-50 max-w-xs rounded-md border bg-popover px-3 py-2 text-xs shadow-md"
          style={{ left: hover.x + 12, top: hover.y + 12 }}>
          <div className="font-medium text-foreground">{hovered.title}</div>
          <div className="mt-1 flex items-center gap-2"><StatusBadge status={hovered.status} /><PriorityChip priority={hovered.priority} /></div>
          <div className="mt-1 text-muted-foreground">
            {hovered.start_at && <>Start {formatUtc(hovered.start_at)}<br /></>}
            {hovered.deadline ? <>Due {formatDeadline(hovered.deadline)}</> : hovered.completed_at && <>Completed {formatUtc(hovered.completed_at)}</>}
          </div>
          <div className="mt-1 tabular-nums text-muted-foreground">Progress {displayProgress(hovered)}%</div>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        {tl.datable} of {rows.length} items are dated; undated items are listed without a bar. Bars run from start to deadline.
      </p>
    </div>
  );
}

// ── Table ───────────────────────────────────────────────────────────────────

type SortKey = "title" | "kind" | "status" | "priority" | "assignee" | "deadline" | "progress" | "updated_at";

export function TableView({ items, now, onSelect, selected }: {
  items: Item[]; now: number; onSelect: Select; selected: string | null;
}) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "deadline", dir: 1 });
  const sorted = useMemo(() => {
    const val = (it: Item): string | number => {
      switch (sort.key) {
        case "priority": return PRIORITY_META[it.priority].rank;
        case "status": return ["blocked", "in_progress", "open", "complete", "archived"].indexOf(it.status);
        case "progress": return displayProgress(it);
        case "deadline": return it.deadline ? Date.parse(it.deadline) : Number.POSITIVE_INFINITY;
        case "updated_at": return Date.parse(it.updated_at);
        default: return String(it[sort.key] ?? "").toLowerCase();
      }
    };
    return [...items].sort((a, b) => {
      const x = val(a), y = val(b);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [items, sort]);
  if (items.length === 0) return <Empty text="No items match these filters." />;
  const th = (key: SortKey, label: string, right = false) => (
    <th className={cn("px-3 py-2 font-medium", right && "text-right")}
      aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="inline-flex items-center gap-1 hover:text-foreground"
        onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 }))}>
        {label}{sort.key === key && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </th>
  );
  return (
    <div className="overflow-x-auto rounded-lg border" data-testid="table-view">
      <table className="w-full min-w-[860px] text-sm">
        <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
          <tr>{th("title", "Title")}{th("kind", "Kind")}{th("status", "Status")}{th("priority", "Priority")}
            {th("assignee", "Assignee")}{th("deadline", "Deadline")}{th("progress", "Progress")}{th("updated_at", "Updated", true)}</tr>
        </thead>
        <tbody className="divide-y">
          {sorted.map((it) => (
            <tr key={it.id} tabIndex={0} aria-selected={selected === it.id} onKeyDown={activate(() => onSelect(it.id))}
              className={cn("cursor-pointer hover:bg-muted/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring", selected === it.id && "bg-muted/60")}
              onClick={() => onSelect(it.id)}>
              <td className="max-w-[22rem] px-3 py-2"><div className="truncate font-medium">{it.title}</div></td>
              <td className="px-3 py-2"><KindTag item={it} /></td>
              <td className="px-3 py-2"><StatusBadge status={it.status} /></td>
              <td className="px-3 py-2"><PriorityChip priority={it.priority} /></td>
              <td className="px-3 py-2 text-xs">{it.assignee ?? <span className="text-muted-foreground">—</span>}</td>
              <td className="px-3 py-2"><Deadline item={it} now={now} /></td>
              <td className="w-40 px-3 py-2"><ProgressBar value={displayProgress(it)} status={it.status} /></td>
              <td className="whitespace-nowrap px-3 py-2 text-right text-xs text-muted-foreground">{formatUtc(it.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
