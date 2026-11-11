/**
 * The project Settings tab as sections (T-0387, CONCEPT-0131 § C1/C2): General · Location · Protection · Use in a chat ·
 * Maintenance · Share · Danger zone. Which sections and actions are drawn is `settingsSections()` (inspector-logic.ts, unit-tested);
 * this file only draws them. Every action goes through the server, which judges it again. The only file deletion is "Delete project
 * files…" of a project in the Corvin workspace: it moves the folder to the Trash for 30 days (ADR-2321 Decision 12).
 */
import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { deleteProjectFiles, getHealth, listProjects, listTemplates, openProject, rekeyWorkspace, relocateProject, removeProjectFromList, repairProject, type OpenCommands, type ProjectDetail } from "@/lib/api/projects";
import { cn } from "@/lib/utils";
import { templateLabel, whereLabel } from "./encodings";
import { DELETE_ROW_TEXT, DELETE_SENTENCE, MARKER_TRASH, confirmMatches, doctorSummary, settingsSections, type ActionGate, type SectionId } from "./inspector-logic";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { AttachSection, MoveFlow, ProtectionSection, RenameField } from "./manage";
import { StorageBadge } from "./badges";
import { roleLabel } from "./roles";
import { explain } from "./wizard";
import { FolderPicker } from "@/components/folder-picker";
import { parentOf } from "./picker-logic";
import { DuplicateDialog, ExportDialog } from "./share-dialogs";

const KEY = (id: string) => ["projects", "detail", id] as const;
const HEALTH = (id: string) => ["projects", "health", id] as const;
const LIST = ["projects", "list"] as const;

/** The typed confirmation for a new workspace; the words are the action, so they cannot be typed by accident. */
export const REKEY_CONFIRM = "new workspace";

/** `onNotice`: a confirmation that must outlive this tree - a successful "Locate folder" makes the project usable again, and the detail page
 * then draws a different branch, which unmounts the box that said "Found." (live picker spec, review round 2 of the initiative). */
export function SettingsSections({ d, onRemoved, onNotice }: { d: ProjectDetail; onRemoved: (msg: string) => void; onNotice?: (msg: string) => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const list = useQuery({ queryKey: LIST, queryFn: ({ signal }) => listProjects(signal), staleTime: 10_000 });
  const sections = settingsSections(d, { workspace: list.data?.workspace });
  // a link to one section (`#settings-protection`, from a "tools need edit access" note) lands on it
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash.startsWith("#settings-")) return;
    requestAnimationFrame(() => document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" }));
  }, [hash]);
  const changed = () => {
    void qc.invalidateQueries({ queryKey: KEY(d.id) });
    void qc.invalidateQueries({ queryKey: HEALTH(d.id) });
    void qc.invalidateQueries({ queryKey: LIST });
  };
  const body = (id: SectionId, actions: Partial<Record<string, ActionGate>>, notes: string[]): ReactNode => {
    switch (id) {
      case "general": return <General d={d} csrf={csrf} rename={actions.rename} onChanged={changed} />;
      case "location": return <Location d={d} csrf={csrf} actions={actions} notes={notes} where={list.data} onChanged={changed} onNotice={onNotice} />;
      case "protection": return <ProtectionSection d={d} csrf={csrf} onChanged={() => void qc.invalidateQueries({ queryKey: KEY(d.id) })} />;
      case "chat": return <AttachSection d={d} csrf={csrf} />;
      case "maintenance": return <Maintenance d={d} csrf={csrf} repair={actions.repair} onRemoved={onRemoved} />;
      case "share": return <Share d={d} actions={actions} />;
      case "danger": return <Danger d={d} csrf={csrf} actions={actions} onRemoved={onRemoved} />;
    }
  };
  return (
    <section className="space-y-4" aria-label="Settings" data-testid="settings-tab">
      {d.builtin && (
        <p className="text-sm text-muted-foreground" data-testid="builtin-note">
          Corvin is built in. It is Corvin's own knowledge base, board and graph: it cannot be removed, renamed, moved or reconfigured, and
          the assistant can only read it. You can attach it to a chat below.
        </p>
      )}
      {sections.map((s) => (
        <div key={s.id} id={`settings-${s.id}`} data-testid="settings-section" data-section={s.id}
          className={cn("space-y-3 rounded-lg border p-4", s.id === "danger" && "border-red-500/40")}>
          <h2 className={cn("font-medium", s.id === "danger" && "text-red-700 dark:text-red-300")}>{s.title}</h2>
          <div data-testid={`${s.id}-section`}>{body(s.id, s.actions, s.notes)}</div>
        </div>
      ))}
    </section>
  );
}

