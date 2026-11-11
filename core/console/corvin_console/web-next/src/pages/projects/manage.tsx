/** Adopt, rename, move, protection, chat attachment and the default place (T-0362, T-0387, T-0388). Every action says what it will and
 * will not touch before it runs. */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useAuth } from "@/lib/auth";
import { adoptProject, attachProject, detachProject, getChatAttachment, getHealth, moveProject, renameProject, setDefaultStorage, setProjectSettings, type Access, type DataClass, type DefaultStorage, type ProjectDetail, type ProjectList } from "@/lib/api/projects";
import { ACCESS_META, CLASS_META, CLASS_ORDER, accessOf, chatAccessSentence, classOf, lowersProtection, needsConfirmation } from "./roles";
import { listChatSessions } from "@/lib/api/sessions";
import { explain } from "./wizard";
import { FolderField, FolderPicker } from "@/components/folder-picker";
import { parentOf } from "./picker-logic";

export function AdoptSection() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [location, setLocation] = useState("");
  const [title, setTitle] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => adoptProject({ location: location.trim(), ...(title.trim() ? { title: title.trim() } : {}) }, csrf),
    onSuccess: (r) => { setMsg(`Added ${r.id}.`); setLocation(""); setTitle(""); void qc.invalidateQueries({ queryKey: ["projects", "list"] }); },
    onError: (e) => setMsg(explain(e)),
  });
  if (!open) return <Button size="sm" variant="outline" onClick={() => setOpen(true)} data-testid="adopt-start">Add existing folder</Button>;
  return (
    <form className="w-full basis-full space-y-2 rounded-lg border p-3" data-testid="adopt-form" onSubmit={(e) => { e.preventDefault(); mut.mutate(); }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !mut.isPending) {
          e.preventDefault(); setOpen(false); setMsg(null); setLocation("");
          requestAnimationFrame(() => document.querySelector<HTMLElement>("[data-testid=adopt-start]")?.focus());
        }
      }}>
      <p className="text-sm text-muted-foreground">Any folder of your own. If it has no project.yaml, exactly that one file is added; nothing else in the folder changes.</p>
      <div className="space-y-1 text-sm"><span className="block">Folder</span>
        <FolderField value={location} onChange={(p) => { setLocation(p); setMsg(null); }} purpose="adopt" inputTestId="adopt-location" testid="adopt-folder"
          disabled={mut.isPending} />
      </div>
      <label className="block text-sm">Title (optional)
        <input className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} data-testid="adopt-title" />
      </label>
      <div className="flex gap-2">
        <Button size="sm" type="submit" disabled={mut.isPending || !location.trim()} data-testid="adopt-submit">Add folder</Button>
        <Button size="sm" type="button" variant="ghost" disabled={mut.isPending} onClick={() => { setOpen(false); setMsg(null); setLocation(""); }}>Cancel</Button>
      </div>
      {msg && <p role="status" className="text-sm" data-testid="adopt-note">{msg}</p>}
    </form>
  );
}

/** How long a rename can be undone with one click (CONCEPT-0131 § C1). */
export const UNDO_MS = 10_000;

/** The title, renamed in place; for 10 s an "Undo" sends the old title back. The id never changes. */
export function RenameField({ d, csrf, enabled, onChanged }: { d: ProjectDetail; csrf: string; enabled: boolean; onChanged: () => void }) {
  const [title, setTitle] = useState(d.title ?? "");
  const [undo, setUndo] = useState<string | null>(null);              // the title before the last rename, while Undo is offered
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const offerUndo = (old: string | null) => {
    if (timer.current) clearTimeout(timer.current);
    setUndo(old);
    if (old !== null) timer.current = setTimeout(() => setUndo(null), UNDO_MS);
  };
  const rename = useMutation({
    mutationFn: (v: { to: string; from: string | null }) => renameProject(d.id, v.to, csrf),
    onSuccess: (_r, v) => {
      setTitle(v.to);
      if (v.from === null) { offerUndo(null); setMsg("Undone. The old title is back."); } else { offerUndo(v.from); setMsg("Renamed. Only project.yaml changed."); }
      onChanged();
    },
    onError: (e) => setMsg(explain(e)),
  });
  const current = d.title ?? "";
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <input className="w-full min-w-0 max-w-sm flex-1 rounded border bg-background px-2 py-1 text-sm" value={title} maxLength={120}
          onChange={(e) => setTitle(e.target.value)} aria-label="Project title" data-testid="rename-input" disabled={!enabled}
          placeholder={enabled ? undefined : "the title cannot be read right now"} />
        <Button size="sm" variant="outline" onClick={() => rename.mutate({ to: title.trim(), from: current })}
          disabled={!enabled || rename.isPending || !title.trim() || title.trim() === current} data-testid="rename-submit">Rename</Button>
        {undo !== null && (
          <button type="button" className="text-xs underline" disabled={rename.isPending} data-testid="rename-undo"
            onClick={() => rename.mutate({ to: undo, from: null })}>Undo</button>
        )}
      </div>
      {msg && <p role="status" className="text-sm" data-testid="manage-note">{msg}</p>}
    </div>
  );
}

