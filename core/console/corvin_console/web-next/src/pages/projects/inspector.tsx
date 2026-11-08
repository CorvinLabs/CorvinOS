/**
 * Project inspector - one project: its state and checks, its features, and the settings a user may change
 * (repair, open, remove from the list). Every change goes through the server, which judges it. A file is
 * never deleted from here.
 *
 * Tabs built here: Overview, Features, Settings. Not built yet: Structure, Docs, Knowledge, Tasks, Videos,
 * Compute (T-0359 stays open until they exist).
 */
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { createVideo, getHealth, getProject, openProject, removeProjectFromList, repairProject, setFeatures, type FeatureRow, type ProjectDetail } from "@/lib/api/projects";
import { cn } from "@/lib/utils";
import { explain } from "./wizard";
import { TABS, conflictOf, healthHeadline, parseTab, rowState, tabGate, videoNameProblem, type Conflict, type Tab } from "./inspector-logic";
import { ComputeTab, DirectoryTab, DocsTab, FeatureOff, KnowledgeTab, StructureTab, TasksTab, VideosTab } from "./inspector-tabs";
import { stateOf } from "./encodings";

const KEY = (id: string) => ["projects", "detail", id] as const;
const HEALTH = (id: string) => ["projects", "health", id] as const;

export default function ProjectInspectorPage() {
  const { projectId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = parseTab(params.get("tab"));
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  const detail = useQuery({ queryKey: KEY(projectId), queryFn: ({ signal }) => getProject(projectId, signal), enabled: !!projectId });
  const d: ProjectDetail | undefined = detail.data;
  const ready = d?.state === "ok";

  if (detail.isLoading) return <div className="p-6"><Skeleton className="h-32 w-full" data-testid="inspector-loading" /></div>;
  if (detail.error || !d) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-6" data-testid="inspector-missing">
        <BackLink />
        <h1 className="text-xl font-semibold">This project is not in your list</h1>
        <p className="text-sm text-muted-foreground">It may have been removed from the list. Nothing on disk was touched.</p>
      </div>
    );
  }
  const st = stateOf(d);

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-4 md:p-6" data-testid="project-inspector" data-project-id={d.id}>
      <BackLink />
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold" data-testid="inspector-title">{d.title ?? d.id}</h1>
          <p className="truncate font-mono text-xs text-muted-foreground" title={d.path}>{d.path}</p>
        </div>
        <span data-testid="inspector-state" className={cn("rounded-md border px-2 py-1 text-xs",
          st.tone === "ok" ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300" : "border-red-500/40 text-red-700 dark:text-red-300")}>
          {st.label}
        </span>
      </header>

      {!ready && (
        <Card><CardContent className="space-y-3 p-4">
          <p className="text-sm" data-testid="inspector-not-ready">{st.detail || "This project cannot be opened right now."}</p>
          <RemoveSection projectId={d.id} csrf={csrf} onDone={() => { void qc.invalidateQueries({ queryKey: ["projects", "list"] }); }} />
        </CardContent></Card>
      )}

      {ready && (
        <>
          <nav role="tablist" aria-label="Project sections" className="flex flex-wrap gap-1 border-b">
            {TABS.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} data-testid={`tab-${t.id}`}
                onClick={() => setTab(t.id)}
                className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t.id ? "border-primary font-medium" : "border-transparent text-muted-foreground")}>
                {t.label}
              </button>
            ))}
          </nav>
          {(() => {
            const gate = tabGate(tab, d.active ?? []);
            if (!gate.open && gate.feature) return <FeatureOff d={d} feature={gate.feature} />;
            switch (tab) {
              case "overview": return <OverviewTab projectId={d.id} videosOn={(d.active ?? []).includes("videos")} csrf={csrf} onChanged={() => void qc.invalidateQueries({ queryKey: KEY(d.id) })} />;
              case "structure": return <StructureTab id={d.id} />;
              case "docs": return <DocsTab id={d.id} />;
              case "knowledge": return <KnowledgeTab id={d.id} />;
              case "tasks": return <TasksTab id={d.id} />;
              case "videos": return <VideosTab id={d.id} />;
              case "directory": return <DirectoryTab d={d} />;
              case "compute": return <ComputeTab id={d.id} />;
              case "features": return <FeaturesTab d={d} csrf={csrf} onChanged={() => void qc.invalidateQueries({ queryKey: KEY(d.id) })} />;
              case "settings": return <SettingsTab d={d} csrf={csrf} />;
            }
          })()}
        </>
      )}
    </div>
  );
}

