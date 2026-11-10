/** Adopt, rename and move (T-0362). Every action says what it will and will not touch before it runs. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { adoptProject, attachProject, detachProject, getChatAttachment, getHealth, moveProject, renameProject, setProjectSettings, setProjectsRoot, type Access, type DataClass, type ProjectDetail } from "@/lib/api/projects";
import { ACCESS_META, CLASS_META, CLASS_ORDER, accessOf, classOf, lowersProtection, needsConfirmation } from "./roles";
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
    <form className="w-full basis-full space-y-2 rounded-lg border p-3" data-testid="adopt-form" onSubmit={(e) => { e.preventDefault(); mut.mutate(); }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !mut.isPending) {
          e.preventDefault(); setOpen(false); setMsg(null);
          requestAnimationFrame(() => document.querySelector<HTMLElement>("[data-testid=adopt-start]")?.focus());
        }
      }}>
      <p className="text-sm text-muted-foreground">The folder must be inside the projects root. If it has no project.yaml, exactly that one file is added; nothing else in the folder changes.</p>
      <label className="block text-sm">Folder (absolute path)
        <input className="mt-1 w-full rounded border bg-background px-2 py-1 font-mono text-xs" value={location} onChange={(e) => setLocation(e.target.value)} data-testid="adopt-location" autoFocus />
      </label>
      <label className="block text-sm">Title (optional)
        <input className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} data-testid="adopt-title" />
      </label>
      <div className="flex gap-2">
        <Button size="sm" type="submit" disabled={mut.isPending || !location.trim()} data-testid="adopt-submit">Add folder</Button>
        <Button size="sm" type="button" variant="ghost" disabled={mut.isPending} onClick={() => { setOpen(false); setMsg(null); }}>Cancel</Button>
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
    onSuccess: (r) => {
      setSure(false);
      // null = the repair after the move failed: the machine-local files still name the old folder (not "nothing to regenerate")
      const repaired = r.repaired_files === null
        ? "The machine-local files could not be regenerated for the new location - run Repair above."
        : `${r.repaired_files} machine-local file(s) regenerated for the new location.`;
      const warn = (r.warnings ?? []).filter(Boolean);
      setMsg(`Moved. ${repaired}${warn.length ? ` Note: ${warn.join(" ")}` : ""}`);
      onChanged();
    },
    onError: (e) => { setSure(false); setMsg(explain(e)); onChanged(); },   // the move may have happened (a timeout): show what the server says now
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
        <input className="w-full max-w-xl rounded border bg-background px-2 py-1 font-mono text-xs" value={dest} onChange={(e) => { setDest(e.target.value); setSure(false); }} placeholder={d.path ? `e.g. ${d.path}-new` : "absolute path of the new folder"} aria-label="New location" data-testid="move-input" />
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
      <h2 className="font-medium">Protection</h2>
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

/** Where new projects are created (release review F1): shown on the Projects page, changeable in place. */
export function RootEditor({ current }: { current: string }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(current);
  const [msg, setMsg] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => setProjectsRoot(value.trim(), csrf),
    onSuccess: (r) => {
      setOpen(false);
      setMsg(r.outside.length ? `New projects go to ${r.projects_root}. Already outside it and still working: ${r.outside.join(", ")}.` : `New projects go to ${r.projects_root}.`);
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => setMsg(explain(e)),
  });
  return (
    <span className="inline" data-testid="root-editor">
      {!open ? (
        <button type="button" className="ml-2 text-xs underline" onClick={() => { setValue(current); setOpen(true); setMsg(null); }} data-testid="root-change">Change folder</button>
      ) : (
        <form className="mt-2 flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); mut.mutate(); }} data-testid="root-form"
          onKeyDown={(e) => {
            if (e.key === "Escape" && !mut.isPending) {
              e.preventDefault(); setOpen(false);
              requestAnimationFrame(() => document.querySelector<HTMLElement>("[data-testid=root-change]")?.focus());
            }
          }}>
          <input className="min-w-0 flex-1 rounded border bg-background px-2 py-1 font-mono text-xs" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Projects folder" data-testid="root-input" />
          <Button size="sm" type="submit" disabled={mut.isPending || !value.trim() || value.trim() === current} data-testid="root-save">Save</Button>
          <Button size="sm" type="button" variant="ghost" disabled={mut.isPending} onClick={() => setOpen(false)}>Cancel</Button>
        </form>
      )}
      {msg && <span className="mt-1 block break-all text-xs" role="status" data-testid="root-note">{msg}</span>}
    </span>
  );
}