/** Move the folder: the picker chooses the new place (its verdict shown before Choose), then "Move here" and a second click
 * "Yes, move the folder". Same disk only; the server judges the place again. */
export function MoveFlow({ d, csrf, onChanged }: { d: ProjectDetail; csrf: string; onChanged: () => void }) {
  const [picking, setPicking] = useState(false);
  const [dest, setDest] = useState("");
  const [sure, setSure] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const move = useMutation({
    mutationFn: () => moveProject(d.id, dest, csrf),
    onSuccess: (r) => {
      setSure(false); setDest("");
      // null = the repair after the move failed: the machine-local files still name the old folder (not "nothing to regenerate")
      const repaired = r.repaired_files === null
        ? "The machine-local files could not be regenerated for the new location - run Repair in Maintenance."
        : `${r.repaired_files} machine-local file(s) regenerated for the new location.`;
      const warn = (r.warnings ?? []).filter(Boolean);
      setMsg(`Moved. ${repaired}${warn.length ? ` Note: ${warn.join(" ")}` : ""}`);
      onChanged();
    },
    onError: (e) => { setSure(false); setMsg(explain(e)); onChanged(); },   // the move may have happened (a timeout): show what the server says now
  });
  const cancel = () => { setDest(""); setSure(false); };
  return (
    <div className="space-y-2" data-testid="move-flow">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">Move the folder to another place. Same disk only; your files are not changed, machine-local files are regenerated.</p>
        {!dest && <Button size="sm" variant="outline" className="self-start sm:self-auto" onClick={() => { setPicking(true); setMsg(null); }} data-testid="move-open">Move…</Button>}
      </div>
      <FolderPicker open={picking} purpose="move" projectId={d.id} title={d.title || d.id} initialPath={d.path ? parentOf(d.path) : null}
        inputTestId="move-input" onChoose={(p) => { setDest(p); setSure(false); setPicking(false); }} onCancel={() => setPicking(false)} />
      {dest && (
        <div className="space-y-2 rounded border p-2" data-testid="move-target">
          <p className="text-sm">New place: <span className="break-all font-mono text-xs" data-testid="move-dest">{dest}</span></p>
          {!sure ? (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setSure(true)} data-testid="move-start">Move here</Button>
              <Button size="sm" variant="ghost" onClick={() => setPicking(true)} data-testid="move-change">Choose another…</Button>
              <Button size="sm" variant="ghost" onClick={cancel}>Cancel</Button>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="destructive" onClick={() => move.mutate()} disabled={move.isPending} data-testid="move-confirm">
                {move.isPending ? "Moving…" : "Yes, move the folder"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSure(false)} disabled={move.isPending}>Cancel</Button>
            </div>
          )}
        </div>
      )}
      {msg && <p role="status" className="text-sm" data-testid="move-note">{msg}</p>}
    </div>
  );
}

