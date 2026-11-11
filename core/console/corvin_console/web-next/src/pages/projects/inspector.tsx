/**
 * Project inspector - one project: its state and checks, its features, and the settings a user may change
 * (repair, open, remove from the list). Every change goes through the server, which judges it. A file is
 * never deleted from here.
 *
 * Tabs built here: Overview, Features; the Settings tab's sections are settings-sections.tsx; Structure, Docs, Knowledge, Tasks, Videos, Directory and Compute live in
 * inspector-tabs.tsx.
 */
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { createVideo, getHealth, getProject, setFeatures, type FeatureRow, type ProjectDetail } from "@/lib/api/projects";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { explain } from "./wizard";
import { TABS, checkDetail, conflictOf, healthHeadline, parseTab, rowState, tabGate, videoNameProblem, type Conflict, type Tab } from "./inspector-logic";
import { ComputeTab, DirectoryTab, DocsTab, FeatureOff, KnowledgeTab, StructureTab, TasksTab, VideosTab } from "./inspector-tabs";
import { stateOf } from "./encodings";
import { SettingsSections } from "./settings-sections";
import { Glance } from "./inspector-glance";
import { AccessBadge, ClassBadge } from "./badges";
import { accessOf, classOf, roleLabel } from "./roles";

const KEY = (id: string) => ["projects", "detail", id] as const;
const HEALTH = (id: string) => ["projects", "health", id] as const;

