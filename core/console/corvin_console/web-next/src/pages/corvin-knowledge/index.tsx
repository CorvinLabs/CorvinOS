/**
 * Knowledge Graph — the console panel of the contributor plugin
 * plugins/contributor/knowledge_management/corvin_knowledge (ADR-0892).
 *
 * Mounts only while the plugin is installed AND enabled: the capability
 * manifest lists the panel (route `corvin-knowledge`, group Marketplace) and
 * manifestPanelRoutes resolves the component by name — like VideoProducerPage.
 *
 * Backend: the console's own `/v1/console/plugins/corvin-knowledge/*` routes
 * (graph read from the configured checkout's graph/*.jsonl; config; git sync).
 * Every call goes through `api()` (session cookie, CSRF on writes). The panel
 * is a port of the plugin's shipped web-next sources onto the console's tokens:
 * no hard-coded light palette, no bare fetch, no console.log placeholders.
 */
import { useCallback, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, Network as NetworkIcon, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { GraphCanvas } from "./GraphCanvas";
import { DocumentPane } from "./DocumentPane";
import { buildLinkIndex } from "./link-resolver";
import { neighbourhood, newestDecisionKey, searchNodes } from "./graph-model";

export interface KnowledgeEntity { id: string; label?: string; file?: string; type: string; title: string; status: string; tags: string[] }
export interface KnowledgeRelation { from_id: string; to_id: string; relation: string }
export interface KnowledgeGraph { entities: KnowledgeEntity[]; relations: KnowledgeRelation[] }
export interface KnowledgeConfig {
  repo_path: string; remote_url: string; auto_sync_on_query: boolean; consistency_level: "strict" | "warn" | "ignore";
}
export interface SyncResult { status: string; message: string; timestamp: string; conflicts: string[]; errors: string[] }

const BASE_PATH = "/plugins/corvin-knowledge";
const KEY_GRAPH = ["corvin-knowledge", "graph"] as const;
const KEY_CONFIG = ["corvin-knowledge", "config"] as const;

/** Deploy marker for the explorer (a string literal, see ADR-0885). */
export const MARKER_EXPLORER = "Click a node to read its document. Follow an id in the text to move through the graph.";

/** Rendered caption — also the deploy marker (a string literal, see ADR-0885). */
export const MARKER_KNOWLEDGE = "Decisions, concepts and ideas of the configured knowledge repository, as the graph its relations describe.";

/** Status -> colour. Two closed, nominal vocabularies (ADR-2108 decisions, ADR-2205
 *  tasks/containers) in one graph; shape already disambiguates them (`box` for
 *  decisions, `dot` for everything else, see GraphCanvas below), so a hue may be
 *  reused across the two without ambiguity. Decision colours reuse the ordinal
 *  `--viz-tier-*` ramp by IDENTITY, same as before this list grew (ADR-0761 notes
 *  that reuse is not a freshly validated palette). Task colours reuse the Tasks
 *  panel's own validated `--viz-status-*` tokens. */
const DECISION_STATUS_ORDER = ["proposed", "accepted", "frozen", "rejected", "superseded"] as const;
const TASK_STATUS_ORDER = ["open", "in_progress", "blocked", "done", "cancelled"] as const;
const STATUS_ORDER = [...DECISION_STATUS_ORDER, ...TASK_STATUS_ORDER] as const;
function statusColours(): Record<string, string> {
  const cs = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  return {
    // decisions (ADR-2108)
    proposed: v("--viz-tier-2", "#ca9b49"),
    accepted: v("--viz-tier-1", "#d6b171"),
    frozen: v("--viz-tier-3", "#775822"),
    rejected: v("--viz-status-blocked", "#b0306a"),
    superseded: v("--viz-baseline", "#8a8a8a"),
    // tasks + containers (ADR-2205)
    open: v("--viz-baseline", "#8a8a8a"),
    in_progress: v("--viz-status-progress", "#c08a1e"),
    blocked: v("--viz-status-blocked", "#b0306a"),
    done: v("--viz-status-complete", "#2a78c6"),
    cancelled: v("--viz-tier-3", "#775822"),
  };
}

export function CorvinKnowledgePage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const graph = useQuery({ queryKey: [...KEY_GRAPH], queryFn: ({ signal }) => api<KnowledgeGraph>(`${BASE_PATH}/graph`, { signal }), retry: false });
  const config = useQuery({ queryKey: [...KEY_CONFIG], queryFn: ({ signal }) => api<KnowledgeConfig>(`${BASE_PATH}/config`, { signal }), retry: false });

  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<"focus" | "all">("focus");
  const [hops, setHops] = useState<1 | 2>(1);
  // null = automatic: islands for the whole graph (a force layout of 1300 nodes clumps), force for a focus.
  const [layoutChoice, setLayoutChoice] = useState<"force" | "islands" | null>(null);
  const layoutMode = layoutChoice ?? (mode === "all" ? "islands" : "force");
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [draft, setDraft] = useState<KnowledgeConfig | null>(null);

  const entities = useMemo(() => graph.data?.entities ?? [], [graph.data]);
  const relations = useMemo(() => graph.data?.relations ?? [], [graph.data]);
  const types = useMemo(() => Array.from(new Set(entities.map((e) => e.type))).sort(), [entities]);
  const linkIndex = useMemo(() => buildLinkIndex(entities), [entities]);
  const colours = useMemo(() => statusColours(), [graph.data]); // eslint-disable-line react-hooks/exhaustive-deps

  // The selection is the URL (?node=<key>): browser Back/Forward is the reading history and a
  // link can be shared. With no node in the URL the newest decision is the start page.
  const nodeParam = params.get("node");
  const known = useMemo(() => new Set(entities.map((e) => e.id)), [entities]);
  // The parameter may be a node key (uid) or the human id of a hand-written link (ADR-2206).
  const resolvedParam = nodeParam ? (linkIndex.byKey.get(nodeParam) ?? linkIndex.byLabel.get(nodeParam) ?? null) : null;
  const unknownParam = nodeParam !== null && resolvedParam === null && entities.length > 0;
  const selectedKey = resolvedParam ?? newestDecisionKey(entities);
  const select = useCallback((key: string) => setParams({ node: key }), [setParams]);
  const canGoBack = location.key !== "default";

  const shown = useMemo(() => {
    if (mode === "focus" && selectedKey && known.has(selectedKey)) {
      const keep = neighbourhood(relations, selectedKey, hops);
      return { entities: entities.filter((e) => keep.has(e.id)), relations: relations.filter((r) => keep.has(r.from_id) && keep.has(r.to_id)) };
    }
    const keep = entities.filter((e) => (!type || e.type === type) && (!status || e.status === status));
    const ids = new Set(keep.map((e) => e.id));
    return { entities: keep, relations: relations.filter((r) => ids.has(r.from_id) && ids.has(r.to_id)) };
  }, [mode, hops, selectedKey, known, entities, relations, type, status]);
  const hits = useMemo(() => searchNodes(entities, search), [entities, search]);

  const sync = useMutation({
    mutationFn: (sync_type: "pull" | "push" | "both") => api<SyncResult>(`${BASE_PATH}/sync`, { method: "POST", csrf, body: { sync_type } }),
    onSuccess: (r) => {
      setMsg(r.status === "success" ? `Sync ${r.message.toLowerCase()} — the graph was reloaded.`
        : r.status === "conflict" ? "Sync stopped on a merge conflict — resolve it in the checkout." : `Sync failed: ${r.errors.join("; ")}`);
      qc.invalidateQueries({ queryKey: [...KEY_GRAPH] });
    },
    onError: (e) => setMsg(e instanceof ApiError && e.status === 400 ? "Sync not started — the repository path does not exist. Set it under Settings." : "Sync not started."),
  });
  const save = useMutation({
    mutationFn: (c: KnowledgeConfig) => api<{ status: string; config: KnowledgeConfig }>(`${BASE_PATH}/config`, { method: "POST", csrf, body: c }),
    onSuccess: () => { setMsg("Settings saved."); setDraft(null); qc.invalidateQueries({ queryKey: [...KEY_CONFIG] }); qc.invalidateQueries({ queryKey: [...KEY_GRAPH] }); },
    onError: () => setMsg("Settings not saved."),
  });

  const cfg = draft ?? config.data ?? null;
  // Every status the data carries — the four canonical ones first, then any
  // other the repository uses (the maintainer checkout has 200 entities
  // outside proposed/accepted/verified/superseded; hiding them would misstate
  // the total).
  const statuses = useMemo(() => {
    const seen = Array.from(new Set(entities.map((e) => e.status)));
    return [...STATUS_ORDER.filter((s) => seen.includes(s)), ...seen.filter((s) => !(STATUS_ORDER as readonly string[]).includes(s)).sort()];
  }, [entities]);
  const counts = statuses.map((s) => [s, entities.filter((e) => e.status === s).length] as const);

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6" data-testid="corvin-knowledge-panel">
      <div>
        <div className="flex items-center gap-3 mb-1">
          <NetworkIcon className="w-8 h-8 text-accent" />
          <h1 className="text-3xl font-bold">Knowledge Graph</h1>
        </div>
        <p className="text-muted-foreground">{MARKER_KNOWLEDGE}</p>
      </div>

      <Card>
        <CardContent className="py-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <span><span className="text-muted-foreground">Entities:</span> <span className="font-mono tabular-nums">{graph.isLoading ? "—" : graph.isError ? "unavailable" : entities.length}</span></span>
          <span><span className="text-muted-foreground">Relations:</span> <span className="font-mono tabular-nums">{graph.isLoading ? "—" : graph.isError ? "unavailable" : graph.data?.relations.length ?? 0}</span></span>
          {counts.map(([s, n]) => <span key={s} className="text-xs text-muted-foreground">{s} <span className="font-mono tabular-nums text-foreground">{n}</span></span>)}
          <span className="ml-auto text-xs text-muted-foreground">Repository: <span className="font-mono">{config.data?.repo_path ?? "—"}</span></span>
          <div className="flex gap-2">
            {(["pull", "push", "both"] as const).map((t) => (
              <Button key={t} size="sm" variant="outline" disabled={sync.isPending || !csrf} onClick={() => sync.mutate(t)}>
                {sync.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} {t === "both" ? "Sync" : t === "pull" ? "Pull" : "Push"}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>
      {msg && <p className="text-xs text-muted-foreground" data-testid="knowledge-msg">{msg}</p>}

      <Tabs defaultValue="graph">
        <TabsList>
          <TabsTrigger value="graph">Graph</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="graph" className="mt-6 space-y-4">
          <p className="text-xs text-muted-foreground" data-testid="knowledge-hint">{MARKER_EXPLORER}</p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="relative flex-1 min-w-[220px]">
              <span className="sr-only">Find a node</span>
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input className="pl-9" placeholder="Find a node by id, title or tag" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Find a node" />
              {hits.length > 0 && (
                <ul className="absolute z-20 mt-1 w-full rounded-md border border-border bg-card shadow-md text-sm" data-testid="knowledge-search-hits">
                  {hits.map((h) => (
                    <li key={h.id}>
                      <button type="button" className="flex w-full gap-2 px-3 py-1.5 text-left hover:bg-muted"
                        onClick={() => { select(h.id); setSearch(""); }}>
                        <span className="font-mono text-xs text-muted-foreground">{h.label ?? h.id}</span>
                        <span className="truncate">{h.title}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </label>
            <div className="flex rounded-md border border-border" role="group" aria-label="View">
              {(["focus", "all"] as const).map((m) => (
                <Button key={m} size="sm" variant={mode === m ? "accent" : "ghost"} aria-pressed={mode === m} onClick={() => { setMode(m); setLayoutChoice(null); }}>{m === "focus" ? "Focus" : "All"}</Button>
              ))}
            </div>
            {mode === "focus" ? (
              <div className="flex rounded-md border border-border" role="group" aria-label="Distance">
                {([1, 2] as const).map((h) => (
                  <Button key={h} size="sm" variant={hops === h ? "accent" : "ghost"} aria-pressed={hops === h} onClick={() => setHops(h)}>{h} hop{h > 1 ? "s" : ""}</Button>
                ))}
              </div>
            ) : (
              <>
                <label className="text-xs text-muted-foreground flex flex-col gap-1">Type
                  <select className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground" value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
                    <option value="">all</option>{types.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
                <label className="text-xs text-muted-foreground flex flex-col gap-1">Status
                  <select className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
                    <option value="">all</option>{statuses.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </label>
              </>
            )}
            <span className="text-xs text-muted-foreground" data-testid="knowledge-summary">{shown.entities.length} of {entities.length} nodes shown</span>
          </div>

          {graph.isLoading ? (
            <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>
          ) : graph.isError ? (
            <Card className="border-destructive/30 bg-destructive/10"><CardContent className="py-6 flex items-center gap-2 text-destructive text-sm"><AlertCircle size={18} /> The graph could not be loaded.</CardContent></Card>
          ) : entities.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg" data-testid="knowledge-empty">
              No entities at <span className="font-mono">{config.data?.repo_path ?? "the configured path"}</span>/kb/graph. Point the repository path at a Corvin-Knowledge checkout under Settings, run `kb index` there, or pull it.
            </div>
          ) : shown.entities.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">No node matches.</div>
          ) : (
            <>
            {unknownParam && (
              <p className="text-xs text-muted-foreground" data-testid="knowledge-unknown-node">
                There is no node &quot;{nodeParam}&quot; in this graph — showing the newest decision instead.
              </p>
            )}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <GraphCanvas nodes={shown.entities} edges={shown.relations} selectedKey={selectedKey} statusColours={colours} onSelect={select} layoutMode={layoutMode} onLayoutModeChange={setLayoutChoice} />
              <DocumentPane nodeKey={selectedKey && known.has(selectedKey) ? selectedKey : null} index={linkIndex} basePath={BASE_PATH} onOpen={select} onBack={canGoBack ? () => navigate(-1) : null} />
            </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="settings" className="mt-6">
          {config.isError ? (
            <Card className="border-destructive/30 bg-destructive/10"><CardContent className="py-6 flex items-center gap-2 text-destructive text-sm"><AlertCircle size={18} /> The settings could not be loaded.</CardContent></Card>
          ) : cfg ? (
            <Card><CardContent className="p-4 space-y-4 max-w-2xl">
              <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(cfg); }} data-testid="knowledge-settings-form">
                <div className="space-y-1">
                  <label className="text-sm font-medium" htmlFor="ck-repo">Repository path</label>
                  <Input id="ck-repo" className="font-mono" value={cfg.repo_path} onChange={(e) => setDraft({ ...cfg, repo_path: e.target.value })} />
                  <p className="text-xs text-muted-foreground">Local checkout the graph is read from (kb/graph/entities.jsonl, kb/graph/relations.jsonl — ADR-2206).</p>
                </div>
                <div className="space-y-1">
                  <label className="text-sm font-medium" htmlFor="ck-remote">Remote URL</label>
                  <Input id="ck-remote" className="font-mono" value={cfg.remote_url} onChange={(e) => setDraft({ ...cfg, remote_url: e.target.value })} />
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={cfg.auto_sync_on_query} onChange={(e) => setDraft({ ...cfg, auto_sync_on_query: e.target.checked })} /> Pull the remote before a query
                </label>
                <fieldset className="space-y-1">
                  <legend className="text-sm font-medium">Consistency level</legend>
                  {(["strict", "warn", "ignore"] as const).map((l) => (
                    <label key={l} className="flex items-center gap-2 text-sm">
                      <input type="radio" name="ck-consistency" value={l} checked={cfg.consistency_level === l} onChange={() => setDraft({ ...cfg, consistency_level: l })} />
                      <span className="capitalize">{l}</span>
                      <span className="text-xs text-muted-foreground">{l === "strict" ? "abort a sync on any validation error" : l === "warn" ? "log validation errors, continue" : "skip validation (development only)"}</span>
                    </label>
                  ))}
                </fieldset>
                <div className="flex gap-2">
                  <Button type="submit" size="sm" variant="accent" disabled={!draft || save.isPending || !csrf}>Save settings</Button>
                  {draft && <Button type="button" size="sm" variant="ghost" onClick={() => setDraft(null)}>Discard</Button>}
                </div>
                <p className="text-xs text-muted-foreground">The same values are the plugin's settings on the Marketplace → Installed tab; saving here updates both.</p>
              </form>
            </CardContent></Card>
          ) : (
            <div className="py-10 flex justify-center"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default CorvinKnowledgePage;