/** Attach this project to one console chat (and take it off again). */
export function AttachSection({ d, csrf }: { d: ProjectDetail; csrf: string }) {
  const [sid, setSid] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const chats = useQuery({ queryKey: ["chat", "sessions", "for-attach"], queryFn: ({ signal }) => listChatSessions(signal) });
  const current = useQuery({
    queryKey: ["projects", "chat-attachment", sid], enabled: !!sid,
    queryFn: ({ signal }) => getChatAttachment(sid, signal),
  });
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["projects", "chat-attachment", sid] });
  const attach = useMutation({
    mutationFn: () => attachProject(d.id, sid, csrf),
    onSuccess: (r) => { setMsg(r.note); refresh(); },
    onError: (e) => setMsg(explain(e)),
  });
  const detach = useMutation({
    mutationFn: () => detachProject(sid, csrf),
    onSuccess: () => { setMsg("Detached. The chat no longer sees this project."); refresh(); },
    onError: (e) => setMsg(explain(e)),
  });
  const list = chats.data?.sessions ?? [];
  const attachedHere = current.data?.project_id === d.id;
  return (
    <div className="space-y-2" data-testid="attach-section">
      <p className="text-sm text-muted-foreground" data-testid="attach-explain">Attach this project to one console chat: the chat can follow the project's rules and use its tools. {chatAccessSentence(d)} The chat keeps its own folder and history.</p>
      {chats.error ? (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="attach-chats-error">The chats could not be loaded. Reload the page to try again.</p>
      ) : chats.isLoading ? <p className="text-sm text-muted-foreground">Loading chats…</p> : list.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="attach-no-chats">There is no chat yet. Start one in the console, then come back here.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select className="w-full min-w-0 max-w-full rounded border bg-background px-2 py-1 text-sm sm:w-auto sm:max-w-xs" value={sid} onChange={(e) => { setSid(e.target.value); setMsg(null); }} aria-label="Chat" data-testid="attach-chat">
            <option value="">Choose a chat…</option>
            {list.map((c) => <option key={c.sid} value={c.sid}>{c.title || c.sid}</option>)}
          </select>
          {attachedHere ? (
            <Button size="sm" variant="outline" onClick={() => detach.mutate()} disabled={detach.isPending} data-testid="detach-submit">Detach</Button>
          ) : (
            <Button size="sm" variant="outline" onClick={() => attach.mutate()} disabled={!sid || attach.isPending || current.isLoading || !!current.error} data-testid="attach-submit">Attach</Button>
          )}
          {sid && current.data?.project_id && !attachedHere && (
            <span className="text-xs text-muted-foreground" data-testid="attach-other">This chat is attached to another project; attaching replaces it.</span>
          )}
        </div>
      )}
      {sid && current.error && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="attach-state-error">What this chat is attached to could not be read, so Attach is off for now.</p>
      )}
      {attachedHere && current.data?.withheld && (
        <p role="status" className="rounded border border-amber-600/40 p-2 text-sm" data-testid="attach-withheld">
          Attached, but not sent to the assistant right now: {current.data.withheld}
        </p>
      )}
      {msg && <p role="status" className="text-sm" data-testid="attach-note-chat">{msg}</p>}
    </div>
  );
}