/** One label/value row: two columns from 640 px, stacked on a phone. */
function Row({ label, children, testid }: { label: string; children: ReactNode; testid?: string }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[8rem_minmax(0,1fr)] sm:items-center sm:gap-3" data-testid={testid}>
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState<"copied" | "failed" | null>(null);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone("copied"); } catch { setDone("failed"); }
    setTimeout(() => setDone(null), 2000);
  };
  return (
    <button type="button" className="shrink-0 text-xs underline" onClick={() => void copy()} aria-label={label}>
      {done === "copied" ? "Copied" : done === "failed" ? "Copy failed - select the text" : "Copy"}
    </button>
  );
}

function General({ d, csrf, rename, onChanged }: { d: ProjectDetail; csrf: string; rename?: ActionGate; onChanged: () => void }) {
  // the same cache the Projects list fills: the template's title instead of its id (REVIEW-0067 #8)
  const templates = useQuery({ queryKey: ["projects", "templates"], queryFn: ({ signal }) => listTemplates(signal), staleTime: 30_000, retry: false });
  return (
    <div className="space-y-2">
      <Row label="Title">
        <RenameField d={d} csrf={csrf} enabled={!!rename?.enabled} onChanged={onChanged} />
        {rename && !rename.enabled && rename.note && <p className="mt-1 text-xs text-muted-foreground">{rename.note}</p>}
      </Row>
      <Row label="Id">
        <span className="flex items-center gap-2"><span className="font-mono text-sm" data-testid="general-id">{d.id}</span><CopyButton text={d.id} label="Copy the id" /></span>
      </Row>
      <Row label="Template"><span className="text-sm">{templateLabel(d.template, templates.data?.templates)}</span></Row>
      {roleLabel(d.role) && <Row label="Role"><span className="text-sm">{roleLabel(d.role)}</span></Row>}
      {d.added_at && <Row label="Added"><span className="text-sm">{d.added_at.slice(0, 10)}</span></Row>}
    </div>
  );
}

function Location({ d, csrf, actions, notes, where, onChanged, onNotice }: {
  d: ProjectDetail; csrf: string; actions: Partial<Record<string, ActionGate>>; notes: string[];
  where?: { projects_root: string; projects_root_set?: boolean }; onChanged: () => void; onNotice?: (msg: string) => void;
}) {
  const w = whereLabel(d, { projects_root: where?.projects_root, projects_root_set: where?.projects_root_set });
  const [opened, setOpened] = useState<string | null>(null);
  const [openedAll, setOpenedAll] = useState<OpenCommands | null>(null);
  const [openNote, setOpenNote] = useState<string | null>(null);
  // a command shown for the OLD folder must not survive a move or a relink
  const [shownFor, setShownFor] = useState(d.path);
  if (shownFor !== d.path) { setShownFor(d.path); setOpened(null); setOpenedAll(null); }
  const openMut = useMutation({
    mutationFn: () => openProject(d.id, csrf),
    onSuccess: (r) => { setOpenNote(null); setOpened(r.command); setOpenedAll(r.commands ?? null); },
    onError: (e) => setOpenNote(explain(e)),
  });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {w.kind !== "builtin" && <StorageBadge kind={w.kind} title={w.title} />}
        <span className="min-w-0 break-all font-mono text-sm" title={d.path} data-testid="location-path">{d.display_path || d.path}</span>
        <CopyButton text={d.path} label="Copy the full path" />
        {d.state === "ok" && (
          <button type="button" className="text-xs underline" onClick={() => openMut.mutate()} disabled={openMut.isPending} data-testid="open-project">Show open command</button>
        )}
      </div>
      {openNote && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="open-note">{openNote}</p>}
      {opened && !openedAll && <code className="block break-all rounded bg-muted p-2 text-xs" data-testid="open-command">{opened}</code>}
      {openedAll && (
        <dl className="grid grid-cols-1 gap-x-3 gap-y-1 text-xs sm:grid-cols-[auto_minmax(0,1fr)]" data-testid="open-commands">
          <dt className="text-muted-foreground">PowerShell</dt><dd><code className="break-all">{openedAll.powershell}</code></dd>
          <dt className="text-muted-foreground">cmd.exe</dt>
          <dd>{openedAll.cmd ? <code className="break-all">{openedAll.cmd}</code>
            : <span className="text-muted-foreground" data-testid="open-cmd-none">not offered: the folder name contains % or !, which cmd.exe would expand</span>}</dd>
          <dt className="text-muted-foreground">bash / zsh</dt><dd><code className="break-all">{openedAll.posix}</code></dd>
        </dl>
      )}
      {notes.map((n) => (
        <p key={n} className="rounded border border-amber-600/50 p-2 text-sm" data-testid="location-warning">{n}</p>
      ))}
      {actions.move && <MoveFlow d={d} csrf={csrf} onChanged={onChanged} />}
      {actions.locate && <LocateBox d={d} csrf={csrf} onChanged={onChanged} onNotice={onNotice} />}
      {actions.rekey && <RekeyBox csrf={csrf} onChanged={onChanged} />}
    </div>
  );
}