export default function ProjectInspectorPage() {
  const { projectId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const requestedTab = parseTab(params.get("tab"));
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  // set once the project was removed from the list HERE: the cached detail would otherwise keep showing it as Ready with every action
  const [removed, setRemoved] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);       // warnings a feature switch returned (a GET has none)
  const detail = useQuery({ queryKey: KEY(projectId), queryFn: ({ signal }) => getProject(projectId, signal), enabled: !!projectId && !removed,
    refetchInterval: 60_000 });                         // not the app-wide 15 s: the detail builds the catalogue every time
  const d: ProjectDetail | undefined = detail.data;
  const ready = d?.state === "ok";
  // Corvin itself has fewer tabs: a ?tab= the nav does not offer falls back to the overview instead of rendering a dead switch
  const tab: Tab = d?.builtin && !BUILTIN_TABS.includes(requestedTab) ? "overview" : requestedTab;
  const onRemoved = (msg: string) => {
    setRemoved(msg);
    qc.removeQueries({ queryKey: KEY(projectId) });
    qc.removeQueries({ queryKey: HEALTH(projectId) });
    void qc.invalidateQueries({ queryKey: ["projects", "list"] });
    void qc.invalidateQueries({ queryKey: ["projects", "chat-attachment"] });   // the server detached every chat from it
  };

  if (removed) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-6" data-testid="inspector-removed">
        <BackLink />
        <h1 className="text-xl font-semibold">Removed from the list</h1>
        <p role="status" className="text-sm" data-testid="remove-message">{removed}</p>
      </div>
    );
  }
  if (detail.isLoading) return <div className="p-6"><Skeleton className="h-32 w-full" data-testid="inspector-loading" /></div>;
  if (detail.error && !d && !(detail.error instanceof ApiError && detail.error.status === 404)) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-6" data-testid="inspector-error">
        <BackLink />
        <h1 className="text-xl font-semibold">The project could not be loaded</h1>
        <p role="alert" className="text-sm text-red-700 dark:text-red-300">{explain(detail.error)}</p>
        <Button size="sm" variant="outline" onClick={() => void detail.refetch()} disabled={detail.isFetching} data-testid="inspector-retry">Try again</Button>
      </div>
    );
  }
  if (!d || (detail.error instanceof ApiError && detail.error.status === 404)) {
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
      {detail.error && (
        <p role="alert" className="rounded border border-red-500/40 p-2 text-sm text-red-700 dark:text-red-300" data-testid="inspector-refresh-error">
          The latest refresh failed ({explain(detail.error)}); what you see may be out of date.
        </p>
      )}
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="flex min-w-0 items-center gap-2 text-xl font-semibold" data-testid="inspector-title">
            <span className="min-w-0 truncate" title={d.title ?? d.id}>{d.title ?? d.id}</span>
            {d.builtin && <span className="rounded-full border px-2 py-0.5 text-[11px] font-normal text-muted-foreground" data-testid="inspector-builtin">Built in</span>}
          </h1>
          {/* the same short path the list and Location show; the full one on hover (REVIEW-0067 #13) */}
          <p className="truncate font-mono text-xs text-muted-foreground" title={d.path} data-testid="inspector-path">{d.display_path || d.path}</p>
          {d.state === "ok" && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5" data-testid="inspector-protection">
              {roleLabel(d.role) && <span className="text-xs font-medium">{roleLabel(d.role)}</span>}
              <ClassBadge cls={classOf(d)} /><AccessBadge access={accessOf(d)} />
            </div>
          )}
        </div>
        <span data-testid="inspector-state" className={cn("rounded-md border px-2 py-1 text-xs",
          st.tone === "ok" ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300" : "border-red-500/40 text-red-700 dark:text-red-300")}>
          {st.label}
        </span>
      </header>

      {!ready && (
        <>
          <Card><CardContent className="space-y-3 p-4">
            <p className="text-sm" data-testid="inspector-not-ready">{st.detail || "This project cannot be opened right now."}</p>
          </CardContent></Card>
          {/* a missing or unusable project keeps the settings that do not need its folder: Locate folder, Remove from the list */}
          {!d.builtin && <SettingsSections d={d} onRemoved={onRemoved} />}
        </>
      )}

      {ready && (
        <>
          <Glance d={d} />
          {notice && (
            <p role="status" className="rounded border border-amber-600/50 p-2 text-sm" data-testid="feature-notice">
              {notice} <button type="button" className="ml-2 text-xs underline" onClick={() => setNotice(null)}>Dismiss</button>
            </p>
          )}
          <nav role="tablist" aria-label="Project sections" className="flex flex-wrap gap-1 border-b"
            onKeyDown={(e) => {
              // arrow keys move between tabs (WAI-ARIA tabs pattern)
              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
              const ids = (d.builtin ? TABS.filter((t) => BUILTIN_TABS.includes(t.id)) : TABS).map((t) => t.id);
              const i = ids.indexOf(tab);
              const nextTab = ids[(i + (e.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length];
              e.preventDefault();
              setTab(nextTab);
              requestAnimationFrame(() => document.getElementById(`project-tab-${nextTab}`)?.focus());
            }}>
            {(d.builtin ? TABS.filter((t) => BUILTIN_TABS.includes(t.id)) : TABS).map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} data-testid={`tab-${t.id}`}
                id={`project-tab-${t.id}`} aria-controls="project-tabpanel" tabIndex={tab === t.id ? 0 : -1}
                onClick={() => setTab(t.id)}
                className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t.id ? "border-primary font-medium" : "border-transparent text-muted-foreground")}>
                {t.label}
              </button>
            ))}
          </nav>
          <div role="tabpanel" id="project-tabpanel" aria-labelledby={`project-tab-${tab}`}>
          {(() => {
            const gate = tabGate(tab, d.active ?? []);
            if (!gate.open && gate.feature) return <FeatureOff d={d} feature={gate.feature} onNotice={setNotice} />;
            switch (tab) {
              case "overview": return <OverviewTab projectId={d.id} builtin={!!d.builtin} videosOn={(d.active ?? []).includes("videos")} csrf={csrf} onChanged={() => void qc.invalidateQueries({ queryKey: KEY(d.id) })} />;
              case "structure": return <StructureTab id={d.id} />;
              case "docs": return <DocsTab id={d.id} />;
              case "knowledge": return <KnowledgeTab id={d.id} />;
              case "tasks": return <TasksTab id={d.id} />;
              case "videos": return <VideosTab id={d.id} />;
              case "directory": return <DirectoryTab d={d} />;
              case "compute": return <ComputeTab id={d.id} />;
              case "features": return <FeaturesTab d={d} csrf={csrf} onChanged={() => void qc.invalidateQueries({ queryKey: KEY(d.id) })} />;
              case "settings": return <SettingsSections d={d} onRemoved={onRemoved} />;
            }
          })()}
          </div>
        </>
      )}
    </div>
  );
}

