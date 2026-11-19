/**
 * DaddyReady — the console panel of the private plugin `daddy_ready` (installed from the operator's own plugin
 * repository). Three views: Episodes (rendered videos with an embedded player and the day's brief), Knowledge (a
 * read-only view of what the plugin has recorded) and Settings (every option the plugin offers).
 *
 * Mounts only while the plugin is installed AND enabled (capability manifest -> manifestPanelRoutes resolves
 * `DaddyReadyPage` by name, like CorvinTV). Every call goes through `api()` (session cookie, CSRF on writes). The page
 * shows data about a child to the signed-in operator only; it logs nothing and sends nothing anywhere.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ChevronLeft, ChevronRight, Loader2, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/lib/api/client";
import {
  getBrief, getEpisodes, getGraph, getKb, getKbEntry, getOverview, getSettings, renderEpisode, saveSettings, videoUrl,
  type EpisodeRow, type KbItem,
} from "@/lib/api/daddy-ready";
import { useAuth } from "@/lib/auth";
import { SettingsForm } from "./SettingsForm";
import { GraphView } from "./GraphView";
import { briefBlocks, formatAge, formatBytes, formatDate, formatDuration, isDirty, pageLabel, plural, stepTone, type BriefBlock, type Json } from "./model";

const KEY = ["daddy-ready"] as const;
const TYPES = ["observation", "moment", "milestone", "feedback", "episode", "method"] as const;

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message || `The request failed (${e.status}).`;
  return e instanceof Error ? e.message : "The request failed.";
}

function ErrorLine({ error }: { error: unknown }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> <span>{errText(error)}</span>
    </div>
  );
}

function BriefView({ markdown }: { markdown: string }) {
  const blocks = briefBlocks(markdown);
  const text = (b: BriefBlock) => b.parts.map((p, i) => (p.bold ? <strong key={i}>{p.text}</strong> : <span key={i}>{p.text}</span>));
  const out: React.ReactNode[] = [];
  let items: BriefBlock[] = [];
  const flush = () => { if (items.length) { out.push(<ul key={`ul${out.length}`} className="list-disc space-y-1 pl-5">{items.map((b, i) => <li key={i}>{text(b)}</li>)}</ul>); items = []; } };
  blocks.forEach((b, i) => {
    if (b.kind === "li") { items.push(b); return; }
    flush();
    if (b.kind === "h1") out.push(<h4 key={i} className="text-base font-semibold">{text(b)}</h4>);
    else if (b.kind === "h2") out.push(<h5 key={i} className="pt-2 text-sm font-semibold">{text(b)}</h5>);
    else out.push(<p key={i} className="break-words">{text(b)}</p>);
  });
  flush();
  return <div className="space-y-2 text-sm" data-testid="dr-brief">{out}</div>;
}

function Loading({ what }: { what: string }) {
  return <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading {what}…</div>;
}

/* ------------------------------------------------------------------ episodes */
function EpisodesView() {
  const eps = useQuery({ queryKey: [...KEY, "episodes"], queryFn: ({ signal }) => getEpisodes(signal), retry: false });
  const [day, setDay] = useState<string | null>(null);
  const [videoFailed, setVideoFailed] = useState(false);
  const rows = eps.data?.episodes ?? [];
  const current: EpisodeRow | undefined = rows.find((r) => r.day === day) ?? rows[0];
  useEffect(() => { setVideoFailed(false); }, [current?.day]);
  const brief = useQuery({
    queryKey: [...KEY, "brief", current?.day], enabled: Boolean(current?.has_brief),
    queryFn: ({ signal }) => getBrief(current!.day, signal), retry: false,
  });
  if (eps.isPending) return <Loading what="episodes" />;
  if (eps.isError) return <ErrorLine error={eps.error} />;
  if (rows.length === 0) {
    return (
      <Card><CardContent className="p-6 text-sm text-muted-foreground" data-testid="dr-episodes-empty">
        No episodes yet. A brief is written by the nightly plan step; a video appears after the render step
        (turn on video rendering under Settings).
      </CardContent></Card>
    );
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]" data-testid="dr-episodes">
      <ul className="space-y-1" aria-label="Episodes">
        {rows.map((r) => (
          <li key={r.day}>
            <button type="button" onClick={() => setDay(r.day)} aria-current={r.day === current?.day}
              className={`w-full rounded-md border p-3 text-left text-sm transition-colors hover:bg-muted ${r.day === current?.day ? "border-primary bg-muted" : ""}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium tabular-nums">{r.day}</span>
                <span className="flex gap-1">
                  {r.has_video ? <Badge variant="secondary">video</Badge> : null}
                  {Object.entries(r.steps).filter(([, v]) => stepTone(v) === "bad").map(([k]) => <Badge key={k} variant="danger">{k} failed</Badge>)}
                </span>
              </div>
              <div className="truncate text-xs text-muted-foreground">{r.method ?? "No method"}</div>
            </button>
          </li>
        ))}
      </ul>
      {current ? (
        <div className="min-w-0 space-y-4">
          <Card><CardContent className="space-y-3 p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-base font-semibold">Episode {current.day}</h3>
              {current.has_video ? <span className="text-xs text-muted-foreground tabular-nums">{formatDuration(current.duration_s)} · {formatBytes(current.video_bytes)}</span> : null}
            </div>
            {current.has_video ? (
              videoFailed ? (
                <p role="alert" className="text-sm text-destructive">The video could not be played in this browser.</p>
              ) : (
                <video key={current.day} data-testid="dr-video" controls controlsList="nofullscreen nodownload" playsInline
                  preload="metadata" autoPlay={Boolean(eps.data?.autoplay)}
                  className="max-h-[28rem] w-full rounded-md bg-black [&::-webkit-media-controls-fullscreen-button]:hidden"
                  src={videoUrl(current.day)} onError={() => setVideoFailed(true)} />
              )
            ) : (
              <div className="space-y-2" data-testid="dr-no-video">
                <p className="text-sm text-muted-foreground">There is no rendered video for this day yet.</p>
                {current.has_brief ? <RenderButton day={current.day} /> : null}
              </div>
            )}
          </CardContent></Card>
          <Card><CardContent className="space-y-2 p-4">
            <h3 className="text-base font-semibold">Brief</h3>
            {!current.has_brief ? <p className="text-sm text-muted-foreground">No brief for this day.</p>
              : brief.isPending ? <Loading what="brief" />
              : brief.isError ? <ErrorLine error={brief.error} />
              : <BriefView markdown={brief.data.markdown} />}
          </CardContent></Card>
        </div>
      ) : null}
    </div>
  );
}

function RenderButton({ day }: { day: string }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const [started, setStarted] = useState(false);
  const go = useMutation({
    mutationFn: () => renderEpisode(day, csrf),
    onSuccess: () => {
      setStarted(true);
      // the render runs in the background: refresh the episode list until the step status changes
      const t = setInterval(() => void qc.invalidateQueries({ queryKey: KEY }), 15_000);
      setTimeout(() => clearInterval(t), 30 * 60_000);
    },
  });
  return (
    <div className="space-y-2">
      <Button type="button" variant="accent" disabled={go.isPending || started || !csrf} onClick={() => go.mutate()}>
        {go.isPending ? "Starting…" : started ? "Rendering in the background…" : "Create video"}
      </Button>
      <p className="text-xs text-muted-foreground">Rendering takes several minutes and sends this day's brief to the video service (CorvinTV). It is refused unless video rendering is on and every source is approved for it under Settings.</p>
      {go.isError ? <ErrorLine error={go.error} /> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ knowledge */
function KnowledgeView() {
  const [type, setType] = useState("");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const page = useQuery({
    queryKey: [...KEY, "kb", type, q, offset],
    queryFn: ({ signal }) => getKb({ type: type || undefined, q: q || undefined, offset }, signal), retry: false,
  });
  const entry = useQuery({
    queryKey: [...KEY, "entry", selected], enabled: Boolean(selected),
    queryFn: ({ signal }) => getKbEntry(selected!, signal), retry: false,
  });
  const submit = (e: React.FormEvent) => { e.preventDefault(); setOffset(0); setQ(text.trim()); };
  const d = page.data;
  return (
    <div className="space-y-4" data-testid="dr-kb">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2" role="search">
        <div className="w-44">
          <label htmlFor="dr-kb-type" className="mb-1 block text-xs text-muted-foreground">Type</label>
          <Select id="dr-kb-type" value={type} onChange={(e) => { setType(e.target.value); setOffset(0); setSelected(null); }}>
            <option value="">All types</option>
            {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
        </div>
        <div className="min-w-[14rem] flex-1">
          <label htmlFor="dr-kb-q" className="mb-1 block text-xs text-muted-foreground">Search the text</label>
          <Input id="dr-kb-q" value={text} onChange={(e) => setText(e.target.value)} placeholder="Words in a note, a method, a title…" />
        </div>
        <Button type="submit" variant="outline"><Search className="mr-1 h-4 w-4" aria-hidden /> Search</Button>
      </form>
      <p className="text-xs text-muted-foreground">Read-only: this is what the plugin has recorded. Entries are added by notes, sources and the planner.</p>
      {page.isPending ? <Loading what="entries" /> : page.isError ? <ErrorLine error={page.error} /> : d ? (
        <div className="grid gap-4 lg:grid-cols-[1fr_24rem]">
          <Card><CardContent className="p-0">
            {d.items.length === 0 ? <p className="p-6 text-sm text-muted-foreground" data-testid="dr-kb-empty">Nothing recorded{q || type ? " for this filter" : " yet"}.</p> : (
              <ul className="divide-y" aria-label="Knowledge entries">
                {d.items.map((it: KbItem) => (
                  <li key={it.id}>
                    <button type="button" onClick={() => setSelected(it.id)} aria-current={it.id === selected}
                      className={`w-full p-3 text-left text-sm hover:bg-muted ${it.id === selected ? "bg-muted" : ""}`}>
                      <div className="mb-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <Badge variant="outline">{it.type}</Badge>
                        <span className="tabular-nums">{formatDate(it.captured_at)}</span>
                        <span>age {formatAge(it.age_days)}</span>
                        {it.source_key ? <span>· {it.source_key}</span> : null}
                      </div>
                      <div className="break-words">{it.snippet || <span className="text-muted-foreground">(no text)</span>}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex items-center justify-between border-t p-3 text-xs text-muted-foreground">
              <span data-testid="dr-kb-range">{pageLabel(d.offset, d.limit, d.total)}</span>
              <span className="flex gap-1">
                <Button type="button" size="sm" variant="outline" aria-label="Previous page" disabled={d.offset === 0} onClick={() => setOffset(Math.max(0, d.offset - d.limit))}><ChevronLeft className="h-4 w-4" /></Button>
                <Button type="button" size="sm" variant="outline" aria-label="Next page" disabled={d.offset + d.limit >= d.total} onClick={() => setOffset(d.offset + d.limit)}><ChevronRight className="h-4 w-4" /></Button>
              </span>
            </div>
          </CardContent></Card>
          <Card><CardContent className="space-y-3 p-4" data-testid="dr-kb-detail">
            {!selected ? <p className="text-sm text-muted-foreground">Select an entry to read it in full.</p>
              : entry.isPending ? <Loading what="entry" />
              : entry.isError ? <ErrorLine error={entry.error} />
              : (
                <>
                  <div className="flex items-center gap-2"><Badge variant="outline">{entry.data.type}</Badge><code className="text-xs">{entry.data.id}</code></div>
                  <p className="whitespace-pre-wrap break-words text-sm">{entry.data.body || "(no text)"}</p>
                  <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-xs">
                    {Object.entries(entry.data.meta).map(([k, v]) => (
                      <div key={k} className="contents"><dt className="text-muted-foreground">{k}</dt><dd className="break-all">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>
                    ))}
                  </dl>
                </>
              )}
          </CardContent></Card>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ settings */
function SettingsView() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const doc = useQuery({ queryKey: [...KEY, "settings"], queryFn: ({ signal }) => getSettings(signal), retry: false });
  const [draft, setDraft] = useState<Json | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => { if (doc.data && draft === null) setDraft(doc.data.config as Json); }, [doc.data, draft]);
  const save = useMutation({
    mutationFn: (c: Json) => saveSettings(c, csrf),
    onSuccess: (r) => {
      setSaved(`Saved. The previous file is kept as ${r.backup}.`);
      setDraft(null);
      void qc.invalidateQueries({ queryKey: KEY });
    },
  });
  const dirty = useMemo(() => doc.data && draft ? isDirty(doc.data.config as Json, draft) : false, [doc.data, draft]);
  if (doc.isPending || draft === null && !doc.isError) return <Loading what="settings" />;
  if (doc.isError) return <ErrorLine error={doc.error} />;
  return (
    <div className="space-y-4">
      <SettingsForm doc={doc.data} draft={draft!} onChange={(n) => { setSaved(null); save.reset(); setDraft(n); }} />
      <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-3 border-t bg-background/95 p-3 backdrop-blur" data-testid="dr-settings-bar">
        <Button type="button" variant="accent" disabled={!dirty || save.isPending || !csrf} onClick={() => save.mutate(draft!)}>
          {save.isPending ? "Saving…" : "Save settings"}
        </Button>
        <Button type="button" variant="outline" disabled={!dirty || save.isPending} onClick={() => { setDraft(doc.data!.config as Json); save.reset(); }}>Discard changes</Button>
        {dirty ? <span className="text-xs text-muted-foreground">Unsaved changes</span> : null}
        {saved ? <span role="status" className="text-sm">{saved}</span> : null}
        {save.isError ? <span role="alert" className="text-sm text-destructive" data-testid="dr-save-error">{errText(save.error)}</span> : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ graph */
function GraphTab() {
  const g = useQuery({ queryKey: [...KEY, "graph"], queryFn: ({ signal }) => getGraph(signal), retry: false });
  if (g.isPending) return <Loading what="graph" />;
  if (g.isError) return <ErrorLine error={g.error} />;
  return <GraphView doc={g.data} />;
}

/* ------------------------------------------------------------------ page */
export function DaddyReadyPage() {
  const ov = useQuery({ queryKey: [...KEY, "overview"], queryFn: ({ signal }) => getOverview(signal), retry: false, refetchInterval: 30_000 });
  const o = ov.data;
  return (
    <div className="space-y-6" data-testid="dr-page">
      <div>
        <h1 className="text-3xl font-bold">DaddyReady</h1>
        <p className="text-muted-foreground">
          {o ? `${o.child_name || "Your child"}${o.age_months !== null ? ` · ${o.age_months} months` : ""}` : "Follow your child's development, one morning episode at a time."}
        </p>
      </div>
      {ov.isError ? <ErrorLine error={ov.error} /> : o ? (
        <div className="flex flex-wrap gap-2 text-xs" data-testid="dr-summary">
          <Badge variant={o.paused ? "danger" : "secondary"}>{o.paused ? "Paused" : "Active"}</Badge>
          <Badge variant="outline">{plural(o.counts.moment ?? 0, "moment")}</Badge>
          <Badge variant="outline">{plural(o.counts.observation ?? 0, "note")}</Badge>
          <Badge variant="outline">{plural(o.counts.milestone ?? 0, "milestone")}</Badge>
          <Badge variant="outline">{plural(o.episodes, "episode")}</Badge>
          <Badge variant="outline">target {o.length_minutes} min</Badge>
          <Badge variant={o.video_enabled ? "secondary" : "outline"}>video {o.video_enabled ? "on" : "off"}</Badge>
          <Badge variant={o.delivery_enabled ? "secondary" : "outline"}>delivery {o.delivery_enabled ? "on" : "off"}</Badge>
          {o.rejected > 0 ? <Badge variant="danger">{o.rejected} rejected</Badge> : null}
        </div>
      ) : null}
      <Tabs defaultValue="episodes">
        <TabsList>
          <TabsTrigger value="episodes">Episodes</TabsTrigger>
          <TabsTrigger value="knowledge">Knowledge</TabsTrigger>
          <TabsTrigger value="graph">Graph</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="episodes"><EpisodesView /></TabsContent>
        <TabsContent value="knowledge"><KnowledgeView /></TabsContent>
        <TabsContent value="graph"><GraphTab /></TabsContent>
        <TabsContent value="settings"><SettingsView /></TabsContent>
      </Tabs>
    </div>
  );
}

export default DaddyReadyPage;