/** "Folder missing": point the list at the folder that now holds this project, chosen in the picker. Moves nothing. */
function LocateBox({ d, csrf, onChanged, onNotice }: { d: ProjectDetail; csrf: string; onChanged: () => void; onNotice?: (msg: string) => void }) {
  const [picking, setPicking] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: (loc: string) => relocateProject(d.id, loc, csrf),
    onSuccess: (r) => {
      const warn = (r.warnings ?? []).filter(Boolean);
      const text = `Found. The list now points at the new folder.${warn.length ? ` Note: ${warn.join(" ")}` : ""}`;
      setMsg(text);
      onNotice?.(text);                     // the box itself disappears once the project is usable again: the page keeps the confirmation
      onChanged();
    },
    onError: (e) => setMsg(explain(e)),
  });
  return (
    <div className="space-y-2 rounded border border-red-500/40 p-3" data-testid="locate-box">
      <p className="text-sm">Folder missing: {d.reason || "the folder is not where the list says."}</p>
      <p className="text-sm text-muted-foreground">If you moved or renamed it, point the list at it. Nothing is moved or copied.</p>
      <Button size="sm" variant="outline" disabled={mut.isPending} onClick={() => { setPicking(true); setMsg(null); }} data-testid="locate-open">
        {mut.isPending ? "Locating…" : "Locate folder…"}
      </Button>
      <FolderPicker open={picking} purpose="relocate" projectId={d.id} title={d.title || d.id} initialPath={d.path ? parentOf(d.path) : null}
        inputTestId="locate-input" onChoose={(p) => { setPicking(false); mut.mutate(p); }} onCancel={() => setPicking(false)} />
      {msg && <p role="status" className="text-sm" data-testid="locate-note">{msg}</p>}
    </div>
  );
}

/** A workspace copied from another installation: start an empty one for this one, after typing the words. */
function RekeyBox({ csrf, onChanged }: { csrf: string; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => rekeyWorkspace(csrf),
    onSuccess: () => { setOpen(false); setTyped(""); setMsg("Done. New projects go to a new workspace for this installation; the old folder was not touched."); onChanged(); },
    onError: (e) => setMsg(explain(e)),
  });
  return (
    <div className="space-y-2 rounded border border-amber-600/50 p-3" data-testid="rekey-box">
      <p className="text-sm">This workspace belongs to another Corvin installation.</p>
      <p className="text-sm text-muted-foreground">Projects in it stay where they are. A new workspace starts empty for this installation.</p>
      {!open ? (
        <Button size="sm" variant="outline" onClick={() => { setOpen(true); setMsg(null); }} data-testid="rekey-start">Start a new workspace for this installation</Button>
      ) : (
        <div className="space-y-2">
          <label className="block text-sm">Type <span className="font-mono">{REKEY_CONFIRM}</span> to confirm
            <input className="mt-1 w-full max-w-xs rounded border bg-background px-2 py-1 text-sm" value={typed} autoFocus
              onChange={(e) => setTyped(e.target.value)} data-testid="rekey-input" />
          </label>
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" disabled={typed !== REKEY_CONFIRM || mut.isPending} onClick={() => mut.mutate()} data-testid="rekey-confirm">Start a new workspace</Button>
            <Button size="sm" variant="ghost" onClick={() => { setOpen(false); setTyped(""); }}>Cancel</Button>
          </div>
        </div>
      )}
      {msg && <p role="status" className="text-sm" data-testid="rekey-note">{msg}</p>}
    </div>
  );
}