/** Drawn only while the videos feature is on (REVIEW-0067 M5): a project without videos does not open on an unrelated card. */
function NewVideoCard({ projectId, csrf, onCreated }: { projectId: string; csrf: string; onCreated: () => void }) {
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
          {result && <p role="status" className="break-all text-sm" data-testid="new-video-result">{result}</p>}
          {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="new-video-error">{error}</p>}
        </>
    </div>
  );
}

function BackLink() {
  return (
    <Link to="/app/projects" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
      aria-label="Back to all projects" data-testid="inspector-back">
      <ArrowLeft className="h-4 w-4" aria-hidden /> All projects
    </Link>
  );
}

function OverviewTab({ projectId, builtin, videosOn, csrf, onChanged }: { projectId: string; builtin: boolean; videosOn: boolean; csrf: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: HEALTH(projectId), queryFn: ({ signal }) => getHealth(projectId, signal), refetchInterval: false });
  const head = healthHeadline(health.data?.checks ?? []);
  return (
    <section className="space-y-3" aria-label="Checks" data-testid="overview-tab">
      {!builtin && videosOn && <NewVideoCard projectId={projectId} csrf={csrf} onCreated={() => {
        onChanged();
        // the new folder, its INDEX row and (with the KB) its epic and tasks show in these tabs at once
        void qc.invalidateQueries({ queryKey: ["projects", "structure", projectId] });
        void qc.invalidateQueries({ queryKey: ["task-tracking", "items", `project:${projectId}`] });
      }} />}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <p className={cn("text-sm font-medium", head.ok ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300")} data-testid="health-headline">
            {health.isLoading ? "Checking…" : head.text}
          </p>
          {!builtin && (health.data?.checks ?? []).some((c) => !c.ok) && (
            <Link to={`/app/projects/${encodeURIComponent(projectId)}?tab=settings`} className="text-sm underline" data-testid="health-fix-link">Open Settings to repair</Link>
          )}
        </div>
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
            <span><span className="block font-medium">{CHECK_LABEL[c.id] ?? c.id}</span><span className="text-muted-foreground" title={c.detail} data-testid="health-detail">{checkDetail(c)}</span></span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const CHECK_LABEL: Record<string, string> = {
  path_under_base: "Folder is where it belongs", no_engine_copy: "No engine copy inside the project",
  import_leftovers: "No unfinished import beside it", pending_import: "No import left half-done", hint_source: "Path rules available",
  protection: "Protection setting",
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
    onSuccess: (r) => {
      setConflict(null);
      const warn = (r.warnings ?? []).filter(Boolean);
      setMessage(`Saved. Generated files were updated; nothing you wrote was touched.${warn.length ? ` Note: ${warn.join(" ")}` : ""}`);
      onChanged();
    },
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
            <li key={f.id} data-testid="feature-row" data-feature={f.id} data-active={String(rs.on)} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="font-medium">{rs.label}</span>{" "}
                  <span data-testid="feature-status" className={cn("text-xs", rs.on ? "text-emerald-700 dark:text-emerald-300" : "text-muted-foreground")}>
                    {rs.status}
                  </span>
                  {f.description && <p className="text-xs text-muted-foreground">{f.description}</p>}
                </div>
                {f.id !== "core" && (
                  <Button size="sm" variant={f.active ? "outline" : "default"} data-testid="feature-action"
                    disabled={!rs.canToggle || mut.isPending} onClick={() => mut.mutate({ feature: f.id, on: !f.active })}>
                    {f.active ? "Switch off" : "Switch on"}
                  </Button>
                )}
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

/** Corvin itself has no features to switch, no folder to move and nothing to remove: it can be looked at and attached to a chat. */
const BUILTIN_TABS: Tab[] = ["overview", "structure", "docs", "knowledge", "tasks", "settings"];
