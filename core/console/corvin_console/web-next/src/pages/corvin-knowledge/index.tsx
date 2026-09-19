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
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Network } from "vis-network";
import "vis-network/styles/vis-network.min.css";
import { AlertCircle, Loader2, Network as NetworkIcon, RefreshCw, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";

export interface KnowledgeEntity { id: string; type: string; title: string; status: string; tags: string[] }
export interface KnowledgeRelation { from_id: string; to_id: string; relation: string }
export interface KnowledgeGraph { entities: KnowledgeEntity[]; relations: KnowledgeRelation[] }
export interface KnowledgeConfig {
  repo_path: string; remote_url: string; auto_sync_on_query: boolean; consistency_level: "strict" | "warn" | "ignore";
}
export interface SyncResult { status: string; message: string; timestamp: string; conflicts: string[]; errors: string[] }

const BASE_PATH = "/plugins/corvin-knowledge";
const KEY_GRAPH = ["corvin-knowledge", "graph"] as const;
const KEY_CONFIG = ["corvin-knowledge", "config"] as const;

/** Rendered caption — also the deploy marker (a string literal, see ADR-0885). */
export const MARKER_KNOWLEDGE = "Decisions, concepts and ideas of the configured knowledge repository, as the graph its relations describe.";

/** Status → colour. Four closed values, one hue each; the graph library needs
 *  concrete colours, so these are the console's viz tokens resolved at render. */
const STATUS_ORDER = ["proposed", "accepted", "verified", "superseded"] as const;
function statusColours(): Record<string, string> {
  const cs = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  return {
    proposed: v("--viz-tier-2", "#d29a3a"),
    accepted: v("--viz-tier-1", "#3a9a6a"),
    verified: v("--viz-role-worker", "#3a7fbf"),
    superseded: v("--viz-baseline", "#8a8a8a"),
  };
}

function GraphCanvas({ graph, onSelect }: { graph: KnowledgeGraph; onSelect: (e: KnowledgeEntity | null) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || graph.entities.length === 0) return;
    const colours = statusColours();
    const fg = getComputedStyle(document.documentElement).getPropertyValue("--foreground").trim();
    const font = fg ? `hsl(${fg})` : undefined;
    // Defensive: the loader dedupes, but a duplicate node id makes the graph
    // library throw and the error boundary swallow the whole panel.
    const uniq = Array.from(new Map(graph.entities.map((e) => [e.id, e])).values());
    const nodes = uniq.map((e) => ({
      id: e.id,
      label: e.title.length > 32 ? `${e.title.slice(0, 31)}…` : e.title,
      color: { background: colours[e.status] ?? colours.superseded, border: colours[e.status] ?? colours.superseded },
      shape: e.type === "decision" ? "box" : "dot",
      size: e.type === "decision" ? 18 : 10,
      font: { size: e.type === "decision" ? 13 : 11, color: font },
      title: `${e.type} · ${e.status}\n${e.title}`,
    }));
    const known = new Set(uniq.map((e) => e.id));
    const edges = graph.relations
      .filter((r) => known.has(r.from_id) && known.has(r.to_id))
      .map((r) => ({ from: r.from_id, to: r.to_id, label: r.relation, arrows: "to", font: { size: 9, align: "middle" as const } }));
    const net = new Network(el, { nodes, edges }, {
      physics: { solver: "forceAtlas2Based", forceAtlas2Based: { gravitationalConstant: -30, springLength: 160 }, stabilization: { iterations: 120 } },
      interaction: { hover: true, navigationButtons: false, keyboard: false },
      layout: { randomSeed: 42 },
    });
    net.on("click", (params: { nodes: string[] }) => {
      onSelect(params.nodes.length ? uniq.find((e) => e.id === params.nodes[0]) ?? null : null);
    });
    return () => net.destroy();
  }, [graph, onSelect]);
  return <div ref={ref} className="h-[560px] w-full rounded-lg border border-border bg-card" data-testid="knowledge-graph-canvas" />;
}

export function CorvinKnowledgePage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const graph = useQuery({ queryKey: [...KEY_GRAPH], queryFn: ({ signal }) => api<KnowledgeGraph>(`${BASE_PATH}/graph`, { signal }), retry: false });
  const config = useQuery({ queryKey: [...KEY_CONFIG], queryFn: ({ signal }) => api<KnowledgeConfig>(`${BASE_PATH}/config`, { signal }), retry: false });

  const [selected, setSelected] = useState<KnowledgeEntity | null>(null);
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [draft, setDraft] = useState<KnowledgeConfig | null>(null);

  const entities = useMemo(() => graph.data?.entities ?? [], [graph.data]);
  const types = useMemo(() => Array.from(new Set(entities.map((e) => e.type))).sort(), [entities]);
  const filtered = useMemo(() => {
    const s = search.trim().toLowerCase();
    const keep = entities.filter((e) =>
      (!type || e.type === type) && (!status || e.status === status) &&
      (!s || e.title.toLowerCase().includes(s) || e.id.toLowerCase().includes(s) || e.tags.some((t) => t.toLowerCase().includes(s))));
    const ids = new Set(keep.map((e) => e.id));
    return { entities: keep, relations: (graph.data?.relations ?? []).filter((r) => ids.has(r.from_id) && ids.has(r.to_id)) };
  }, [entities, graph.data, search, type, status]);

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
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex-1 min-w-[220px]">
              <span className="sr-only">Search entities</span>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input className="pl-9" placeholder="Search title, id or tag" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search entities" />
              </div>
            </label>
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
            <span className="text-xs text-muted-foreground" data-testid="knowledge-summary">{filtered.entities.length} of {entities.length} entities shown</span>
          </div>

          {graph.isLoading ? (
            <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>
          ) : graph.isError ? (
            <Card className="border-destructive/30 bg-destructive/10"><CardContent className="py-6 flex items-center gap-2 text-destructive text-sm"><AlertCircle size={18} /> The graph could not be loaded.</CardContent></Card>
          ) : entities.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg" data-testid="knowledge-empty">
              No entities at <span className="font-mono">{config.data?.repo_path ?? "the configured path"}</span>/graph. Point the repository path at a Corvin-Knowledge checkout under Settings, or pull it.
            </div>
          ) : filtered.entities.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">No entity matches.</div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-4">
              <GraphCanvas graph={filtered} onSelect={setSelected} />
              <Card>
                <CardContent className="p-4 space-y-3 text-sm">
                  {selected ? (
                    <>
                      <div className="flex flex-wrap gap-1"><Badge variant="outline">{selected.type}</Badge><Badge variant="secondary">{selected.status}</Badge></div>
                      <div className="font-semibold">{selected.title}</div>
                      <div className="text-xs text-muted-foreground font-mono break-all">{selected.id}</div>
                      {selected.tags.length > 0 && <div className="flex flex-wrap gap-1">{selected.tags.map((t) => <Badge key={t} variant="outline">{t}</Badge>)}</div>}
                      <div className="text-xs text-muted-foreground">
                        {(graph.data?.relations ?? []).filter((r) => r.from_id === selected.id || r.to_id === selected.id).length} relations
                      </div>
                    </>
                  ) : (
                    <p className="text-muted-foreground text-xs">Click an entity to see its details. Boxes are decisions; dots are concepts, ideas and implementations. Colour is the status.</p>
                  )}
                </CardContent>
              </Card>
            </div>
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
                  <p className="text-xs text-muted-foreground">Local checkout the graph is read from (graph/entities.jsonl, graph/relations.jsonl).</p>
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