function Maintenance({ d, csrf, repair, onRemoved }: { d: ProjectDetail; csrf: string; repair?: ActionGate; onRemoved: (msg: string) => void }) {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: HEALTH(d.id), queryFn: ({ signal }) => getHealth(d.id, signal), refetchInterval: false });
  const [preview, setPreview] = useState<{ pending: { path: string; status: string; diff: string }[] } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const previewMut = useMutation({
    mutationFn: () => repairProject(d.id, true, csrf),
    onSuccess: (r) => { setPreview(r); setNote(null); },
    onError: (e) => setNote(explain(e)),
  });
  const applyMut = useMutation({
    mutationFn: () => repairProject(d.id, false, csrf),
    onSuccess: (r) => {
      // removing an unfinished import takes the entry off the list: back to the list with the server's sentence, like Remove
      if (r.removed_import) { onRemoved(r.message || "The unfinished import was removed."); return; }
      setPreview(null); setNote("Repaired. Only generated and machine-local files were rewritten.");
      void qc.invalidateQueries({ queryKey: KEY(d.id) }); void qc.invalidateQueries({ queryKey: HEALTH(d.id) });
    },
    onError: (e) => setNote(explain(e)),
  });
  const failed = (health.data?.checks ?? []).some((c) => !c.ok && !c.note);
  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
        <p className={cn("text-sm", failed && "text-amber-800 dark:text-amber-300")} data-testid="doctor-summary">
          {health.isLoading ? "Checking…" : health.error ? "The checks could not run." : doctorSummary(health.data?.checks ?? [])}
        </p>
        {/* a project that is not ready has no Overview tab; its checks are listed above Settings on the same page */}
        {d.state === "ok" && <Link to={`/app/projects/${encodeURIComponent(d.id)}?tab=overview`} className="shrink-0 text-xs underline" data-testid="doctor-open">Open Overview</Link>}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground" data-testid="repair-what">{d.state === "pending"
          ? "Repair removes the unfinished import: the entry leaves the list and the import's leftover folders beside it are deleted; a folder at the project's place is kept."
          : "Repair restores generated and machine-local files (CLAUDE.md sections, MCP settings); your text is kept."}</p>
        <Button size="sm" variant="outline" className="self-start sm:self-auto" onClick={() => previewMut.mutate()}
          disabled={!repair?.enabled || previewMut.isPending} title={repair?.note ?? undefined} data-testid="repair-preview">Preview changes</Button>
      </div>
      {repair && !repair.enabled && repair.note && <p className="text-xs text-muted-foreground">{repair.note}</p>}
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
      {note && <p role="status" className="text-sm" data-testid="settings-note">{note}</p>}
    </div>
  );
}

function Share({ d, actions }: { d: ProjectDetail; actions: Partial<Record<string, ActionGate>> }) {
  const [open, setOpen] = useState<"export" | "duplicate" | null>(null);
  const rows: { id: "export" | "duplicate"; text: string; label: string }[] = [
    { id: "export", text: "Export this project as a ZIP for another installation or a colleague.", label: "Export…" },
    { id: "duplicate", text: "Make a copy under a new id, with its own folder.", label: "Duplicate…" },
  ];
  return (
    <div className="space-y-2">
      {rows.map((r) => actions[r.id] && (
        <div key={r.id} className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">{r.text}</p>
          {/* a disabled button receives no hover: the wrapper carries the tooltip, the note is also written out */}
          <span title={actions[r.id]?.note ?? undefined} className="flex shrink-0 flex-col items-start gap-1 self-start sm:items-end sm:self-auto">
            <Button size="sm" variant="outline" disabled={!actions[r.id]?.enabled} title={actions[r.id]?.note ?? undefined}
              onClick={() => setOpen(r.id)} data-testid={`share-${r.id}`}>{r.label}</Button>
            {!actions[r.id]?.enabled && actions[r.id]?.note && <span className="text-xs text-muted-foreground" data-testid={`share-${r.id}-note`}>{actions[r.id]?.note}</span>}
          </span>
        </div>
      ))}
      <p className="text-xs text-muted-foreground">Import a project from the Projects list ("Import project…").</p>
      <ExportDialog open={open === "export"} onOpenChange={(o) => setOpen(o ? "export" : null)} ids={[d.id]} label={d.title || d.id} />
      <DuplicateDialog open={open === "duplicate"} onOpenChange={(o) => setOpen(o ? "duplicate" : null)} d={d} />
    </div>
  );
}

