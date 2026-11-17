/**
 * Knowledge Graph → "New": a timeline of what was added or changed in a knowledge base.
 *
 * Reads GET /v1/console/plugins/corvin-knowledge/recent (read-only; the server joins the checkout's git history to
 * the graph). The look: a vertical rail with a glowing amber dot for an entry that appeared in the period and a
 * hollow ring for one that was changed, day headings that stay in view while you scroll, a status-coloured edge on
 * every card, and three tiles (new / changed / all) that double as the filter. Colours are the console's own
 * tokens (accent, the --viz-* status ramp through `colours`), so both themes work and nothing is hard-coded.
 */
import { useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle, ArrowUpRight, ClipboardList, Eye, FileText, GitCommitHorizontal, Layers, Lightbulb, ListChecks,
  Loader2, RefreshCw, Sparkles, StickyNote, Target,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import {
  DEFAULT_WINDOW, emptyCopy, groupByDay, MARKER_RECENT, own, relativeTime, showSummary, sortedTypes, typeLabel, WINDOW_CHOICES,
  type KindFilter, type RecentItem, type RecentPayload, type WindowDays,
} from "./recent-model";

const BASE_PATH = "/plugins/corvin-knowledge";
export const KEY_RECENT = ["corvin-knowledge", "recent"] as const;

const ICONS: Record<string, typeof FileText> = {
  decision: FileText, concept: Lightbulb, idea: Lightbulb, plan: ClipboardList, review: Eye,
  note: StickyNote, task: ListChecks, epic: Layers, initiative: Target,
};

interface Props {
  /** the project whose knowledge base to read; null = the tenant's bound one */
  project: string | null;
  scopeName: string;
  /** several knowledge bases are selected: the feed reads one at a time */
  joined: boolean;
  /** the page knows which knowledge base is meant (project list and settings are loaded) */
  ready: boolean;
  /** Refresh was pressed: the page refreshes its graph too, so an entry listed here can be opened */
  onRefresh?: () => void;
  /** status -> colour (the graph's own ramp) */
  colours: Record<string, string>;
  onOpen: (id: string) => void;
}

export function RecentFeed({ project, scopeName, joined, ready, onRefresh, colours, onOpen }: Props) {
  const lagTries = useRef(0);
  // the controls live in the URL (?days= &kind= &type=), so Back from an opened entry returns to the same list
  const [params, setParams] = useSearchParams();
  // how many entries the list asks for: 80 at first; "Show more" adds 80 at a time (the server caps it at 500)
  // a whole number between 80 and 500 whatever the URL says ("100.5", "1e2", "abc"): the server takes integers only
  const limit = Math.min(500, Math.max(80, Math.floor(Number(params.get("limit")) || 80)));
  const days = (WINDOW_CHOICES as readonly number[]).includes(Number(params.get("days"))) ? (Number(params.get("days")) as WindowDays) : DEFAULT_WINDOW;
  const kind: KindFilter = params.get("kind") === "new" ? "new" : params.get("kind") === "updated" ? "updated" : "all";
  const type = /^[a-z_]{1,24}$/.test(params.get("type") ?? "") ? (params.get("type") as string) : "";
  const setParam = (key: string, value: string | null) => setParams((p) => { const n = new URLSearchParams(p); if (value) n.set(key, value); else n.delete(key); return n; }, { replace: true });
  const q = useQuery({
    // waits until the page knows which knowledge base is meant (a shared link must not ask for one that turns out not to exist)
    enabled: !joined && ready,
    queryKey: [...KEY_RECENT, project ?? "bound", days, kind, type, limit],
    queryFn: ({ signal }) => {
      const qs = new URLSearchParams({ days: String(days) });
      if (project) qs.set("project", project);
      if (kind !== "all") qs.set("kind", kind);
      if (type) qs.set("type", type);
      qs.set("limit", String(limit));
      // the server may wait for the graph lock (15 s), index a stale project graph (60 s) and read the history (30 s): longer than that
      return api<RecentPayload>(`${BASE_PATH}/recent?${qs}`, { signal, timeoutMs: 120_000 });
    },
    // the tiles keep their numbers while a filter is being fetched
    placeholderData: (prev) => prev,
    retry: false,
    // the list only changes when something is committed: no polling (the console's default 15 s interval would run a git
    // process and re-read the graph four times a minute for as long as the tab is open), a fresh look on focus or on Refresh
    // the one exception: while the graph lags the commits (a pull just brought entries the index has not seen), look again every
    // 8 seconds, at most four times; the projector re-indexes meanwhile
    refetchInterval: (query) => {
      const d = query.state.data;
      // only after a SUCCESS (a failed request keeps the last payload; looking again at it would never end)
      const lagging = !!d && (d.state === "busy" || (!!d.graph_stale && !d.index_error));
      return query.state.status === "success" && lagging && lagTries.current < 4 ? 8_000 : false;
    },
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  });
  const answer = q.data;
  // a "busy" answer to a refetch must not wipe a list that is already on screen: the last good list for THIS filter stays
  // the same list at a smaller or equal limit is a prefix of this one: a busy answer may keep it on screen
  const sig = `${project ?? "bound"}|${days}|${kind}|${type}`;
  const lastGood = useRef<{ sig: string; limit: number; data: RecentPayload } | null>(null);
  // only a REAL answer for this filter is remembered: while a new filter loads, `answer` is still the previous list (placeholder)
  if (answer && !q.isPlaceholderData && (answer.state === "ok" || answer.state === "empty")) lastGood.current = { sig, limit, data: answer };
  const keep = lastGood.current && lastGood.current.sig === sig && lastGood.current.limit <= limit;
  const data = answer?.state === "busy" && keep ? lastGood.current!.data : answer;
  const busy = answer?.state === "busy";
  const nowMs = useMemo(() => Date.now(), [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const items = data?.items ?? [];
  const types = useMemo(() => sortedTypes(data?.type_counts ?? {}), [data]);
  const groups = useMemo(() => groupByDay(items, nowMs), [items, nowMs]);
  const filtered = kind !== "all" || type !== "";
  useEffect(() => { lagTries.current = answer && (answer.state === "busy" || answer.graph_stale) ? lagTries.current + 1 : 0; }, [answer]);
  useEffect(() => { lagTries.current = 0; }, [days, kind, type, project, limit]);            // a new list gets its own looks

  // a type kept from another period or knowledge base that this one does not have would show an empty list with no chip to clear it
  useEffect(() => {
    if (data && !q.isPlaceholderData && type && !Object.prototype.hasOwnProperty.call(data.type_counts, type)) setParam("type", null);
  }, [data, q.isPlaceholderData, type]); // eslint-disable-line react-hooks/exhaustive-deps

  if (joined) {
    return (
      <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground" data-testid="recent-joined-note">
        <Sparkles className="mx-auto mb-3 h-6 w-6 text-accent" aria-hidden />
        <p className="font-medium text-foreground">Choose one knowledge base to see what is new</p>
        <p className="mt-1">The timeline reads one knowledge base at a time. Pick Corvin or a project in the list above.</p>
      </div>
    );
  }

  return (
    <div className="space-y-5" data-testid="knowledge-recent">
      {/* header: what this is, and how far back to look */}
      <div className="relative overflow-hidden rounded-2xl border border-border bg-gradient-to-br from-accent/[0.12] via-card to-card p-5">
        <div className="flex flex-wrap items-start gap-4">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent ring-1 ring-accent/30" aria-hidden>
            <Sparkles className="h-5 w-5" />
          </span>
          <div className="min-w-[220px] flex-1">
            <h2 className="text-lg font-semibold leading-tight">What&apos;s new in {scopeName}</h2>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground" data-testid="recent-hint">{MARKER_RECENT}</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded-lg border border-border bg-background/60 p-0.5" role="group" aria-label="Period">
              {WINDOW_CHOICES.map((d) => (
                <button key={d} type="button" aria-pressed={days === d} onClick={() => setParams((p) => { const n = new URLSearchParams(p); if (d === DEFAULT_WINDOW) n.delete("days"); else n.set("days", String(d)); n.delete("kind"); n.delete("type"); return n; }, { replace: true })}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition-colors motion-reduce:transition-none ${days === d ? "bg-accent text-accent-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
                  {d} days
                </button>
              ))}
            </div>
            <Button size="sm" variant="ghost" onClick={() => { lagTries.current = 0; onRefresh?.(); q.refetch(); }} disabled={q.isFetching} aria-label="Refresh the list" title="Refresh">
              {q.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        {/* the tiles are the filter: a click asks the server for exactly what the tile counts (within the active type) */}
        {data && data.state === "ok" && (
          <div className="mt-5 grid grid-cols-3 gap-3" role="group" aria-label="Show">
            <Tile testId="recent-tile-new" label="New" value={data.new_count} hint="appeared in this period" active={kind === "new"} accent onClick={() => setParam("kind", kind === "new" ? null : "new")} />
            <Tile testId="recent-tile-updated" label="Changed" value={data.updated_count} hint="existed before, edited since" active={kind === "updated"} onClick={() => setParam("kind", kind === "updated" ? null : "updated")} />
            <Tile testId="recent-tile-all" label="Entries touched" value={data.total} hint={type ? `all ${typeLabel(type).toLowerCase()} entries` : "all of them"} active={kind === "all"} onClick={() => setParam("kind", null)} />
          </div>
        )}
        {data?.state === "ok" && types.length > 1 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5" role="group" aria-label="Type">
            <Chip active={type === ""} onClick={() => setParam("type", null)}>All types</Chip>
            {types.map((t) => <Chip key={t.type} active={type === t.type} onClick={() => setParam("type", type === t.type ? null : t.type)}>{typeLabel(t.type)} <span className="font-mono opacity-70">{t.count}</span></Chip>)}
          </div>
        )}
      </div>

      {/* a live region that stays mounted: only its text changes, so a screen reader announces the notice when it appears */}
      <div role="status" aria-live="polite" data-testid="recent-live">
        {busy ? <span className="sr-only">The graph is being rebuilt. This list looks again a few times.</span> : null}
        {data?.index_error ? (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs" data-testid="recent-index-error">
            The graph could not be re-indexed ({data.index_error}). This list comes from the last graph and may miss the newest entries.
          </p>
        ) : data?.graph_stale && !busy && data.unindexed && data.unindexed > 0 ? (
          <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground" data-testid="recent-graph-lag">
            {data.unindexed_capped ? "At least " : ""}{data.unindexed} newer {data.unindexed === 1 ? "entry is" : "entries are"} not in the graph yet,
            so {data.unindexed_capped || data.unindexed !== 1 ? "they are" : "it is"} missing from this list. Press Refresh in a moment; if it stays like this, the knowledge base needs re-indexing (<code>kb index</code>).
          </p>
        ) : data?.graph_stale && !busy && data.unindexed_capped ? (
          <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground" data-testid="recent-graph-lag">
            Some newer files may not be in the graph yet, so they may be missing from this list. Press Refresh in a moment.
          </p>
        ) : null}
        {busy && data?.state !== "busy" ? (
          <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground" data-testid="recent-busy-note">
            The graph is being rebuilt; the list below is the last one that was read.
          </p>
        ) : null}
      </div>

      {q.isPending ? (
        <ul className="space-y-3" aria-busy="true" aria-label="Loading">
          {[0, 1, 2, 3].map((i) => <li key={i} className="h-[76px] animate-pulse rounded-xl border border-border bg-muted/40 motion-reduce:animate-none" />)}
        </ul>
      ) : q.isError ? (
        <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive" role="alert">
          <AlertCircle className="h-[18px] w-[18px] shrink-0" /> The list of changes could not be loaded.
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => q.refetch()}>Try again</Button>
        </div>
      ) : groups.length === 0 ? (
        <Empty state={data?.state ?? "empty"} days={days} filtered={filtered && (data?.total ?? 0) > 0} error={data?.error} />
      ) : (
        <>
          <div className={`relative ml-3 border-l border-border/70 pl-7 transition-opacity motion-reduce:transition-none ${q.isPlaceholderData ? "opacity-60" : ""}`} data-testid="recent-timeline">
            {groups.map((g, i) => (
              <section key={g.key} className="pb-5" data-testid="recent-day">
                <h3 className="sticky top-0 z-10 -ml-10 mb-3 flex items-center gap-2 bg-background/85 py-1.5 pl-10 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground backdrop-blur">
                  {g.label}
                  <span className="rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal text-foreground/70">{i === groups.length - 1 && data && data.matching > items.length ? `${g.items.length}+` : g.items.length}</span>
                </h3>
                <ol className="space-y-3">
                  {g.items.map((it) => <Entry key={`${it.id}-${it.changed_at}`} it={it} nowMs={nowMs} colour={own(colours, it.status)} onOpen={onOpen} />)}
                </ol>
              </section>
            ))}
          </div>
          <p className="text-xs text-muted-foreground" data-testid="recent-footer">
            {data && !q.isPlaceholderData && data.matching > items.length ? `Showing the newest ${items.length} of ${data.matching}${filtered ? " that match" : ""}. ` : ""}
            {data && !q.isPlaceholderData && data.matching > items.length && limit < 500 ? (
              <button type="button" className="underline underline-offset-2 hover:text-foreground" data-testid="recent-show-more"
                onClick={() => setParam("limit", String(Math.min(500, limit + 80)))}>Show {Math.min(80, 500 - limit, data.matching - items.length)} more</button>
            ) : null}
            {data && !q.isPlaceholderData && data.matching > items.length && limit >= 500 ? "The list stops at 500 entries. A kind or type filter shows the rest when it brings the matches under 500. " : ""}
            {data?.truncated ? "This knowledge base has a very long history: the oldest changes of the period may be missing. " : ""}
            Read-only view of {scopeName}: opening an entry never changes it. Your personal space is separate; switch to &ldquo;Only for me&rdquo; above to see it.
          </p>
        </>
      )}
    </div>
  );
}

function Tile({ label, value, hint, active, accent, onClick, testId }: { label: string; value: number; hint: string; active: boolean; accent?: boolean; onClick: () => void; testId: string }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active} data-testid={testId}
      className={`rounded-xl border p-3 text-left transition-all motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${active ? "border-accent bg-accent/10 shadow-sm" : "border-border bg-background/60 hover:border-accent/50"}`}>
      <span className="block text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{label}</span>
      <span className={`mt-0.5 block font-mono text-2xl font-semibold tabular-nums ${accent ? "text-accent" : "text-foreground"}`}>{value}</span>
      <span className="block truncate text-xs text-muted-foreground">{hint}</span>
    </button>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors motion-reduce:transition-none ${active ? "border-accent bg-accent/15 text-foreground" : "border-border text-muted-foreground hover:border-accent/50 hover:text-foreground"}`}>
      {children}
    </button>
  );
}

function Entry({ it, nowMs, colour, onOpen }: { it: RecentItem; nowMs: number; colour?: string; onOpen: (id: string) => void }) {
  const Icon = own(ICONS, it.type) ?? FileText;
  const isNew = it.kind === "new";
  return (
    <li className="relative" data-testid="recent-item" data-kind={it.kind}>
      {/* the rail's marker: a glowing dot = appeared in this period, a ring = changed */}
      <span aria-hidden className={`absolute -left-[37px] top-5 h-3 w-3 rounded-full ${isNew ? "bg-accent shadow-[0_0_0_4px_hsl(var(--accent)/0.22)]" : "border-2 border-muted-foreground/60 bg-background"}`} />
      <button type="button" onClick={() => onOpen(it.id)}
        className="group relative block w-full overflow-hidden rounded-xl border border-border bg-card/70 py-3 pl-5 pr-4 text-left transition-all hover:border-accent/50 hover:bg-card hover:shadow-md motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span aria-hidden className="absolute inset-y-0 left-0 w-1" style={{ background: colour ?? "hsl(var(--border))" }} />
        <div className="flex items-start gap-3">
          <span className="sr-only">Open </span>
          <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground group-hover:text-accent" aria-hidden />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
              <span className="font-mono text-muted-foreground">{it.label}</span>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${isNew ? "bg-accent/15 text-accent" : "bg-muted text-muted-foreground"}`}>{isNew ? "New" : "Changed"}</span>
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: colour ?? "hsl(var(--border))" }} />{it.status.replace("_", " ")}
              </span>
              <span className="text-muted-foreground/80">{typeLabel(it.type)}</span>
              <time className="ml-auto text-muted-foreground" dateTime={it.changed_at} title={new Date(it.changed_at).toLocaleString("en-US")}>{relativeTime(it.changed_at, nowMs)}</time>
            </div>
            <p className="mt-1 font-medium leading-snug text-foreground">{it.title}</p>
            {(showSummary(it) || it.commits > 1) && (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                <GitCommitHorizontal className="h-3.5 w-3.5 shrink-0" aria-hidden />
                {showSummary(it) && <span className="truncate">{it.summary}</span>}
                {it.commits > 1 && <span className="shrink-0">{showSummary(it) ? "· " : ""}{it.commits} changes</span>}
              </p>
            )}
          </div>
          <ArrowUpRight className="mt-0.5 h-4 w-4 shrink-0 text-accent opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none" aria-hidden />
        </div>
      </button>
    </li>
  );
}

function Empty({ state, days, filtered, error }: { state: RecentPayload["state"]; days: number; filtered: boolean; error?: string }) {
  const c = emptyCopy(state, days, filtered);
  return (
    <div className="rounded-xl border border-dashed border-border px-4 py-12 text-center" data-testid="recent-empty" data-state={state}>
      <Sparkles className="mx-auto mb-3 h-6 w-6 text-muted-foreground" aria-hidden />
      <p className="font-medium text-foreground">{c.title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">{c.hint}</p>
      {state === "error" && error && <p className="mt-2 font-mono text-xs text-muted-foreground">{error}</p>}
    </div>
  );
}