function NewVideoCard({ projectId, videosOn, csrf, onCreated }: { projectId: string; videosOn: boolean; csrf: string; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const problem = name.length > 0 ? videoNameProblem(name) : null;
  const mut = useMutation({
    mutationFn: () => createVideo(projectId, name.trim(), csrf),
    onSuccess: (r) => {
      setError(null);
      setName("");
      setResult(`Created ${r.video.name} at ${r.video.path}.${r.video.kb ? ` Epic ${r.video.epic} with ${r.video.tasks.length} tasks in the knowledge base.` : ""}`);
      if (r.note) setResult((x) => `${x} ${r.note}`);
      onCreated();
    },
    onError: (e) => { setResult(null); setError(explain(e)); },
  });
  return (
    <div className="space-y-2 rounded-lg border p-4" data-testid="new-video-card">
      <h2 className="font-medium">New video</h2>
      {!videosOn ? (
        <p className="text-sm text-muted-foreground" data-testid="new-video-off">The videos feature is off. Switch it on in Features to create videos here.</p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">Creates the video's folder with source/, build/ and a sources.md of the eight sections. With the knowledge base on, it also makes the epic VIDEO-&lt;name&gt; with the German and English tasks.</p>
          <div className="flex flex-wrap items-start gap-2">
            <label className="min-w-0 flex-1 text-sm">
              <span className="sr-only">Video name</span>
              <input className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm" data-testid="new-video-name"
                value={name} onChange={(e) => { setName(e.target.value); setError(null); }} placeholder="e.g. plumbline-intro" />
            </label>
            <Button size="sm" onClick={() => mut.mutate()} disabled={mut.isPending || name.trim().length === 0 || problem !== null} data-testid="new-video-create">
              {mut.isPending ? "Creating…" : "Create video"}
            </Button>
          </div>
          {problem && <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="new-video-problem">{problem}</p>}
          {result && <p role="status" className="text-sm" data-testid="new-video-result">{result}</p>}
          {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="new-video-error">{error}</p>}
        </>
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link to="/app/projects" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline">
      <ArrowLeft className="h-4 w-4" aria-hidden /> Projects
    </Link>
  );
}

function OverviewTab({ projectId, videosOn, csrf, onChanged }: { projectId: string; videosOn: boolean; csrf: string; onChanged: () => void }) {
  const health = useQuery({ queryKey: HEALTH(projectId), queryFn: ({ signal }) => getHealth(projectId, signal) });
  const head = healthHeadline(health.data?.checks ?? []);
  return (
    <section className="space-y-3" aria-label="Checks" data-testid="overview-tab">
      <NewVideoCard projectId={projectId} videosOn={videosOn} csrf={csrf} onCreated={onChanged} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className={cn("text-sm font-medium", head.ok ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300")} data-testid="health-headline">
          {health.isLoading ? "Checking…" : head.text}
        </p>
        <Button variant="outline" size="sm" onClick={() => void health.refetch()} disabled={health.isFetching}>
          <RefreshCw className={cn("mr-2 h-4 w-4", health.isFetching && "animate-spin")} aria-hidden /> Check again
        </Button>
      </div>
      {health.error && <p className="text-sm text-red-700 dark:text-red-300" data-testid="health-error">The checks could not run.</p>}
      <ul className="space-y-2" data-testid="health-checks">
        {(health.data?.checks ?? []).map((c) => (
          <li key={c.id} data-testid="health-check" data-check={c.id} data-ok={String(c.ok)} className="flex items-start gap-2 rounded-md border p-3 text-sm">
            {c.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-label="passed" />
              : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" aria-label="failed" />}
            <span><span className="block font-medium">{CHECK_LABEL[c.id] ?? c.id}</span><span className="text-muted-foreground">{c.detail}</span></span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const CHECK_LABEL: Record<string, string> = {
  path_exists: "Folder exists", manifest: "project.yaml", features_closed: "Features complete",
  generated_files_in_sync: "Generated files up to date", tenant_plugins: "Plugins enabled",
  catalogue: "Template catalogue", kb_check: "Knowledge base check", compute_worker: "Compute worker",
};

function FeaturesTab({ d, csrf, onChanged }: { d: ProjectDetail; csrf: string; onChanged: () => void }) {
  const [conflict, setConflict] = useState<{ conflict: Conflict; feature: string; on: boolean } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const rows: FeatureRow[] = d.catalogue?.features ?? [];
  const mut = useMutation({
    mutationFn: (req: { feature: string; on: boolean; acceptDependencies?: boolean; cascade?: boolean }) =>
      setFeatures(d.id, { set: { [req.feature]: req.on }, accept_dependencies: !!req.acceptDependencies, cascade: !!req.cascade }, csrf),
    onSuccess: () => { setConflict(null); setMessage("Saved. Generated files were updated; nothing you wrote was touched."); onChanged(); },
    onError: (e, req) => {
      const c = conflictOf(e);
      if (c) setConflict({ conflict: c, feature: req.feature, on: req.on });
      else setMessage(explain(e));
    },
  });
  if (!d.catalogue?.available) {
    return <p className="text-sm text-muted-foreground" data-testid="features-unavailable">{d.catalogue?.reason ?? "The template catalogue is not available."}</p>;
  }
  return (
    <section className="space-y-3" aria-label="Features" data-testid="features-tab">
      <p className="text-sm text-muted-foreground">Switching a feature off keeps its files. Switching one on adds what it needs.</p>
      {message && <p role="status" className="text-sm" data-testid="features-message">{message}</p>}
      {conflict && <ConflictBox c={conflict} onAccept={(extra) => mut.mutate({ ...extra })} onCancel={() => setConflict(null)} pending={mut.isPending} />}
      <ul className="space-y-2">
        {rows.map((f) => {
          const rs = rowState(f);
          return (
            <li key={f.id} data-testid="feature-row" data-feature={f.id} data-active={String(f.active)} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="font-medium">{rs.label}</span>{" "}
                  <span data-testid="feature-status" className={cn("text-xs", f.active ? "text-emerald-700 dark:text-emerald-300" : "text-muted-foreground")}>
                    {f.active ? "On" : "Feature off"}
                  </span>
                  {f.description && <p className="text-xs text-muted-foreground">{f.description}</p>}
                </div>
                <Button size="sm" variant={f.active ? "outline" : "default"} data-testid="feature-action"
                  disabled={!rs.canToggle || mut.isPending} onClick={() => mut.mutate({ feature: f.id, on: !f.active })}>
                  {f.active ? "Switch off" : "Switch on"}
                </Button>
              </div>
              {!f.active && rs.preview.length > 0 && (
                <p className="mt-2 text-xs text-muted-foreground" data-testid="feature-preview">Switching on also adds: {rs.preview.join(", ")}.</p>
              )}
              {rs.blockedBy && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300" data-testid="feature-blocked">{rs.blockedBy}.</p>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ConflictBox({ c, onAccept, onCancel, pending }: {
  c: { conflict: Conflict; feature: string; on: boolean }; onAccept: (x: { feature: string; on: boolean; acceptDependencies?: boolean; cascade?: boolean }) => void;
  onCancel: () => void; pending: boolean;
}) {
  const k = c.conflict;
  if (k.kind === "dependants") {
    return (
      <div role="alert" className="space-y-2 rounded-lg border border-amber-500/50 p-3 text-sm" data-testid="conflict-dependants">
        <p>Switching off {c.feature} also switches off: {k.dependants.join(", ")}. Their files stay.</p>
        <div className="flex gap-2">
          <Button size="sm" disabled={pending} onClick={() => onAccept({ feature: c.feature, on: false, cascade: true })} data-testid="conflict-confirm">Switch them all off</Button>
          <Button size="sm" variant="ghost" onClick={onCancel}>Keep it on</Button>
        </div>
      </div>
    );
  }
  if (k.kind === "requires") {
    return (
      <div role="alert" className="space-y-2 rounded-lg border border-amber-500/50 p-3 text-sm" data-testid="conflict-requires">
        <p>Switching on {c.feature} also needs: {k.requires.join(", ")}.</p>
        <div className="flex gap-2">
          <Button size="sm" disabled={pending} onClick={() => onAccept({ feature: c.feature, on: true, acceptDependencies: true })} data-testid="conflict-confirm">Switch them on</Button>
          <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
        </div>
      </div>
    );
  }
  return (
    <div role="alert" className="space-y-2 rounded-lg border border-red-500/50 p-3 text-sm" data-testid="conflict-other">
      <p>{k.kind === "plugin" || k.kind === "other" ? k.message : "The change was refused."}</p>
      <Button size="sm" variant="ghost" onClick={onCancel}>Dismiss</Button>
    </div>
  );
}

function SettingsTab({ d, csrf }: { d: ProjectDetail; csrf: string }) {
  const [preview, setPreview] = useState<{ pending: { path: string; status: string; diff: string }[] } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const qc = useQueryClient();
  const previewMut = useMutation({
    mutationFn: () => repairProject(d.id, true, csrf),
    onSuccess: (r) => { setPreview(r); setNote(null); },
    onError: (e) => setNote(explain(e)),
  });
  const applyMut = useMutation({
    mutationFn: () => repairProject(d.id, false, csrf),
    onSuccess: () => { setPreview(null); setNote("Repaired. Only generated and machine-local files were rewritten."); void qc.invalidateQueries({ queryKey: KEY(d.id) }); void qc.invalidateQueries({ queryKey: HEALTH(d.id) }); },
    onError: (e) => setNote(explain(e)),
  });
  const openMut = useMutation({
    mutationFn: () => openProject(d.id, csrf),
    onSuccess: (r) => setOpened(r.command),
    onError: (e) => setNote(explain(e)),
  });
  return (
    <section className="space-y-6" aria-label="Settings" data-testid="settings-tab">
      <div className="space-y-2">
        <h2 className="font-medium">Repair</h2>
        <p className="text-sm text-muted-foreground">Restores the generated files (CLAUDE.md sections, machine-local MCP settings) to what the active features say. Your own text is kept.</p>
        <Button size="sm" variant="outline" onClick={() => previewMut.mutate()} disabled={previewMut.isPending} data-testid="repair-preview">Preview changes</Button>
        {preview && (
          <div className="space-y-2" data-testid="repair-preview-result">
            {preview.pending.length === 0 ? <p className="text-sm">Nothing to repair.</p> : (
              <>
                <ul className="space-y-2 text-sm">
                  {preview.pending.map((p) => (
                    <li key={p.path} data-testid="repair-pending" className="space-y-1">
                      <div className="font-mono text-xs">{p.status}: {p.path}</div>
                      {p.diff && <pre className="max-h-64 overflow-auto rounded bg-muted p-2 text-xs" data-testid="repair-diff">{p.diff}</pre>}
                    </li>
                  ))}
                </ul>
                <Button size="sm" onClick={() => applyMut.mutate()} disabled={applyMut.isPending} data-testid="repair-apply">Apply repair</Button>
              </>
            )}
          </div>
        )}
      </div>
      <div className="space-y-2">
        <h2 className="font-medium">Open</h2>
        <p className="text-sm text-muted-foreground">Shows the command to open this folder in your own editor. Attaching it to a chat comes later.</p>
        <Button size="sm" variant="outline" onClick={() => openMut.mutate()} disabled={openMut.isPending} data-testid="open-project">Show command</Button>
        {opened && <code className="block break-all rounded bg-muted p-2 text-xs" data-testid="open-command">{opened}</code>}
      </div>
      <RemoveSection projectId={d.id} csrf={csrf} onDone={() => void qc.invalidateQueries({ queryKey: ["projects", "list"] })} />
      {note && <p role="status" className="text-sm" data-testid="settings-note">{note}</p>}
    </section>
  );
}

function RemoveSection({ projectId, csrf, onDone }: { projectId: string; csrf: string; onDone: () => void }) {
  const [sure, setSure] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => removeProjectFromList(projectId, csrf),
    onSuccess: () => { onDone(); setMsg("Removed from the list. Its folder and every file stay where they are."); },
    onError: (e) => setMsg(explain(e)),
  });
  return (
    <div className="space-y-2">
      <h2 className="font-medium">Remove from the list</h2>
      <p className="text-sm text-muted-foreground">The folder and every file in it stay. You can add the project back later.</p>
      {!sure ? (
        <Button size="sm" variant="outline" onClick={() => setSure(true)} data-testid="remove-start">Remove from the list…</Button>
      ) : (
        <div className="flex gap-2">
          <Button size="sm" variant="destructive" onClick={() => mut.mutate()} disabled={mut.isPending} data-testid="remove-confirm">Yes, remove from the list</Button>
          <Button size="sm" variant="ghost" onClick={() => setSure(false)}>Cancel</Button>
        </div>
      )}
      {msg && <p role="status" className="text-sm" data-testid="remove-message">{msg}</p>}
    </div>
  );
}

