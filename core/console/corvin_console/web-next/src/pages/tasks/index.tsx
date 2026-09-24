/**
 * Tasks — every work item in the Task-Tracking store, in five views.
 *
 * Data: GET /v1/console/task-tracking/items (routes/task_tracking.py →
 * core/task_tracking), polled every 5 s. The server derives rollups,
 * overdue flags and evidence on each read; this page groups, filters and lays
 * them out. Every write goes through the audited API (optimistic `version`).
 *
 *   Tree (default) · Board · Timeline · Table — work items
 *   Activity       — runtime runs (chat, forge, ACS …) read-only from
 *                    their own stores; a run can be LINKED to an item, never copied.
 *
 * View, filters and the selected item live in the URL, so a view can be linked.
 * An empty store renders an empty state (with an import offer when an
 * initiatives.json exists) — never sample data.
 */
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, CalendarRange, Columns3, Download, FolderTree, Gavel, Link2, ListTree, Loader2, Network, Plus, RefreshCw,
  Search, ShieldCheck, Table2, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api/client";
import { getAllTasks, startInitiativesVerify, type TaskType, type UnifiedTask } from "@/lib/api/initiatives";
import {
  createTaskItem, getTaskItems, importInitiatives, linkTaskRun, patchTaskItem,
  type Item, type ItemCreateBody, type ItemStatus,
} from "@/lib/api/task-tracking";
import { cn } from "@/lib/utils";
import { RunningNow, SourceNotes, TaskTable, TypeChips } from "./activity-parts";
import { CreateDialog } from "./create-dialog";
import { DetailDrawer } from "./detail-drawer";
import {
  EMPTY_FILTERS, KIND_META, KIND_ORDER, PRIORITY_META, PRIORITY_ORDER, STATUS_META, WORK_KINDS, buildTree,
  filtersActive, filtersFromQuery, filtersToQuery, matches, type Filters,
} from "./encodings";
import { clockSkewMs, formatUtc } from "./format";
import { LIVE_QUERY, freshness } from "./live";
import { StatusIcon } from "./parts";
import { BoardView, TableView, TimelineView, TreeView } from "./views";

type View = "tree" | "board" | "timeline" | "table" | "graph" | "activity";
const VIEWS: { id: View; label: string; icon: typeof ListTree }[] = [
  { id: "tree", label: "Tree", icon: ListTree },
  { id: "board", label: "Board", icon: Columns3 },
  { id: "timeline", label: "Timeline", icon: CalendarRange },
  { id: "table", label: "Table", icon: Table2 },
  { id: "graph", label: "Graph", icon: Network },
  { id: "activity", label: "Activity", icon: FolderTree },
];
const ITEMS_KEY = ["task-tracking", "items"] as const;
// React Flow is heavy; load the graph only when the tab is opened.
const GraphView = lazy(() => import("./graph-view"));
const RUN_TYPES: TaskType[] = ["chat", "background", "acs", "workflow", "flow", "gateway", "forge", "compute", "scheduled", "skill_creator", "agent", "commit"];

function useNow(skewMs: number): number {
  const [now, setNow] = useState(() => Date.now() + skewMs);
  useEffect(() => {
    setNow(Date.now() + skewMs);
    const t = setInterval(() => setNow(Date.now() + skewMs), 30_000);
    return () => clearInterval(t);
  }, [skewMs]);
  return now;
}

/** "Live · updated 3s ago" — or, loudly, that the numbers on screen are NOT current. */
function FreshnessLine({ updatedAt, failing, fetching }: { updatedAt: number; failing: boolean; fetching: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1_000); return () => clearInterval(t); }, []);
  const f = freshness(updatedAt, failing, now);
  if (f.state === "loading") return null;
  const at = updatedAt ? formatUtc(new Date(updatedAt).toISOString()) : "never";
  return (
    <p data-testid="freshness" data-state={f.state} className={cn("mt-0.5 flex items-center gap-1.5 text-xs",
      f.state === "live" ? "text-muted-foreground" : f.state === "delayed" ? "text-amber-700 dark:text-amber-400" : "text-destructive")}>
      <span aria-hidden className={cn("h-2 w-2 rounded-full",
        f.state === "live" ? "bg-emerald-500" : f.state === "delayed" ? "bg-amber-500" : "bg-destructive")} />
      {f.state === "live" && <>Live · updated {f.ageS}s ago</>}
      {f.state === "delayed" && <>Updates delayed · last update {f.ageS}s ago</>}
      {f.state === "offline" && <>Connection lost — showing data from {at}. Retrying.</>}
      {fetching && <Loader2 className="h-3 w-3 animate-spin" aria-label="Refreshing" />}
    </p>
  );
}

