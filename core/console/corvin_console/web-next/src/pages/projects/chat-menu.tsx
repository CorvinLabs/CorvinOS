/**
 * The project button in a chat's header (CONCEPT-0127 D5): shows which project this chat works in, with its class and what the
 * assistant may do; opens a menu with a multiple-choice list of projects ("No project" first), links to the project's storage and
 * settings, and the change view. One click attaches; a refusal (the data class may not go to this engine) is said in words.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, AlertTriangle, ChevronDown, FileDiff, FolderKanban, FolderOpen, Settings } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { attachProject, detachProject, getActivity, getChanges, getChatAttachment, listProjects, type ProjectSummary } from "@/lib/api/projects";
import { AccessBadge, ClassBadge } from "./badges";
import { anyWorking } from "./activity-logic";
import { ChangesPanel, changesKey } from "./changes-panel";
import { ExplorerPanel } from "./explorer-panel";
import { accessOf, badgeLine, classOf, roleLabel } from "./roles";
import { explain } from "./wizard";

const attachKey = (sid: string) => ["projects", "chat-attachment", sid] as const;

export function ProjectMenu({ sid, streaming }: { sid: string; streaming: boolean }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState(false);
  const [explorer, setExplorer] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const att = useQuery({ queryKey: attachKey(sid), queryFn: ({ signal }) => getChatAttachment(sid, signal), staleTime: 15_000, retry: false });
  const list = useQuery({ queryKey: ["projects", "list"], queryFn: ({ signal }) => listProjects(signal), staleTime: 10_000, retry: false, enabled: open || !!att.data?.project_id });
  const pid = att.data?.project_id ?? null;
  const projects: ProjectSummary[] = (list.data?.projects ?? []).filter((p) => p.state === "ok");
  const current = projects.find((p) => p.id === pid);
  const changes = useQuery({ queryKey: changesKey(pid ?? "", null), enabled: !!pid, retry: false, refetchInterval: 15_000,
    queryFn: ({ signal }) => getChanges(pid as string, null, signal) });
  const count = changes.data?.files.length ?? 0;
  // the live dot reads the cheap snapshot (it starts no watcher and opens no stream): an agent working on this project, in ANY chat
  const activity = useQuery({ queryKey: ["projects", "activity-dot", pid ?? ""], enabled: !!pid, retry: false, refetchInterval: 8000,
    queryFn: ({ signal }) => getActivity(pid as string, null, signal) });
  const live = !!pid && anyWorking(activity.data?.agents ?? []);

  // a finished turn may have changed files: look again as soon as the answer is complete
  const wasStreaming = useRef(streaming);
  useEffect(() => {
    if (wasStreaming.current && !streaming && pid) void qc.invalidateQueries({ queryKey: ["projects", "changes", pid] });
    wasStreaming.current = streaming;
  }, [streaming, pid, qc]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const choose = useMutation({
    mutationFn: async (id: string | null) => (id ? attachProject(id, sid, csrf) : detachProject(sid, csrf)),
    onSuccess: () => { setError(null); setOpen(false); void qc.invalidateQueries({ queryKey: attachKey(sid) }); void qc.invalidateQueries({ queryKey: ["projects", "changes"] }); },
    onError: (e) => setError(explain(e)),
  });

  const label = current?.title ?? att.data?.title ?? (pid ? pid : "Project");
  return (
    <div ref={root} className="relative" data-testid="project-menu">
      <button type="button" onClick={() => { setOpen((o) => !o); setError(null); }} aria-haspopup="dialog" aria-expanded={open}
        data-testid="project-menu-button" data-attached={pid ? "yes" : "no"}
        title={pid && current ? `${label} - ${badgeLine(current)}` : "Choose a project for this chat"}
        className="inline-flex max-w-[14rem] items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground">
        {att.data?.withheld ? <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600" aria-label="Not sent to the assistant" data-testid="project-menu-withheld-icon" />
          : <FolderKanban className="h-3.5 w-3.5 shrink-0" aria-hidden />}
        <span className="truncate" data-testid="project-menu-label">{label}</span>
        {live && <span className="h-2 w-2 shrink-0 rounded-full bg-emerald-500 motion-safe:animate-pulse" data-testid="project-live-dot" role="img" aria-label="An agent is working on this project" />}
        {pid && count > 0 && <span className="rounded-full bg-primary/15 px-1.5 font-mono text-[10px] text-foreground" data-testid="project-menu-count" aria-label={`${count} changed files`}>{count}</span>}
        <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
      </button>

      {open && (
        <div role="dialog" aria-label="Project for this chat" data-testid="project-menu-panel"
          className="fixed inset-x-2 top-14 z-40 max-h-[75vh] space-y-2 overflow-auto rounded-lg border border-border bg-popover p-2 text-sm shadow-lg sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-1 sm:w-[22rem]">
          <p className="px-1 text-xs text-muted-foreground">Project for this chat</p>
          {att.data?.withheld && (
            <p role="status" className="rounded border border-amber-600/40 p-2 text-xs" data-testid="project-menu-withheld">
              Attached, but not sent to the assistant right now: {att.data.withheld}
            </p>
          )}
          <div role="radiogroup" aria-label="Project" className="space-y-1">
            <label className="flex cursor-pointer items-center gap-2 rounded border p-2 hover:border-primary/40">
              <input type="radio" name="chat-project" checked={!pid} data-testid="project-option" data-project="" disabled={choose.isPending}
                onChange={() => pid && choose.mutate(null)} />
              <span>No project</span>
            </label>
            {list.isLoading && <p className="px-1 text-xs text-muted-foreground">Loading projects…</p>}
            {projects.map((p) => (
              <label key={p.id} className="flex cursor-pointer items-start gap-2 rounded border p-2 hover:border-primary/40">
                <input type="radio" name="chat-project" className="mt-1" checked={pid === p.id} data-testid="project-option" data-project={p.id}
                  disabled={choose.isPending} onChange={() => choose.mutate(p.id)} />
                <span className="min-w-0 flex-1 space-y-1">
                  <span className="block truncate font-medium">{p.title ?? p.id}</span>
                  {(p.builtin || roleLabel(p.role)) && <span className="block text-xs text-muted-foreground">{p.builtin ? "Built in - Corvin's own knowledge" : roleLabel(p.role)}</span>}
                  <span className="flex flex-wrap gap-1"><ClassBadge cls={classOf(p)} /><AccessBadge access={accessOf(p)} /></span>
                </span>
              </label>
            ))}
            {list.data && projects.length === 0 && (
              <p className="px-1 text-xs text-muted-foreground" data-testid="project-menu-none">No project is ready yet. <Link className="underline" to="/app/projects">Create one</Link>.</p>
            )}
          </div>
          {error && <p role="alert" className="rounded border border-red-600/40 p-2 text-xs text-red-800 dark:text-red-300" data-testid="project-menu-error">{error}</p>}
          <div className="flex flex-wrap items-center gap-1 border-t pt-2">
            {pid && (
              <>
                <button type="button" onClick={() => { setExplorer(true); setPanel(false); setOpen(false); }} className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-muted" data-testid="project-menu-explorer">
                  <Activity className="h-3.5 w-3.5" aria-hidden /> Explorer
                </button>
                <button type="button" onClick={() => { setPanel(true); setExplorer(false); setOpen(false); }} className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-muted" data-testid="project-menu-changes">
                  <FileDiff className="h-3.5 w-3.5" aria-hidden /> Changes{count > 0 ? ` (${count})` : ""}
                </button>
                <Link to={`/app/projects/${encodeURIComponent(pid)}?tab=structure`} className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-muted" data-testid="project-menu-storage">
                  <FolderOpen className="h-3.5 w-3.5" aria-hidden /> Storage
                </Link>
                <Link to={`/app/projects/${encodeURIComponent(pid)}?tab=settings`} className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-muted" data-testid="project-menu-settings">
                  <Settings className="h-3.5 w-3.5" aria-hidden /> Settings
                </Link>
              </>
            )}
            <Link to="/app/projects" className="ml-auto rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted" data-testid="project-menu-all">All projects</Link>
          </div>
        </div>
      )}
      {panel && pid && <ChangesPanel projectId={pid} title={label} onClose={() => setPanel(false)} />}
      {explorer && pid && <ExplorerPanel projectId={pid} title={label} onClose={() => setExplorer(false)} onOpenChanges={() => { setExplorer(false); setPanel(true); }} />}
    </div>
  );
}
