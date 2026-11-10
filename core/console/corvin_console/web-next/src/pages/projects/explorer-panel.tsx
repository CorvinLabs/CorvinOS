/**
 * The explorer (CONCEPT-0128): a live view of how the attached project is being worked on - who is working, what they run, which files
 * change, which tasks are in progress. A side panel next to the chat, in the family of the Changes panel. Read-only.
 *
 * Order, top to bottom, is the order a person asks: status line, Now, Tasks in progress, Activity. The panel first reads the snapshot
 * (a real HTTP status - EventSource cannot show one), then opens the stream with its cursor. The server ends a stream after 10 s so a
 * restart is never held; the browser reconnects by itself with Last-Event-ID, which is a normal rollover and is not shown as a problem.
 */
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FileDiff, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { activityStreamUrl, getActivity, type ActivityAgent, type ActivityEvent, type ActivitySnapshot } from "@/lib/api/projects";
import { getTaskItems } from "@/lib/api/task-tracking";
import { ago, linkTarget } from "./changes-logic";
import { INITIAL, announcement, elapsed, emptyFeed, filterRows, reduce, statusLine, workingAgents, type Filter } from "./activity-logic";

const ROLLOVER_GRACE_MS = 6000;

export function ExplorerPanel({ projectId, title, onClose, onOpenChanges }: { projectId: string; title: string; onClose: () => void; onOpenChanges: () => void }) {
  const [state, dispatch] = useReducer(reduce, INITIAL);
  const [filter, setFilter] = useState<Filter>("all");
  const [now, setNow] = useState(() => Date.now());
  const [spoken, setSpoken] = useState("");
  const prevAgents = useRef<ActivityAgent[]>([]);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => { closeRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(t); }, []);

  const [attempt, setAttempt] = useState(0);                 // "Try again" after a failed first look
  // snapshot first (a real status), then the stream
  useEffect(() => {
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    const ctl = new AbortController();
    const parse = <T,>(e: MessageEvent): T | null => { try { return JSON.parse(e.data) as T; } catch { return null; } };
    (async () => {
      let from: string | null = null;
      try {
        const snap = await getActivity(projectId, null, ctl.signal);
        if (cancelled) return;
        dispatch({ type: "snapshot", data: snap });
        from = `${snap.epoch}:${snap.seq}`;                    // not read back from state: a dispatch has not re-rendered yet
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        dispatch({ type: "conn", conn: err instanceof ApiError && err.status === 404 ? "gone" : "error" });
        return;
      }
      es = new EventSource(activityStreamUrl(projectId, from), { withCredentials: true });
      es.addEventListener("open", () => { if (timer) { clearTimeout(timer); timer = null; } dispatch({ type: "conn", conn: "live" }); });
      es.addEventListener("snapshot", (e) => { const d = parse<ActivitySnapshot>(e as MessageEvent); if (d) dispatch({ type: "snapshot", data: d }); });
      es.addEventListener("activity", (e) => { const d = parse<ActivityEvent>(e as MessageEvent); if (d) dispatch({ type: "activity", data: d }); });
      es.addEventListener("end", (e) => {
        const d = parse<{ reason: string }>(e as MessageEvent);
        if (!d) return;
        dispatch({ type: "end", reason: d.reason });
        if (d.reason === "project_removed") es.close();          // no reconnect to a project that is no longer in the list
      });
      es.addEventListener("error", () => {
        if (!es) return;
        if (es.readyState === EventSource.CLOSED) {
          if (timer) { clearTimeout(timer); timer = null; }        // final: no "reconnecting" 6 s later over the error
          dispatch({ type: "conn", conn: "error" });
          return;
        }     // a refusal (e.g. 429): no automatic retry
        if (!timer) timer = setTimeout(() => dispatch({ type: "conn", conn: "reconnecting" }), ROLLOVER_GRACE_MS);   // a normal rollover reopens within 2 s
      });
    })();
    return () => { cancelled = true; ctl.abort(); if (timer) clearTimeout(timer); es?.close(); };
  }, [projectId, attempt]);

  // screen-reader announcement: only an agent starting or finishing
  useEffect(() => {
    const said = announcement(prevAgents.current, state.agents);
    prevAgents.current = state.agents;
    if (said) setSpoken(said);
  }, [state.agents]);

  const tasks = useQuery({
    queryKey: ["projects", "explorer-tasks", projectId],
    queryFn: ({ signal }) => getTaskItems(signal, false, `project:${projectId}`),
    refetchInterval: 5000, retry: false,
  });
  const running = (tasks.data?.items ?? []).filter((t) => t.status === "in_progress");
  const working = workingAgents(state.agents);
  const idle = state.agents.filter((a) => a.state === "idle");
  const rows = useMemo(() => filterRows(state.rows, filter), [state.rows, filter]);
  const st = statusLine(state);
  const toneCls = st.tone === "ok" ? "bg-emerald-500" : st.tone === "wait" ? "bg-amber-500" : "bg-red-500";

  return (
    <aside role="dialog" aria-label={`Explorer for ${title}`} data-testid="explorer-panel"
      className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[34rem] flex-col border-l border-border bg-background shadow-xl">
      <header className="flex items-center gap-2 border-b px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium" data-testid="explorer-title">Explorer: {title}</div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground" data-testid="explorer-status" data-conn={state.conn}>
            <span className={`h-2 w-2 shrink-0 rounded-full ${toneCls} ${st.tone === "ok" ? "motion-safe:animate-pulse" : ""}`} aria-hidden />
            <span>{st.text}</span>
            {state.conn === "error" && (
              <button type="button" className="ml-1 underline" onClick={() => { dispatch({ type: "conn", conn: "connecting" }); setAttempt((n) => n + 1); }}
                data-testid="explorer-retry">Try again</button>
            )}
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={onOpenChanges} data-testid="explorer-open-changes" title="Open the Changes view"><FileDiff className="mr-1 h-4 w-4" aria-hidden /> Changes</Button>
        <Button ref={closeRef} variant="ghost" size="sm" onClick={onClose} aria-label="Close the explorer" data-testid="explorer-close"><X className="h-4 w-4" /></Button>
      </header>
      <div className="sr-only" aria-live="polite" data-testid="explorer-announce">{spoken}</div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
        {state.restarted && <p className="rounded border border-amber-600/40 p-2 text-xs" role="status" data-testid="explorer-restarted">The console restarted, so the feed started again. Nothing was lost on disk.</p>}
        {state.gap && <p className="rounded border border-border p-2 text-xs text-muted-foreground" role="status" data-testid="explorer-gap">Some events were missed while the connection was down.</p>}

        <section aria-label="Now" data-testid="explorer-now">
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Now</h3>
          {state.conn === "connecting" && state.agents.length === 0 && <Skeleton className="h-14 w-full" />}
          {working.length === 0 && state.conn !== "connecting" && <p className="text-sm text-muted-foreground" data-testid="explorer-now-empty">Nobody is working on this project right now.</p>}
          <ul className="space-y-2">
            {working.map((a) => (
              <li key={a.session_id} className="rounded-lg border p-2" data-testid="explorer-agent" data-state="working">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{a.title || "A chat"}</span>
                  <span className="shrink-0 text-xs text-muted-foreground" data-testid="agent-elapsed">Working for {elapsed(a.since, now)}</span>
                </div>
                <div className="min-w-0 break-words text-xs text-muted-foreground" data-testid="agent-tool">{a.tool ? `Now: ${a.tool}${a.detail ? ` ${a.detail}` : ""}` : "Thinking"} · {a.tools} {a.tools === 1 ? "tool call" : "tool calls"}</div>
              </li>
            ))}
            {idle.map((a) => (
              <li key={a.session_id} className="break-words rounded-lg border border-dashed p-2 text-xs text-muted-foreground" data-testid="explorer-agent" data-state="idle">
                {a.title || "A chat"}: {a.ok === false ? "stopped" : "finished"} {a.finished ? ago(a.finished, now) : ""}
              </li>
            ))}
          </ul>
        </section>

        <section aria-label="Tasks in progress" data-testid="explorer-tasks">
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Tasks in progress</h3>
          {tasks.isLoading && <Skeleton className="h-8 w-full" />}
          {tasks.data && running.length === 0 && <p className="text-sm text-muted-foreground" data-testid="explorer-tasks-empty">No task of this project is in progress.</p>}
          {tasks.error && (tasks.error instanceof ApiError && tasks.error.status === 404
            ? <p className="text-sm text-muted-foreground" data-testid="explorer-tasks-none">This project has no task board yet. Switch on the knowledge base in its settings to get one.</p>
            : <p className="text-xs text-muted-foreground" data-testid="explorer-tasks-error">The board could not be read just now.</p>)}
          <ul className="space-y-1">
            {running.map((t) => (
              <li key={t.id}><Link to={`/app/initiatives?scope=project:${encodeURIComponent(projectId)}`} className="block truncate rounded border px-2 py-1 text-sm hover:bg-muted" data-testid="explorer-task">{t.title}</Link></li>
            ))}
          </ul>
        </section>

        <section aria-label="Activity" data-testid="explorer-activity">
          <div className="mb-1 flex items-center justify-between">
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Activity</h3>
            <div className="flex gap-1" role="group" aria-label="Filter the activity">
              {(["all", "tools", "files"] as const).map((f) => (
                <button key={f} type="button" onClick={() => setFilter(f)} aria-pressed={filter === f} data-testid="explorer-filter" data-filter={f}
                  className={`rounded-full border px-2 py-0.5 text-xs ${filter === f ? "border-primary bg-primary/10" : "border-border text-muted-foreground"}`}>
                  {f === "all" ? "All" : f === "tools" ? "Tools" : "Files"}
                </button>
              ))}
            </div>
          </div>
          {rows.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="explorer-empty">{emptyFeed(state, filter)}</p>
          ) : (
            <ul className="rounded-md border" data-testid="explorer-feed">
              {rows.map((r) => (
                <li key={r.key} className="flex items-start gap-2 border-b px-3 py-1.5 text-sm last:border-b-0" data-testid="explorer-row" data-kind={r.kind}>
                  <span className="mt-0.5 w-10 shrink-0 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{r.kind === "tool" ? "Tool" : r.kind === "file" ? "File" : "Chat"}</span>
                  <span className={`min-w-0 flex-1 break-words ${r.tone === "bad" ? "text-red-800 dark:text-red-300" : ""}`}>
                    {r.text}{r.count > 1 && <span className="ml-1 text-xs text-muted-foreground" data-testid="row-count">×{r.count}</span>}
                    {r.links?.map((l) => <Link key={l.id} to={linkTarget(projectId, { id: l.id, kind: l.kind as "task" | "knowledge" })} className="ml-1 rounded-full border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground hover:text-foreground" data-testid="explorer-link">{l.id}</Link>)}
                  </span>
                  <span className="shrink-0 text-[11px] text-muted-foreground" title={new Date(r.ts * 1000).toLocaleString("en-US")}>{ago(r.ts, now) || "just now"}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground" data-testid="explorer-footnote">A live view, kept in memory since the console started. The record of what happened is the chat and the project's git history.</p>
        </section>
      </div>
    </aside>
  );
}
