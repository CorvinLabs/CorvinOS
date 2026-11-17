/**
 * "Only for me" — the owner's encrypted personal space inside the Knowledge Graph panel.
 *
 * States: not available on this build (404) · could not be opened (reason + retry) · open (tabs). There is
 * no login: the tenant's default space opens by itself. Every number comes from the server; nothing is invented. Personal tasks appear in Tasks under "Only for me" as well.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, Lock, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import * as pk from "@/lib/api/personal-kb";
import { GraphCanvas } from "../GraphCanvas";
import { DecoderPanel } from "./DecoderPanel";

/** Deploy marker (a string literal): only the personal-space bundle contains it. */
export const MARKER_PERSONAL = "Your personal space is encrypted on this computer and kept apart from the project knowledge base. There is no login: whoever can open this console can open it.";

export const KEY = ["personal-kb"] as const;

function errText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return "Please wait a moment and try again.";
    if (e.status === 423) return "The personal space could not be opened. Reload the page.";
    return e.message || fallback;
  }
  return fallback;
}

export function PersonalSpace() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const status = useQuery({ queryKey: [...KEY, "status"], queryFn: ({ signal }) => pk.getStatus(signal), retry: false });

  if (status.isPending) {
    return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  }
  if (status.isError) {
    const missing = status.error instanceof ApiError && status.error.status === 404;
    return (
      <Card data-testid="personal-unavailable"><CardContent className="py-6 text-sm text-muted-foreground flex items-center gap-2">
        <AlertCircle size={16} /> {missing ? "The personal space is not available on this build." : "The personal space could not be loaded."}
      </CardContent></Card>
    );
  }
  const st = status.data;
  return (
    <div className="space-y-4" data-testid="personal-space">
      <p className="text-sm text-muted-foreground flex items-center gap-2"><ShieldCheck size={16} className="text-accent" />{MARKER_PERSONAL}</p>
      {st.unlocked ? <Unlocked status={st} csrf={csrf} onChanged={() => qc.invalidateQueries({ queryKey: [...KEY] })} />
        : <Unavailable reason={st.reason} onRetry={() => qc.invalidateQueries({ queryKey: [...KEY] })} />}
    </div>
  );
}

// There is no login: the console opens the tenant's default space on its own. The create and unlock forms
// (handle + passphrase) are gone from this view; the routes stay for a later login flow.

function Unavailable({ reason, onRetry }: { reason?: string; onRetry: () => void }) {
  return (
    <Card data-testid="personal-not-open"><CardContent className="py-6 text-sm flex flex-wrap items-center gap-3">
      <AlertCircle size={16} className="text-muted-foreground" />
      <span className="text-muted-foreground">The personal space could not be opened{reason ? `: ${reason}` : "."}</span>
      <Button size="sm" variant="outline" onClick={onRetry}>Try again</Button>
    </CardContent></Card>
  );
}

// ---- unlocked -----------------------------------------------------------------------------------