function Danger({ d, csrf, actions, onRemoved }: { d: ProjectDetail; csrf: string; actions: Partial<Record<string, ActionGate>>; onRemoved: (msg: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-3">
      {actions.remove && <RemoveRow projectId={d.id} csrf={csrf} onDone={onRemoved} gate={actions.remove} />}
      {actions.delete && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between" data-testid="delete-row">
          <p className="text-sm">{DELETE_ROW_TEXT}</p>
          <Button size="sm" variant="outline" className="self-start border-red-500/40 text-red-700 hover:bg-red-500/10 dark:text-red-300 sm:self-auto"
            disabled={!actions.delete.enabled} onClick={() => setOpen(true)} data-testid="delete-files">Delete project files…</Button>
        </div>
      )}
      {actions.delete && <DeleteFilesDialog d={d} csrf={csrf} open={open} onOpenChange={setOpen} />}
    </div>
  );
}

/** The typed-id dialog: "Move to Trash" is enabled only on the exact id; on success the list opens and shows "Trash (n)". */
function DeleteFilesDialog({ d, csrf, open, onOpenChange }: { d: ProjectDetail; csrf: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const mut = useMutation({
    mutationFn: () => deleteProjectFiles(d.id, typed, csrf),
    onMutate: () => setError(null),
    onSuccess: () => {
      qc.removeQueries({ queryKey: KEY(d.id) });
      qc.removeQueries({ queryKey: HEALTH(d.id) });
      void qc.invalidateQueries({ queryKey: LIST });
      void qc.invalidateQueries({ queryKey: ["projects", "trash"] });
      void qc.invalidateQueries({ queryKey: ["projects", "chat-attachment"] });   // the server detached every chat from it
      onOpenChange(false);
      navigate("/app/projects", { state: { notice: `${d.title || d.id} was moved to the Trash. You can restore it there for 30 days.` } });
    },
    onError: (e) => setError(explain(e)),
  });
  const ok = confirmMatches(typed, d.id);
  const close = (o: boolean) => { if (mut.isPending) return; if (!o) { setTyped(""); setError(null); } onOpenChange(o); };
  return (
    <Dialog open={open} onOpenChange={close}>
      {open && (
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md" data-testid="delete-dialog">
          <DialogHeader>
            <DialogTitle className="break-words pr-6">Delete the files of "{d.title || d.id}"?</DialogTitle>
            <DialogDescription data-testid="delete-sentence">{DELETE_SENTENCE}</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (ok && !mut.isPending) mut.mutate(); }}>
            <label className="block text-sm">Type the project id <span className="font-mono font-semibold">{d.id}</span> to confirm
              <input className="mt-1 w-full rounded border bg-background px-2 py-1 font-mono text-sm" value={typed} maxLength={28} spellCheck={false}
                autoComplete="off" autoFocus onChange={(e) => { setTyped(e.target.value); setError(null); }} disabled={mut.isPending}
                aria-label="Project id" data-testid="delete-confirm" />
            </label>
            {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="delete-error">{error}</p>}
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" size="sm" variant="ghost" onClick={() => close(false)} disabled={mut.isPending} data-testid="delete-cancel">Cancel</Button>
              <Button type="submit" size="sm" variant="destructive" disabled={!ok || mut.isPending} data-testid="delete-submit">
                {mut.isPending ? "Moving…" : MARKER_TRASH}
              </Button>
            </div>
          </form>
        </DialogContent>
      )}
    </Dialog>
  );
}

function RemoveRow({ projectId, csrf, onDone, gate }: { projectId: string; csrf: string; onDone: (msg: string) => void; gate?: ActionGate }) {
  const [sure, setSure] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => removeProjectFromList(projectId, csrf),
    onSuccess: () => onDone("Removed from the list. Its folder and every file stay where they are."),
    onError: (e) => setMsg(explain(e)),
  });
  return (
    <div className="space-y-2">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm">Remove from the list — the folder and every file stay.</p>
        {!sure ? (
          <Button size="sm" variant="outline" className="self-start sm:self-auto" onClick={() => setSure(true)} disabled={gate ? !gate.enabled : false}
            title={gate?.note ?? undefined} data-testid="remove-start">Remove from the list…</Button>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="destructive" onClick={() => mut.mutate()} disabled={mut.isPending} data-testid="remove-confirm">Yes, remove from the list</Button>
            <Button size="sm" variant="ghost" onClick={() => setSure(false)}>Cancel</Button>
          </div>
        )}
      </div>
      {gate && !gate.enabled && gate.note && <p className="text-xs text-muted-foreground" data-testid="remove-note">{gate.note}</p>}
      {msg && <p role="status" className="text-sm" data-testid="remove-message">{msg}</p>}
    </div>
  );
}