function Kpi({ label, value, tone, active, onClick, testId }: {
  label: string; value: number | undefined; tone?: string; active?: boolean; onClick?: () => void; testId: string;
}) {
  return (
    <button type="button" data-testid={testId} onClick={onClick} aria-pressed={active}
      className={cn("rounded-lg border px-3 py-2 text-left transition-colors hover:bg-muted/40",
        active && "ring-2 ring-ring")}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-2xl font-semibold tabular-nums", tone)}>{value ?? "—"}</div>
    </button>
  );
}

function Chip({ on, onClick, children, testId }: { on: boolean; onClick: () => void; children: React.ReactNode; testId?: string }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick} data-testid={testId}
      className={cn("inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-xs transition-colors",
        on ? "border-foreground/50 bg-muted font-medium" : "text-muted-foreground hover:bg-muted/40")}>
      {children}
    </button>
  );
}

function toggle<T>(arr: T[], v: T): T[] {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
}

export default function TasksPage() {
  const qc = useQueryClient();
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const [params, setParams] = useSearchParams();
  const view = (VIEWS.some((v) => v.id === params.get("view")) ? params.get("view") : "tree") as View;
  const selectedId = params.get("item");
  const filters = useMemo(() => filtersFromQuery(params), [params]);

  const setQuery = useCallback((patch: { view?: View; item?: string | null; filters?: Filters }) => {
    const next = new URLSearchParams();
    const v = patch.view ?? view;
    if (v !== "tree") next.set("view", v);
    const f = patch.filters ?? filters;
    for (const [k, val] of Object.entries(filtersToQuery(f))) next.set(k, val);
    const it = patch.item === undefined ? selectedId : patch.item;
    if (it) next.set("item", it);
    setParams(next, { replace: true });
  }, [view, filters, selectedId, setParams]);
  const setFilters = (f: Filters) => setQuery({ filters: f });

  const [skew, setSkew] = useState(0);
  const q = useQuery({
    queryKey: [...ITEMS_KEY],
    queryFn: async ({ signal }) => {
      const b = await getTaskItems(signal);
      setSkew(clockSkewMs(b.server_time, Date.now()));
      return b;
    },
    ...LIVE_QUERY,
    retry: (n, err) => !(err instanceof ApiError && err.status === 404) && n < 2,
    retryDelay: 1_000,
  });
  const now = useNow(skew);
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const summary = q.data?.summary;

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const onToggle = (id: string) => setCollapsed((cur) => {
    const n = new Set(cur);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const rows = useMemo(() => buildTree(items, filters, collapsed), [items, filters, collapsed]);
  const flat = useMemo(() => items.filter((i) => matches(i, filters, now)), [items, filters, now]);

  const [createOpen, setCreateOpen] = useState(false);
  const [createParent, setCreateParent] = useState<Item | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: (body: ItemCreateBody) => createTaskItem(body, csrf),
    onSuccess: (it) => {
      setCreateOpen(false);
      qc.invalidateQueries({ queryKey: ["task-tracking"] });
      setQuery({ item: it.id });
    },
    onError: (e) => setCreateError(e instanceof Error ? e.message : "Could not create the item"),
  });
  const [boardError, setBoardError] = useState<string | null>(null);
  const move = useMutation({
    mutationFn: (v: { item: Item; status: ItemStatus }) => patchTaskItem(v.item.id, { version: v.item.version, status: v.status }, csrf),
    onSuccess: () => { setBoardError(null); qc.invalidateQueries({ queryKey: ["task-tracking"] }); },
    onError: (e) => {
      setBoardError(e instanceof ApiError && e.status === 409 ? "That item changed elsewhere — the board was reloaded." : e instanceof Error ? e.message : "Move failed");
      qc.invalidateQueries({ queryKey: ["task-tracking"] });
    },
  });
  const imp = useMutation({
    mutationFn: () => importInitiatives(csrf),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["task-tracking"] }),
  });
  const [verifyNote, setVerifyNote] = useState<string | null>(null);
  const verify = useMutation({
    mutationFn: () => startInitiativesVerify(csrf),
    onSuccess: (r) => setVerifyNote(r.started ? "Evidence check started — results appear within a few minutes." : r.running ? "An evidence check is already running." : (r.reason ?? "Nothing to verify.")),
    onError: (e) => setVerifyNote(e instanceof Error ? e.message : "Could not start the evidence check"),
  });

  // Activity: runtime runs from their own stores (never the initiative type — those are work items now).
  const [runTypes, setRunTypes] = useState<Set<TaskType>>(new Set());
  const [linkRun, setLinkRun] = useState<UnifiedTask | null>(null);
  const [linkTarget, setLinkTarget] = useState("");
  const [linkError, setLinkError] = useState<string | null>(null);
  const runTypeList = runTypes.size ? [...runTypes].sort() : RUN_TYPES;
  const runsQ = useQuery({
    queryKey: ["task-tracking", "runs", runTypeList.join(",")],
    queryFn: ({ signal }) => getAllTasks({ types: runTypeList, finishedLimit: 100 }, signal),
    enabled: view === "activity",
    ...LIVE_QUERY,
    placeholderData: (prev) => prev,
  });
  // "Running now": the operator's live agent sessions, above every view — what is
  // actually being worked on, even before it produced a commit or an item.
  const runningQ = useQuery({
    queryKey: ["task-tracking", "running-now"],
    queryFn: ({ signal }) => getAllTasks({ types: ["agent"], finishedLimit: 1 }, signal),
    ...LIVE_QUERY,
    placeholderData: (prev) => prev,
  });
  const runningNow = useMemo(
    () => (runningQ.data?.active ?? []).filter((r) => r.status === "running" || r.status === "paused"),
    [runningQ.data],
  );
  const link = useMutation({
    mutationFn: (v: { itemId: string; run: UnifiedTask }) => linkTaskRun(v.itemId, v.run.type, v.run.id, csrf),
    onSuccess: (_r, v) => {
      setLinkRun(null); setLinkError(null);
      qc.invalidateQueries({ queryKey: ["task-tracking"] });
      setQuery({ view: "tree", item: v.itemId });  // the drawer lives in the work views
    },
    onError: (e) => setLinkError(e instanceof Error ? e.message : "Could not link the run"),
  });

  const notAvailable = q.error instanceof ApiError && q.error.status === 404;
  // The drawer decides from its own detail query (which includes deleted items),
  // not from the list — so a just-deleted item stays open with its Restore button.
  const selected = selectedId;
  const workViews = view !== "activity";

  // A tile shows a count over WORK items, so its filter selects exactly those
  // (containers stay as context rows in the tree); the search text is kept.
  const kpiTarget = (patch: Partial<Filters>): Filters => ({ ...EMPTY_FILTERS, kinds: WORK_KINDS, ...patch, q: filters.q });
  const isKpi = (patch: Partial<Filters>) =>
    JSON.stringify(filtersToQuery(kpiTarget(patch))) === JSON.stringify(filtersToQuery(filters));
  const kpiFilter = (patch: Partial<Filters>) =>
    setFilters(isKpi(patch) ? { ...EMPTY_FILTERS, q: filters.q } : kpiTarget(patch));

  return (
    <div className="space-y-4 p-4 md:p-6" data-testid="tasks-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Tasks</h1>
          <p className="text-sm text-muted-foreground">
            Initiatives, epics and tasks from the task store
            {summary && <> · {items.length} items · {summary.initiatives_active} active initiatives</>}
          </p>
          <FreshnessLine updatedAt={q.dataUpdatedAt} failing={q.isError} fetching={q.isFetching} />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={verify.isPending || !csrf} onClick={() => verify.mutate()}
            title="Re-run the tests and path checks that back imported tasks">
            <ShieldCheck className="mr-1 h-4 w-4" />Verify evidence
          </Button>
          <Button size="sm" disabled={!csrf} onClick={() => { setCreateParent(null); setCreateError(null); setCreateOpen(true); }}>
            <Plus className="mr-1 h-4 w-4" />New item
          </Button>
        </div>
      </div>
      {verifyNote && (
        <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          {verifyNote}<button type="button" aria-label="Dismiss" onClick={() => setVerifyNote(null)}><X className="h-3 w-3" /></button>
        </p>
      )}

      {notAvailable && (
        <Card><CardContent className="py-6 text-sm text-muted-foreground">The task store is not available on this build.</CardContent></Card>
      )}
      {q.isError && !notAvailable && !q.data && (
        <Card><CardContent className="flex items-center gap-2 py-4 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" />Could not load tasks: {q.error instanceof Error ? q.error.message : "unknown error"}
          <Button size="sm" variant="ghost" onClick={() => q.refetch()}><RefreshCw className="mr-1 h-3.5 w-3.5" />Retry</Button>
        </CardContent></Card>
      )}
      {q.isLoading && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading tasks…</p>}

      {q.data && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" aria-label="Summary">
            <Kpi testId="kpi-open" label="Open" value={summary?.open} active={isKpi({ statuses: ["open"] })} onClick={() => kpiFilter({ statuses: ["open"] })} />
            <Kpi testId="kpi-in-progress" label="In progress" value={summary?.in_progress} active={isKpi({ statuses: ["in_progress"] })} onClick={() => kpiFilter({ statuses: ["in_progress"] })} />
            <Kpi testId="kpi-blocked" label="Blocked" value={summary?.blocked} active={isKpi({ statuses: ["blocked"] })} onClick={() => kpiFilter({ statuses: ["blocked"] })} />
            <Kpi testId="kpi-overdue" label="Overdue" value={summary?.overdue} tone={summary?.overdue ? "text-destructive" : undefined}
              active={isKpi({ overdueOnly: true })} onClick={() => kpiFilter({ overdueOnly: true })} />
            <Kpi testId="kpi-decisions" label="Decisions pending" value={summary?.approvals_pending}
              tone={summary?.approvals_pending ? "text-amber-700 dark:text-amber-400" : undefined}
              active={isKpi({ approvalsOnly: true, kinds: [] })} onClick={() => kpiFilter({ approvalsOnly: true, kinds: [] })} />
            <Kpi testId="kpi-done" label="Completed (7 days)" value={summary?.done_7d} active={isKpi({ doneRecently: true })} onClick={() => kpiFilter({ doneRecently: true })} />
          </div>
          <p className="text-xs text-muted-foreground">Counts cover work items (tasks, subtasks, issues, proposals); initiatives, epics and stories are containers.</p>

          {summary && summary.approvals_pending > 0 && !filters.approvalsOnly && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
              <Gavel className="h-4 w-4 text-amber-700 dark:text-amber-400" />
              <span>{summary.approvals_pending} {summary.approvals_pending === 1 ? "gate is" : "gates are"} waiting for a go / no-go decision.</span>
              <Button size="sm" variant="outline" className="ml-auto h-7" onClick={() => kpiFilter({ approvalsOnly: true, kinds: [] })}>Review</Button>
            </div>
          )}

          {workViews && runningNow.length > 0 && (
            <RunningNow runs={runningNow} onOpen={() => { setRunTypes(new Set<TaskType>(["agent"])); setQuery({ view: "activity" }); }} />
          )}

          <div className="flex flex-wrap items-center gap-2 border-b pb-2" role="tablist" aria-label="View">
            {VIEWS.map((v) => (
              <button key={v.id} type="button" role="tab" aria-selected={view === v.id} aria-controls="tasks-view-panel" data-testid={`view-${v.id}`}
                onClick={() => setQuery({ view: v.id })}
                className={cn("inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-sm transition-colors",
                  view === v.id ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:bg-muted/50")}>
                <v.icon className="h-4 w-4" />{v.label}
              </button>
            ))}
          </div>

          {workViews && view !== "graph" && items.length > 0 && (
            <div className="flex flex-wrap items-center gap-2" aria-label="Filters">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <input aria-label="Search tasks" placeholder="Search…" value={filters.q}
                  onChange={(e) => setFilters({ ...filters, q: e.target.value })}
                  className="h-7 w-44 rounded-full border bg-background pl-7 pr-2 text-xs" />
              </div>
              {(["open", "in_progress", "blocked", "complete"] as ItemStatus[]).map((s) => (
                <Chip key={s} testId={`filter-status-${s}`} on={filters.statuses.includes(s)} onClick={() => setFilters({ ...filters, statuses: toggle(filters.statuses, s) })}>
                  <StatusIcon status={s} className="h-3 w-3" />{STATUS_META[s].label}
                </Chip>
              ))}
              <span className="mx-1 h-4 border-l" aria-hidden />
              {PRIORITY_ORDER.map((p) => (
                <Chip key={p} on={filters.priorities.includes(p)} onClick={() => setFilters({ ...filters, priorities: toggle(filters.priorities, p) })}>
                  {PRIORITY_META[p].label}
                </Chip>
              ))}
              <select aria-label="Kind" className="h-7 rounded-full border bg-background px-2 text-xs"
                value={filters.kinds.length > 1 ? "__work" : filters.kinds[0] ?? ""}
                onChange={(e) => setFilters({ ...filters, kinds: e.target.value === "__work" ? WORK_KINDS : e.target.value ? [e.target.value as Item["kind"]] : [] })}>
                <option value="">All kinds</option>
                <option value="__work">Work items</option>
                {KIND_ORDER.map((k) => <option key={k} value={k}>{KIND_META[k].label}</option>)}
              </select>
              <Chip on={!filters.showClosed} onClick={() => setFilters({ ...filters, showClosed: !filters.showClosed })}>Hide completed</Chip>
              {filtersActive(filters) && (
                <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setFilters(EMPTY_FILTERS)}>
                  <X className="mr-1 h-3 w-3" />Clear
                </Button>
              )}
            </div>
          )}

          {boardError && <p role="alert" className="text-xs text-destructive">{boardError}</p>}

          {workViews && items.length === 0 && (
            <Card data-testid="tasks-empty">
              <CardContent className="space-y-3 py-8 text-center text-sm">
                <p className="font-medium">No tasks yet.</p>
                {q.data.import_available ? (
                  <>
                    <p className="text-muted-foreground">An initiatives.json exists for this tenant. Import its initiatives, tasks and gates once — the file itself stays untouched.</p>
                    <Button size="sm" disabled={imp.isPending || !csrf} onClick={() => imp.mutate()}>
                      <Download className="mr-1 h-4 w-4" />Import from initiatives.json
                    </Button>
                    {imp.isError && <p className="text-xs text-destructive">{imp.error instanceof Error ? imp.error.message : "Import failed"}</p>}
                  </>
                ) : (
                  <p className="text-muted-foreground">Create the first initiative or task with “New item”.</p>
                )}
              </CardContent>
            </Card>
          )}

          {workViews && items.length > 0 && (
            <div id="tasks-view-panel" role="tabpanel" className={cn("grid gap-4", selected && "lg:grid-cols-[minmax(0,1fr)_26rem]")}>
              <div className="min-w-0">
                {view === "tree" && <TreeView rows={rows} now={now} selected={selected} onSelect={(id) => setQuery({ item: id })}
                  collapsed={collapsed} onToggle={onToggle} filterActive={filtersActive(filters)} compact={Boolean(selected)} />}
                {view === "board" && <BoardView items={items} filters={filters} now={now} byId={byId} busy={move.isPending}
                  onSelect={(id) => setQuery({ item: id })} onMove={(item, status) => move.mutate({ item, status })} />}
                {view === "timeline" && <TimelineView rows={rows} now={now} selected={selected} onSelect={(id) => setQuery({ item: id })} />}
                {view === "table" && <TableView items={flat} now={now} selected={selected} onSelect={(id) => setQuery({ item: id })} />}
                {view === "graph" && (
                  <Suspense fallback={<p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading graph…</p>}>
                    <GraphView items={items} now={now} selectedId={selected} onSelect={(id) => setQuery({ item: id })} />
                  </Suspense>
                )}
              </div>
              {selected && (
                <DetailDrawer key={selected} id={selected} csrf={csrf} items={items}
                  onClose={() => setQuery({ item: null })} onSelect={(id) => setQuery({ item: id })}
                  onAddChild={(p) => { setCreateParent(p); setCreateError(null); setCreateOpen(true); }} />
              )}
            </div>
          )}

          {view === "activity" && (
            <div id="tasks-view-panel" role="tabpanel" className="space-y-4" data-testid="activity-view">
              <p className="text-sm text-muted-foreground">
                What ran on this install — agent sessions, commits, chat turns, background runs, ACS, forge and compute jobs, read from their own stores.
                Link a run to a task to see it in that task's details.
              </p>
              {runsQ.data && (
                <TypeChips types={runsQ.data.types.filter((t) => t.type !== "initiative")} view="active" selected={runTypes}
                  onToggle={(t) => setRunTypes((cur) => { const n = new Set(cur); if (n.has(t)) n.delete(t); else n.add(t); return n; })}
                  onAll={() => setRunTypes(new Set())} />
              )}
              {runsQ.isLoading && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Scanning run stores…</p>}
              {runsQ.data && (
                <>
                  <section className="space-y-2">
                    <h2 className="text-sm font-semibold">Active runs</h2>
                    <TaskTable tasks={runsQ.data.active} now={now} emptyText="Nothing is running."
                      action={(t) => <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => { setLinkRun(t); setLinkTarget(""); setLinkError(null); }}><Link2 className="mr-1 h-3 w-3" />Link</Button>} />
                  </section>
                  <section className="space-y-2">
                    <h2 className="text-sm font-semibold">Finished runs <span className="font-normal text-muted-foreground">— newest first, latest {runsQ.data.finished.length} of {runsQ.data.finished_total}</span></h2>
                    <TaskTable tasks={runsQ.data.finished} now={now} emptyText="No finished runs."
                      action={(t) => <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => { setLinkRun(t); setLinkTarget(""); setLinkError(null); }}><Link2 className="mr-1 h-3 w-3" />Link</Button>} />
                  </section>
                  <SourceNotes types={runsQ.data.types} />
                </>
              )}
            </div>
          )}
        </>
      )}

      <CreateDialog open={createOpen} onOpenChange={setCreateOpen} items={items} defaultParent={createParent}
        busy={create.isPending} error={createError} onCreate={(b) => create.mutate(b)} />

      <Dialog open={linkRun !== null} onOpenChange={(v) => { if (!v) setLinkRun(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Link run to a task</DialogTitle>
            <DialogDescription>{linkRun ? `${linkRun.type_label}: ${linkRun.title}` : ""}</DialogDescription>
          </DialogHeader>
          <select aria-label="Task to link" className="h-9 w-full rounded-md border bg-background px-2 text-sm" value={linkTarget}
            onChange={(e) => setLinkTarget(e.target.value)}>
            <option value="">Choose a task…</option>
            {items.filter((i) => !i.deleted_at).map((i) => <option key={i.id} value={i.id}>{KIND_META[i.kind].label}: {i.title}</option>)}
          </select>
          {linkError && <p role="alert" className="text-xs text-destructive">{linkError}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setLinkRun(null)}>Cancel</Button>
            <Button disabled={!linkTarget || link.isPending} onClick={() => linkRun && link.mutate({ itemId: linkTarget, run: linkRun })}>Link</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