function Unlocked({ status, csrf, onChanged }: { status: pk.PersonalStatus; csrf: string; onChanged: () => void }) {
  const lock = useMutation({ mutationFn: () => pk.lockSpace(csrf), onSuccess: onChanged });
  const repair = useMutation({ mutationFn: () => pk.removeUnreadable(csrf), onSuccess: onChanged });
  return (
    <>
      <Card><CardContent className="py-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span><span className="text-muted-foreground">Entries:</span> <span className="font-mono tabular-nums">{status.entries ?? 0}</span></span>
        <span><span className="text-muted-foreground">Waiting for review:</span> <span className="font-mono tabular-nums">{status.review_pending ?? 0}</span></span>
        <span><span className="text-muted-foreground">Automatic learning:</span> {status.auto_capture ? "on" : "off"}{status.frozen && <Badge variant="warn" className="ml-2">paused</Badge>}</span>
        <span><span className="text-muted-foreground">Mode:</span> {status.default ? "always open on this computer" : status.mode === "strict" ? "console only" : "use in my chats"}</span>
        {(status.problems ?? 0) > 0 && <>
          <Badge variant="danger">{status.problems} unreadable entries</Badge>
          <Button size="sm" variant="outline" disabled={!csrf || repair.isPending} data-testid="personal-repair"
            onClick={() => { if (window.confirm(`Remove ${status.problems} unreadable entries for good? They cannot be opened any more.`)) repair.mutate(); }}>
            Remove unreadable entries
          </Button>
        </>}
        {status.has_passphrase && (
          <Button className="ml-auto" size="sm" variant="outline" disabled={!csrf || lock.isPending} onClick={() => lock.mutate()} data-testid="personal-lock">
            <Lock className="w-4 h-4" /> Lock
          </Button>
        )}
      </CardContent></Card>
      <Tabs defaultValue="entries">
        <TabsList>
          <TabsTrigger value="entries">Entries</TabsTrigger>
          <TabsTrigger value="graph">Graph</TabsTrigger>
          <TabsTrigger value="learned">Recently learned{(status.review_pending ?? 0) > 0 ? ` (${status.review_pending})` : ""}</TabsTrigger>
          <TabsTrigger value="learning">How Corvin learns</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="entries" className="mt-4"><Entries csrf={csrf} onChanged={onChanged} /></TabsContent>
        <TabsContent value="graph" className="mt-4"><PersonalGraph /></TabsContent>
        <TabsContent value="learned" className="mt-4"><Learned csrf={csrf} onChanged={onChanged} /></TabsContent>
        <TabsContent value="learning" className="mt-4"><Learning csrf={csrf} onChanged={onChanged} /></TabsContent>
        <TabsContent value="settings" className="mt-4"><Settings status={status} csrf={csrf} onChanged={onChanged} /></TabsContent>
      </Tabs>
    </>
  );
}

const TYPE_LABEL: Record<pk.EntityType, string> = { preference: "Setting", context: "About me", idea: "Idea", task: "Task", note: "Note" };

/** What the consistency check could not decide on its own (ADR-2329). Shown only when there is something to decide. */
function NeedsYourLook({ csrf, onChanged }: { csrf: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: [...KEY, "consistency"], queryFn: ({ signal }) => pk.getConsistency(signal), retry: false });
  const [msg, setMsg] = useState<string | null>(null);
  const after = () => { qc.invalidateQueries({ queryKey: [...KEY] }); onChanged(); };
  const fail = (e: unknown) => setMsg(errText(e, "That did not work."));
  const ack = useMutation({ mutationFn: (i: pk.ReviewItem) => pk.acknowledgePair(i.uid, i.other_uid ?? "", csrf), onSuccess: after, onError: fail });
  const keep = useMutation({ mutationFn: (i: pk.ReviewItem) => pk.dismissReview(i.uid, "STUCK_REALIZATION", csrf), onSuccess: after, onError: fail });
  const close = useMutation({ mutationFn: (i: pk.ReviewItem) => pk.closeIdea(i.uid, csrf), onSuccess: after, onError: (e) => { fail(e); after(); } });
  if (q.isError) return <p className="text-xs text-muted-foreground">The consistency check could not run.</p>;
  const items = (q.data?.items ?? []).filter((i) => ["DUPLICATE_IDEAS", "STUCK_REALIZATION", "DECODER_STATE_LOST", "DUPLICATE_CHECK_CAPPED"].includes(i.code));
  if (!items.length) return null;
  const busy = ack.isPending || keep.isPending || close.isPending;
  const name = (id?: string | null, title?: string) => `${id ?? ""} ${title ? `"${title}"` : ""}`.trim();
  return (
    <Card data-testid="personal-needs-look"><CardContent className="p-4 space-y-2 text-sm">
      <h3 className="font-medium">Needs your look</h3>
      <ul className="space-y-2">
        {items.map((i, n) => (
          <li key={`${i.code}-${i.uid}-${n}`} className="flex flex-wrap items-center gap-2" data-testid={`personal-review-${i.code}`}>
            {i.code === "DUPLICATE_IDEAS" && <>
              <span>{name(i.display_id, i.title)} looks like {name(i.other_display_id, i.other_title)}.</span>
              <Button size="sm" variant="outline" disabled={!csrf || busy || !i.other_uid} onClick={() => ack.mutate(i)}>They are different</Button>
            </>}
            {i.code === "STUCK_REALIZATION" && <>
              <span>{name(i.display_id, i.title)}: every task of its plan is finished.</span>
              <Button size="sm" variant="accent" disabled={!csrf || busy} onClick={() => close.mutate(i)}>Close as realized</Button>
              <Button size="sm" variant="ghost" disabled={!csrf || busy} onClick={() => keep.mutate(i)}>Keep it open</Button>
            </>}
            {i.code === "DECODER_STATE_LOST" && <span className="text-muted-foreground">{name(i.display_id, i.title)} lost its development steps. Notes and tasks you changed are kept; developing it again replaces the drafts you never changed.</span>}
            {i.code === "DUPLICATE_CHECK_CAPPED" && <span className="text-muted-foreground">There are too many ideas to compare them all at once; the newest ones were compared first.</span>}
          </li>
        ))}
      </ul>
      {msg && <p role="status" className="text-xs text-muted-foreground">{msg}</p>}
    </CardContent></Card>
  );
}

