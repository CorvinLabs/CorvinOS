/** Adopt, rename and move (T-0362). Every action says what it will and will not touch before it runs. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { adoptProject, attachProject, detachProject, getChatAttachment, moveProject, renameProject, type ProjectDetail } from "@/lib/api/projects";
import { listChatSessions } from "@/lib/api/sessions";
import { explain } from "./wizard";

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
    <form className="w-full basis-full space-y-2 rounded-lg border p-3" data-testid="adopt-form" onSubmit={(e) => { e.preventDefault(); mut.mutate(); }}>
      <p className="text-sm text-muted-foreground">The folder must be inside the projects root. If it has no project.yaml, exactly that one file is added; nothing else in the folder changes.</p>
      <label className="block text-sm">Folder (absolute path)
        <input className="mt-1 w-full rounded border bg-background px-2 py-1 font-mono text-xs" value={location} onChange={(e) => setLocation(e.target.value)} data-testid="adopt-location" />
      </label>
      <label className="block text-sm">Title (optional)
        <input className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} data-testid="adopt-title" />
      </label>
      <div className="flex gap-2">
        <Button size="sm" type="submit" disabled={mut.isPending || !location.trim()} data-testid="adopt-submit">Add folder</Button>
        <Button size="sm" type="button" variant="ghost" onClick={() => { setOpen(false); setMsg(null); }}>Cancel</Button>
      </div>
      {msg && <p role="status" className="text-sm" data-testid="adopt-note">{msg}</p>}
    </form>
  );
}

export function RenameMoveSection({ d, csrf, onChanged }: { d: ProjectDetail; csrf: string; onChanged: () => void }) {
  const [title, setTitle] = useState(d.title ?? "");
  const [dest, setDest] = useState("");
  const [sure, setSure] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const rename = useMutation({
    mutationFn: () => renameProject(d.id, title.trim(), csrf),
    onSuccess: () => { setMsg("Renamed. Only project.yaml changed."); onChanged(); },
    onError: (e) => setMsg(explain(e)),
  });
  const move = useMutation({
    mutationFn: () => moveProject(d.id, dest.trim(), csrf),
    onSuccess: (r) => { setSure(false); setMsg(`Moved. ${r.repaired_files ?? 0} machine-local file(s) regenerated for the new location.`); onChanged(); },
    onError: (e) => { setSure(false); setMsg(explain(e)); },
  });
  return (
    <div className="space-y-6" data-testid="manage-section">
      <div className="space-y-2">
        <h2 className="font-medium">Rename</h2>
        <p className="text-sm text-muted-foreground">Changes the title only. The id, the folder and the knowledge scope stay.</p>
        <div className="flex gap-2">
          <input className="w-full max-w-sm rounded border bg-background px-2 py-1 text-sm" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} aria-label="Project title" data-testid="rename-input" />
          <Button size="sm" variant="outline" onClick={() => rename.mutate()} disabled={rename.isPending || !title.trim() || title.trim() === d.title} data-testid="rename-submit">Rename</Button>
        </div>
      </div>
      <div className="space-y-2">
        <h2 className="font-medium">Move</h2>
        <p className="text-sm text-muted-foreground">Moves the folder to a new place inside the projects root (same disk, never over an existing folder). Your files are not changed; machine-local files are regenerated.</p>
        <input className="w-full max-w-xl rounded border bg-background px-2 py-1 font-mono text-xs" value={dest} onChange={(e) => { setDest(e.target.value); setSure(false); }} placeholder="/absolute/new/location" aria-label="New location" data-testid="move-input" />
        {!sure ? (
          <Button size="sm" variant="outline" onClick={() => setSure(true)} disabled={!dest.trim()} data-testid="move-start">Move…</Button>
        ) : (
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" onClick={() => move.mutate()} disabled={move.isPending} data-testid="move-confirm">Yes, move the folder</Button>
            <Button size="sm" variant="ghost" onClick={() => setSure(false)}>Cancel</Button>
          </div>
        )}
      </div>
      {msg && <p role="status" className="text-sm" data-testid="manage-note">{msg}</p>}
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
      <h2 className="font-medium">Use in a chat</h2>
      <p className="text-sm text-muted-foreground">Attach this project to one console chat: the chat can follow the project's rules and use its tools. The assistant can read the project but not change its files. The chat keeps its own folder and history.</p>
      {chats.isLoading ? <p className="text-sm text-muted-foreground">Loading chats…</p> : list.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="attach-no-chats">There is no chat yet. Start one in the console, then come back here.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select className="rounded border bg-background px-2 py-1 text-sm" value={sid} onChange={(e) => { setSid(e.target.value); setMsg(null); }} aria-label="Chat" data-testid="attach-chat">
            <option value="">Choose a chat…</option>
            {list.map((c) => <option key={c.sid} value={c.sid}>{c.title || c.sid}</option>)}
          </select>
          {attachedHere ? (
            <Button size="sm" variant="outline" onClick={() => detach.mutate()} disabled={detach.isPending} data-testid="detach-submit">Detach</Button>
          ) : (
            <Button size="sm" variant="outline" onClick={() => attach.mutate()} disabled={!sid || attach.isPending} data-testid="attach-submit">Attach</Button>
          )}
          {sid && current.data?.project_id && !attachedHere && (
            <span className="text-xs text-muted-foreground" data-testid="attach-other">This chat is attached to another project; attaching replaces it.</span>
          )}
        </div>
      )}
      {msg && <p role="status" className="text-sm" data-testid="attach-note-chat">{msg}</p>}
    </div>
  );
}