/** What the assistant may do in this project, and how sensitive its data is. Lowering protection is asked twice. */
export function ProtectionSection({ d, csrf, onChanged }: { d: ProjectDetail; csrf: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const curCls = classOf(d);
  const curAcc = accessOf(d);
  const [cls, setCls] = useState<DataClass>(curCls);
  const [acc, setAcc] = useState<Access>(curAcc);
  const [ask, setAsk] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // project.yaml edited by hand (health "protection" failed): saving the SAME values rewrites the file, so Save is offered then too
  const health = useQuery({ queryKey: ["projects", "health", d.id], queryFn: ({ signal }) => getHealth(d.id, signal), enabled: !d.builtin, refetchInterval: false });
  const drifted = (health.data?.checks ?? []).some((c) => c.id === "protection" && !c.ok);
  const dirty = cls !== curCls || acc !== curAcc || drifted;
  const mut = useMutation({
    mutationFn: (confirm: boolean) => setProjectSettings(d.id, { classification: cls, assistant_access: acc, confirm }, csrf),
    onSuccess: () => {
      setAsk(false); setMsg("Saved. A chat that has this project attached picks it up with its next message."); onChanged();
      void qc.invalidateQueries({ queryKey: ["projects", "health", d.id] });
    },
    onError: (e) => { if (needsConfirmation(e)) { setAsk(true); setMsg(null); } else { setAsk(false); setMsg(explain(e)); } },
  });
  return (
    <div className="space-y-2" data-testid="protection-section">
      <p className="text-sm text-muted-foreground">How sensitive this project's data is, and what the assistant may do with its files. The data class decides which engines may read it.</p>
      <div className="grid gap-1 sm:grid-cols-2" role="radiogroup" aria-label="Data class">
        {CLASS_ORDER.map((c) => (
          <label key={c} className="flex items-start gap-2 rounded border p-2 text-sm">
            <input type="radio" name="settings-class" className="mt-1" data-testid="settings-class" data-class={c} checked={cls === c}
              disabled={mut.isPending} onChange={() => { setCls(c); setAsk(false); setMsg(null); }} />
            <span><span className="block font-medium">{CLASS_META[c].label}</span><span className="block text-xs text-muted-foreground">{CLASS_META[c].hint}</span></span>
          </label>
        ))}
      </div>
      <div className="flex flex-wrap gap-3" role="radiogroup" aria-label="What the assistant may do">
        {(["read", "write"] as const).map((a) => (
          <label key={a} className="flex items-center gap-2 text-sm" title={ACCESS_META[a].hint}>
            <input type="radio" name="settings-access" data-testid="settings-access" data-access={a} checked={acc === a}
              disabled={mut.isPending} onChange={() => { setAcc(a); setAsk(false); setMsg(null); }} />
            {ACCESS_META[a].label}
          </label>
        ))}
      </div>
      {!ask ? (
        <Button size="sm" variant="outline" disabled={!dirty || mut.isPending} onClick={() => mut.mutate(false)} data-testid="protection-save">Save protection</Button>
      ) : (
        <div className="space-y-2 rounded border border-amber-600/50 p-2" role="alertdialog" aria-label="Confirm lower protection" data-testid="protection-confirm">
          <p className="text-sm">{lowersProtection(curCls, cls, curAcc, acc) ? "This lowers the protection of the project" : "Please confirm"}
            {" "}({CLASS_META[cls].label}, {ACCESS_META[acc].label.toLowerCase()}). Are you sure?</p>
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" disabled={mut.isPending} onClick={() => mut.mutate(true)} data-testid="protection-confirm-yes">Yes, change it</Button>
            <Button size="sm" variant="ghost" disabled={mut.isPending} onClick={() => { setAsk(false); setCls(curCls); setAcc(curAcc); }}>Cancel</Button>
          </div>
        </div>
      )}
      {msg && <p role="status" className="text-sm" data-testid="protection-note">{msg}</p>}
    </div>
  );
}

/** "Default place for new projects" (CONCEPT-0131 § C4): the Corvin workspace or a folder of my own. Saving moves nothing. */
export function DefaultPlaceDialog({ open, onOpenChange, list }: { open: boolean; onOpenChange: (o: boolean) => void; list: ProjectList }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const current: DefaultStorage = list.default_storage === "root" ? "root" : "managed";
  const [kind, setKind] = useState<DefaultStorage>(current);
  const [folder, setFolder] = useState(list.projects_root_set ? list.projects_root : "");
  const [error, setError] = useState<string | null>(null);
  // every opening starts from what is saved, not from what was typed and cancelled
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) { setKind(current); setFolder(list.projects_root_set ? list.projects_root : ""); setError(null); }
  }
  const mut = useMutation({
    mutationFn: () => setDefaultStorage(kind === "root" ? { default_storage: "root", projects_root: folder.trim() } : { default_storage: "managed" }, csrf),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["projects"] }); onOpenChange(false); },
    onError: (e) => setError(explain(e)),
  });
  const unchanged = kind === current && (kind === "managed" || folder.trim() === (list.projects_root_set ? list.projects_root : ""));
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!mut.isPending) onOpenChange(o); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg" data-testid="default-place-dialog">
        <DialogHeader>
          <DialogTitle>Default place for new projects</DialogTitle>
          <DialogDescription>Where the New project wizard puts a project when you do not name a folder.</DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (!unchanged) mut.mutate(); }}>
          <div role="radiogroup" aria-label="Default place" className="space-y-2">
            <label className="flex items-start gap-2 rounded border p-2 text-sm">
              <input type="radio" name="default-place" className="mt-1" checked={kind === "managed"} onChange={() => { setKind("managed"); setError(null); }}
                data-testid="place-managed" disabled={mut.isPending} />
              <span className="min-w-0">
                <span className="block font-medium">Corvin workspace (recommended)</span>
                {list.managed_root && (
                  <span className="block text-xs text-muted-foreground">
                    <span className="break-all font-mono">{list.managed_root}</span> — managed by Corvin for this workspace
                  </span>
                )}
                {list.workspace === "foreign" && (
                  <span className="block text-xs text-amber-800 dark:text-amber-300" data-testid="place-foreign">
                    This workspace belongs to another Corvin installation. Start a new one in a project's Settings, under Location.
                  </span>
                )}
              </span>
            </label>
            <label className="flex items-start gap-2 rounded border p-2 text-sm">
              <input type="radio" name="default-place" className="mt-1" checked={kind === "root"} onChange={() => { setKind("root"); setError(null); }}
                data-testid="place-root" disabled={mut.isPending} />
              <span className="min-w-0 flex-1 space-y-1">
                <span className="block font-medium">A folder of my own</span>
                {kind === "root" && (
                  <FolderField value={folder} onChange={(p) => { setFolder(p); setError(null); }} purpose="projects_root"
                    inputTestId="place-root-input" testid="place-root-folder" disabled={mut.isPending} />
                )}
              </span>
            </label>
          </div>
          <p className="text-xs text-muted-foreground">Projects already somewhere else keep working; nothing is moved.</p>
          {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="place-error">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" disabled={mut.isPending} onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" size="sm" disabled={mut.isPending || unchanged || (kind === "root" && !folder.trim())} data-testid="place-save">
              {mut.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