function Entries({ csrf, onChanged }: { csrf: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const [type, setType] = useState<pk.EntityType | "">("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  // The search text never leaves the browser: it is not put in a URL (access logs keep URLs), it filters here.
  const list = useQuery({ queryKey: [...KEY, "entities", type], queryFn: ({ signal }) => pk.listEntities(signal, { type: type || undefined }), retry: false });
  const [newType, setNewType] = useState<pk.EntityType>("idea");
  const [title, setTitle] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: [...KEY] }); onChanged(); };
  const add = useMutation({
    mutationFn: () => pk.createEntity({ type: newType, title }, csrf),
    onSuccess: () => { setTitle(""); setErr(null); refresh(); },
    onError: (e) => setErr(errText(e, "Not saved.")),
  });
  const forget = useMutation({ mutationFn: (uid: string) => pk.forgetEntity(uid, csrf), onSuccess: refresh });
  const needle = q.trim().toLowerCase();
  const entities = (list.data?.entities ?? []).filter((e) => !needle
    || e.title.toLowerCase().includes(needle) || (e.body ?? "").toLowerCase().includes(needle)
    || (e.display_id ?? "").toLowerCase().includes(needle));
  const openIdea = entities.find((e) => e.uid === open && e.type === "idea");
  return (
    <div className="space-y-4">
      <NeedsYourLook csrf={csrf} onChanged={refresh} />
      <Card><CardContent className="p-4">
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }} data-testid="personal-add-form">
          <select aria-label="Entry type" className="h-10 rounded-md border border-input bg-background px-2 text-sm" value={newType} onChange={(e) => setNewType(e.target.value as pk.EntityType)}>
            {(["idea", "task", "note", "context", "preference"] as pk.EntityType[]).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
          </select>
          <Input className="flex-1 min-w-[220px]" aria-label="New entry" placeholder="Write a sentence" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
          <Button type="submit" size="sm" variant="accent" disabled={!csrf || !title.trim() || add.isPending}>Add</Button>
        </form>
        {err && <p role="alert" className="mt-2 text-xs text-destructive">{err}</p>}
      </CardContent></Card>
      <div className="flex flex-wrap gap-2">
        <select aria-label="Filter by type" className="h-9 rounded-md border border-input bg-background px-2 text-sm" value={type} onChange={(e) => setType(e.target.value as pk.EntityType | "")}>
          <option value="">All types</option>{(Object.keys(TYPE_LABEL) as pk.EntityType[]).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
        </select>
        <Input className="max-w-xs" aria-label="Search entries" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {list.isPending ? <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /> : list.isError ? (
        <p className="text-sm text-destructive">{errText(list.error, "The entries could not be loaded.")}</p>
      ) : entities.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg" data-testid="personal-empty">
          {q || type ? "No entry matches." : "Nothing here yet. Add your first idea or note above, or tell Corvin something about yourself in a private chat."}
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border" data-testid="personal-entries">
          {entities.map((e) => (
            <li key={e.uid} className="p-3 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">{e.display_id}</span>
                <Badge variant="outline">{TYPE_LABEL[e.type]}</Badge>
                {e.key && <Badge variant="secondary">{e.key}</Badge>}
                <span className="flex-1 min-w-[160px]">{e.title}</span>
                {e.review === "pending" && <Badge variant="warn">to review</Badge>}
                <span className="text-xs text-muted-foreground">{e.status.replace("_", " ")}</span>
                {e.type === "idea" && <Button size="sm" variant="outline" onClick={() => setOpen(open === e.uid ? null : e.uid)}>{open === e.uid ? "Hide steps" : "Develop"}</Button>}
                <Button size="sm" variant="outline" onClick={() => setEditing(editing === e.uid ? null : e.uid)}>{editing === e.uid ? "Close editor" : "Edit"}</Button>
                <Button size="sm" variant="ghost" disabled={!csrf || forget.isPending}
                  onClick={() => { if (window.confirm(e.type === "idea"
                    ? "Forget this idea for good? Corvin will not learn it again. The drafts its development steps made and you never changed go with it; notes and tasks you changed stay."
                    : "Forget this entry for good? Corvin will not learn it again.")) forget.mutate(e.uid); }}>Forget</Button>
              </div>
              {e.body && <p className="text-sm text-muted-foreground whitespace-pre-wrap">{e.body}</p>}
              {editing === e.uid && <EntryEditor entry={e} csrf={csrf} onChanged={refresh} />}
              {openIdea && openIdea.uid === e.uid && <DecoderPanel idea={openIdea} csrf={csrf} onChanged={refresh} />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Edit one entry, bring back an earlier version, or share an idea with the project knowledge base. */
function EntryEditor({ entry, csrf, onChanged }: { entry: pk.PersonalEntity; csrf: string; onChanged: () => void }) {
  const [title, setTitle] = useState(entry.title);
  const [body, setBody] = useState(entry.body ?? "");
  const [summary, setSummary] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const detail = useQuery({ queryKey: [...KEY, "entity", entry.uid, entry.version], queryFn: ({ signal }) => pk.getEntity(entry.uid, signal), retry: false });
  const fail = (e: unknown) => setMsg(errText(e, "Not saved."));
  const save = useMutation({ mutationFn: () => pk.updateEntity(entry.uid, { title, body, expected_version: entry.version }, csrf),
    onSuccess: () => { setMsg("Saved."); onChanged(); }, onError: fail });
  const restore = useMutation({ mutationFn: (v: number) => pk.restoreEntity(entry.uid, v, csrf),
    onSuccess: () => { setMsg("The earlier version is back."); onChanged(); }, onError: fail });
  const promote = useMutation({ mutationFn: () => pk.promoteIdea(entry.uid, summary, body, "idea", csrf),
    onSuccess: (r) => { setMsg(`Shared with the project as ${r.project_id}.`); onChanged(); }, onError: fail });
  const versions = (detail.data?.versions ?? []).filter((v) => v !== entry.version).sort((a, b) => b - a);
  const canPromote = entry.type === "idea" && entry.status !== "promoted";
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3 space-y-3" data-testid="personal-entry-editor">
      <form className="space-y-2" onSubmit={(ev) => { ev.preventDefault(); save.mutate(); }}>
        <Input aria-label="Title" value={title} maxLength={200} onChange={(ev) => setTitle(ev.target.value)} />
        <textarea aria-label="Details" className="w-full min-h-[72px] rounded-md border border-input bg-background px-3 py-2 text-sm"
          value={body} maxLength={8000} onChange={(ev) => setBody(ev.target.value)} />
        <Button type="submit" size="sm" variant="accent" disabled={!csrf || !title.trim() || save.isPending}>Save</Button>
      </form>
      {versions.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">Earlier versions:</span>
          {versions.map((v) => (
            <Button key={v} size="sm" variant="ghost" disabled={!csrf || restore.isPending}
              onClick={() => { if (window.confirm(`Bring back version ${v}? The current text stays as a version too.`)) restore.mutate(v); }}>Version {v}</Button>
          ))}
        </div>
      )}
      {canPromote && (
        <form className="flex flex-wrap items-center gap-2" onSubmit={(ev) => { ev.preventDefault();
          if (window.confirm("Share this idea with the project? Its summary and details are copied into the project knowledge base, where every member of this workspace can read them.")) promote.mutate(); }}>
          <Input className="flex-1 min-w-[220px]" aria-label="Summary for the project" placeholder="Summary for the project (no personal details)"
            value={summary} maxLength={200} onChange={(ev) => setSummary(ev.target.value)} />
          <Button type="submit" size="sm" variant="outline" disabled={!csrf || !summary.trim() || promote.isPending}>Share with the project</Button>
        </form>
      )}
      {msg && <p role="status" className="text-xs text-muted-foreground">{msg}</p>}
    </div>
  );
}

function PersonalGraph() {
  const graph = useQuery({ queryKey: [...KEY, "graph"], queryFn: ({ signal }) => pk.getPersonalGraph(signal), retry: false });
  const [selected, setSelected] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [layout, setLayout] = useState<"force" | "islands" | null>(null);
  const colours = useMemo(() => ({ superseded: "#8a8a8a", active: "#8a8a8a", inbox: "#ca9b49", exploring: "#c08a1e", realized: "#2a78c6", promoted: "#2a78c6",
    open: "#8a8a8a", in_progress: "#c08a1e", blocked: "#b0306a", done: "#2a78c6", cancelled: "#775822", archived: "#775822", retired: "#775822", dropped: "#775822" }), []);
  if (graph.isPending) return <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />;
  if (graph.isError) return <p className="text-sm text-destructive">{errText(graph.error, "The graph could not be loaded.")}</p>;
  if (!graph.data.unlocked) return <p className="text-sm text-muted-foreground">Your personal space was locked. Unlock it again.</p>;
  const nodes = graph.data.entities.map((e) => ({ ...e, label: e.label ?? e.id }));
  if (nodes.length === 0) return <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">Your graph is empty. Entries and their links appear here.</div>;
  return <GraphCanvas nodes={nodes} edges={graph.data.relations} selectedKey={selected} statusColours={colours} onSelect={setSelected}
    layoutMode={layout ?? "force"} onLayoutModeChange={setLayout} fullscreen={fullscreen} onFullscreen={setFullscreen} />;
}

function Learned({ csrf, onChanged }: { csrf: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const feed = useQuery({ queryKey: [...KEY, "feed"], queryFn: ({ signal }) => pk.getFeed(signal), retry: false });
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const act = useMutation({
    mutationFn: ({ uid, action, title }: { uid: string; action: "accept" | "edit" | "forget"; title?: string }) => pk.reviewEntity(uid, action, csrf, title),
    onSuccess: () => { setEditing(null); qc.invalidateQueries({ queryKey: [...KEY] }); onChanged(); },
  });
  if (feed.isPending) return <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />;
  if (feed.isError) return <p className="text-sm text-destructive">{errText(feed.error, "Could not load.")}</p>;
  const items = feed.data.entries;
  if (items.length === 0) return <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg" data-testid="personal-feed-empty">Nothing waits for your review.</div>;
  return (
    <ul className="divide-y divide-border rounded-lg border border-border" data-testid="personal-feed">
      {items.map((e) => (
        <li key={e.uid} className="p-3 flex flex-wrap items-center gap-2">
          <Badge variant="outline">{TYPE_LABEL[e.type]}</Badge>
          {e.key && <span className="text-xs text-muted-foreground">{e.key}</span>}
          {editing === e.uid
            ? <Input className="flex-1 min-w-[160px]" aria-label="Corrected value" value={value} onChange={(ev) => setValue(ev.target.value)} />
            : <span className="flex-1 min-w-[160px]">{e.title}</span>}
          <span className="text-xs text-muted-foreground">{e.origin === "derived" ? "imported" : "learned automatically"}</span>
          {editing === e.uid
            ? <Button size="sm" variant="accent" disabled={!value.trim()} onClick={() => act.mutate({ uid: e.uid, action: "edit", title: value })}>Save</Button>
            : <>
                <Button size="sm" variant="accent" disabled={!csrf} onClick={() => act.mutate({ uid: e.uid, action: "accept" })}>Keep</Button>
                <Button size="sm" variant="outline" onClick={() => { setEditing(e.uid); setValue(e.title); }}>Correct</Button>
                <Button size="sm" variant="ghost" disabled={!csrf} onClick={() => act.mutate({ uid: e.uid, action: "forget" })}>Forget</Button>
              </>}
        </li>
      ))}
    </ul>
  );
}

const PARAM_LABEL: Record<string, string> = {
  confidence_floor: "How sure Corvin must be before it keeps a fact",
  daily_cap: "Most facts learned per day",
  context_ttl_days: "Days an unreviewed fact about you is kept",
  "type_enabled.preference": "Learn settings (language, tone, format …)",
  "type_enabled.context": "Learn facts about you (role, tools …)",
  "type_enabled.idea": "Collect ideas you mention",
  extractor_variant: "Learning style",
  decoder_adversarial_rounds: "Critical checks per development step",
  decoder_task_cap: "Most tasks per plan",
  decoder_template_variant: "How strict development steps are",
};

function Learning({ csrf, onChanged }: { csrf: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const pol = useQuery({ queryKey: [...KEY, "policy"], queryFn: ({ signal }) => pk.getPolicy(signal), retry: false });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [err, setErr] = useState<string | null>(null);
  const done = () => { setDraft({}); setErr(null); qc.invalidateQueries({ queryKey: [...KEY] }); onChanged(); };
  const save = useMutation({ mutationFn: () => pk.editPolicy(draft, csrf), onSuccess: done, onError: (e) => setErr(errText(e, "Not saved.")) });
  const confirm = useMutation({ mutationFn: ({ id, ok }: { id: string; ok: boolean }) => pk.confirmPolicy(id, ok, csrf), onSuccess: done, onError: (e) => setErr(errText(e, "Not applied.")) });
  const unfreeze = useMutation({ mutationFn: () => pk.unfreezePolicy(csrf), onSuccess: done });
  const rollback = useMutation({ mutationFn: (v: number) => pk.rollbackPolicy(v, csrf), onSuccess: done, onError: (e) => setErr(errText(e, "Not restored.")) });
  if (pol.isPending) return <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />;
  if (pol.isError) return <p className="text-sm text-destructive">{errText(pol.error, "Could not load.")}</p>;
  const p = pol.data;
  const val = (k: string) => (k in draft ? draft[k] : p.current[k]);
  return (
    <div className="space-y-4" data-testid="personal-learning">
      <p className="text-sm text-muted-foreground">Corvin adjusts these settings from how you review what it learned. It can make them stricter on its own; anything that would make it learn more waits for your confirmation. You can always set them yourself.</p>
      {p.frozen && (
        <Card className="border-amber-500/40"><CardContent className="py-3 flex flex-wrap items-center gap-3 text-sm">
          Learning adjustments are paused because you corrected or forgot many recent entries. Corvin still learns, with the stricter settings.
          <Button size="sm" variant="outline" disabled={!csrf} onClick={() => unfreeze.mutate()}>Resume adjustments</Button>
        </CardContent></Card>
      )}
      {p.pending && (
        <Card className="border-accent/40" data-testid="personal-policy-pending"><CardContent className="py-3 space-y-2 text-sm">
          <p>Corvin suggests learning more: {Object.entries(p.pending.target).filter(([k, v]) => p.current[k] !== v).map(([k, v]) => `${PARAM_LABEL[k] ?? k}: ${String(v)}`).join(" · ")}</p>
          <div className="flex gap-2">
            <Button size="sm" variant="accent" disabled={!csrf} onClick={() => confirm.mutate({ id: p.pending!.change_id, ok: true })}>Accept</Button>
            <Button size="sm" variant="outline" disabled={!csrf} onClick={() => confirm.mutate({ id: p.pending!.change_id, ok: false })}>Decline</Button>
          </div>
        </CardContent></Card>
      )}
      <Card><CardContent className="p-4 space-y-3">
        {Object.entries(p.allowlist).map(([k, spec]) => (
          <label key={k} className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>{PARAM_LABEL[k] ?? k}</span>
            {spec.kind === "bool" ? (
              <Switch checked={Boolean(val(k))} onCheckedChange={(c: boolean) => setDraft({ ...draft, [k]: c })} aria-label={PARAM_LABEL[k] ?? k} />
            ) : spec.kind === "enum" ? (
              <select className="h-9 rounded-md border border-input bg-background px-2" value={String(val(k))} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}>
                {spec.choices.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            ) : (
              <Input className="w-28" type="number" step={spec.kind === "float" ? 0.05 : 1} min={spec.min ?? undefined} max={spec.max ?? undefined}
                value={String(val(k))} onChange={(e) => setDraft({ ...draft, [k]: spec.kind === "float" ? parseFloat(e.target.value) : parseInt(e.target.value, 10) })} />
            )}
          </label>
        ))}
        {err && <p role="alert" className="text-xs text-destructive">{err}</p>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="accent" disabled={!csrf || Object.keys(draft).length === 0 || save.isPending} onClick={() => save.mutate()}>Save</Button>
          {Object.keys(draft).length > 0 && <Button size="sm" variant="ghost" onClick={() => setDraft({})}>Discard</Button>}
          {p.versions > 1 && (
            <select aria-label="Restore an earlier version" className="h-9 rounded-md border border-input bg-background px-2 text-sm" value=""
              onChange={(e) => e.target.value && rollback.mutate(parseInt(e.target.value, 10))}>
              <option value="">Restore an earlier version…</option>
              {Array.from({ length: p.versions }, (_, i) => i + 1).filter((v) => v !== p.version).reverse().map((v) => <option key={v} value={v}>Version {v}</option>)}
            </select>
          )}
        </div>
        <p className="text-xs text-muted-foreground">Current version: {p.version || "defaults"}. Health, religion, politics and similar topics, passwords and facts about other people are never learned automatically; that cannot be changed here.</p>
      </CardContent></Card>
    </div>
  );
}

function Settings({ status, csrf, onChanged }: { status: pk.PersonalStatus; csrf: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [oldP, setOldP] = useState("");
  const [newP, setNewP] = useState("");
  const [erasePass, setErasePass] = useState("");
  const [eraseHistory, setEraseHistory] = useState(false);
  const [rekeyPass, setRekeyPass] = useState("");
  const [newCode, setNewCode] = useState<string | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: [...KEY] }); onChanged(); };
  const auto = useMutation({ mutationFn: (on: boolean) => pk.setAutoCapture(on, csrf), onSuccess: refresh });
  const pair = useMutation({ mutationFn: () => pk.issuePairCode(csrf), onSuccess: (r) => setCode(r.code) });
  const pending = useQuery({ queryKey: [...KEY, "pairs"], queryFn: ({ signal }) => pk.pendingPairs(signal), refetchInterval: code ? 3000 : false, retry: false });
  const confirm = useMutation({ mutationFn: (id: string) => pk.confirmPair(id, csrf), onSuccess: (r) => { setCode(null); setMsg(`Linked ${r.linked.channel} ${r.linked.display || r.linked.masked}.${r.migrated ? ` ${r.migrated} older notes were imported for your review.` : ""}`); refresh(); } });
  const unbind = useMutation({ mutationFn: (i: number) => pk.unbindIdentity(i, csrf), onSuccess: refresh });
  const passM = useMutation({ mutationFn: () => pk.changePassphrase(oldP, newP, csrf), onSuccess: () => { setOldP(""); setNewP(""); setMsg("Passphrase changed."); }, onError: (e) => setMsg(errText(e, "Not changed.")) });
  const rekey = useMutation({ mutationFn: () => pk.rekeySpace(rekeyPass, csrf), onSuccess: (r) => { setRekeyPass(""); setNewCode(r.recovery_code); }, onError: (e) => setMsg(errText(e, "Not done.")) });
  const erase = useMutation({ mutationFn: () => pk.eraseSpace(erasePass, eraseHistory, csrf), onSuccess: () => { setErasePass(""); refresh(); }, onError: (e) => setMsg(errText(e, "Not erased.")) });
  const exportM = useMutation({
    mutationFn: pk.downloadExport,
    onSuccess: (blob) => { const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = "personal-knowledge.zip"; a.click(); URL.revokeObjectURL(url); },
    onError: (e) => setMsg(errText(e, "Export failed.")),
  });
  return (
    <div className="space-y-4 max-w-2xl" data-testid="personal-settings">
      {msg && <p role="status" className="text-sm text-muted-foreground">{msg}</p>}
      <Card><CardContent className="p-4 flex items-center justify-between gap-3 text-sm">
        <span>Learn automatically from my private chats</span>
        <Switch checked={Boolean(status.auto_capture)} onCheckedChange={(c: boolean) => auto.mutate(c)} disabled={!csrf} aria-label="Learn automatically" />
      </CardContent></Card>
      <Card><CardContent className="p-4 space-y-2 text-sm">
        <h3 className="font-medium">Chat accounts</h3>
        {status.mode === "strict" ? <p className="text-muted-foreground">This space is console only, so no chat account can be linked.</p> : <>
          {(status.bindings ?? []).length === 0 ? <p className="text-muted-foreground">No chat account is linked.</p> : (
            <ul className="space-y-1">{(status.bindings ?? []).map((b, i) => (
              <li key={`${b.channel}-${i}`} className="flex items-center gap-2"><Badge variant="outline">{b.channel}</Badge>{b.display}
                <Button size="sm" variant="ghost" onClick={() => unbind.mutate(i)} disabled={!csrf}>Unlink</Button></li>))}</ul>
          )}
          {code ? (
            <div className="rounded-md border border-border p-3 space-y-2">
              <p>Send <span className="font-mono">/personal link {code}</span> to Corvin in a private chat within 10 minutes.</p>
              {(pending.data?.pending ?? []).map((p) => (
                <div key={p.pair_id} className="flex items-center gap-2">
                  <span>{p.channel} · {p.display || "unnamed"} · {p.masked}</span>
                  <Button size="sm" variant="accent" disabled={!csrf} onClick={() => confirm.mutate(p.pair_id)}>Confirm, this is me</Button>
                </div>
              ))}
            </div>
          ) : <Button size="sm" variant="outline" disabled={!csrf} onClick={() => pair.mutate()}>Link a chat account</Button>}
        </>}
      </CardContent></Card>
      <Card><CardContent className="p-4 space-y-2 text-sm">
        <h3 className="font-medium">Export</h3>
        <p className="text-muted-foreground">Downloads every entry as Markdown. The downloaded file is not encrypted; keep it somewhere safe.</p>
        <Button size="sm" variant="outline" disabled={exportM.isPending} onClick={() => exportM.mutate()}>Download everything</Button>
      </CardContent></Card>
      {status.has_passphrase && <Card><CardContent className="p-4 space-y-2 text-sm">
        <h3 className="font-medium">Change passphrase</h3>
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); passM.mutate(); }}>
          <Input type="password" aria-label="Current passphrase" placeholder="Current passphrase" value={oldP} onChange={(e) => setOldP(e.target.value)} className="max-w-xs" />
          <Input type="password" aria-label="New passphrase" placeholder="New passphrase" value={newP} onChange={(e) => setNewP(e.target.value)} className="max-w-xs" />
          <Button size="sm" variant="outline" type="submit" disabled={!csrf || !oldP || !newP || passM.isPending}>Change</Button>
        </form>
      </CardContent></Card>}
      {status.has_passphrase && <Card><CardContent className="p-4 space-y-2 text-sm">
        <h3 className="font-medium">Renew the keys</h3>
        <p className="text-muted-foreground">Use this if you think your recovery code or a key file was seen by someone else. You get a new recovery code.</p>
        {newCode ? <><pre className="rounded-md bg-muted px-3 py-2 font-mono break-all whitespace-pre-wrap">{newCode}</pre><Button size="sm" variant="outline" onClick={() => setNewCode(null)}>I saved the new code</Button></> : (
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); rekey.mutate(); }}>
            <Input type="password" aria-label="Passphrase for renewal" placeholder="Passphrase" value={rekeyPass} onChange={(e) => setRekeyPass(e.target.value)} className="max-w-xs" />
            <Button size="sm" variant="outline" type="submit" disabled={!csrf || !rekeyPass || rekey.isPending}>Renew keys</Button>
          </form>
        )}
      </CardContent></Card>}
      <Card className="border-destructive/40"><CardContent className="p-4 space-y-2 text-sm">
        <h3 className="font-medium">Erase my space</h3>
        <p className="text-muted-foreground">Deletes every entry and the keys for good. It cannot be undone.</p>
        <label className="flex items-start gap-2"><input type="checkbox" checked={eraseHistory} onChange={(e) => setEraseHistory(e.target.checked)} />
          <span>Also delete my chat history for linked chat accounts. That erasure request is recorded with the chat account id, as every erasure request is.</span></label>
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (window.confirm("Erase your personal space for good?")) erase.mutate(); }}>
          {status.has_passphrase && <Input type="password" aria-label="Passphrase to erase" placeholder="Passphrase" value={erasePass} onChange={(e) => setErasePass(e.target.value)} className="max-w-xs" />}
          <Button size="sm" variant="destructive" type="submit" disabled={!csrf || (status.has_passphrase && !erasePass) || erase.isPending}>Erase</Button>
        </form>
      </CardContent></Card>
    </div>
  );
}

export default PersonalSpace;
