/**
 * Detail drawer — one work item with everything the SSOT knows about it:
 * editable fields (optimistic `version`, 409 → reload), the approval decision,
 * evidence, children, dependencies, linked runs and the audited history.
 */
import { useEffect, useId, useRef, useState, type InputHTMLAttributes, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Loader2, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import {
  addTaskDependency, decideTaskItem, deleteTaskItem, getTaskItem, patchTaskItem, removeTaskDependency,
  restoreTaskItem, unlinkTaskRun, type HistoryEntry, type Item, type ItemDetail, type ItemPatchBody,
} from "@/lib/api/task-tracking";
import { cn } from "@/lib/utils";
import { KIND_META, PRIORITY_ORDER, PRIORITY_META, STATUS_META, STATUS_ORDER, displayProgress } from "./encodings";
import { evidenceText, formatAgo, formatUtc } from "./format";
import { LIVE_QUERY } from "./live";
import { ApprovalTag, Deadline, EvidenceBadge, KindTag, ProgressBar, StatusBadge, StatusIcon } from "./parts";

const inputCls = "h-8 w-full rounded-md border bg-background px-2 text-sm disabled:opacity-60";

function Section({ title, children, count }: { title: string; children: ReactNode; count?: number }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}{count !== undefined && <span className="ml-1 font-normal tabular-nums">({count})</span>}
      </h3>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block space-y-1 text-xs text-muted-foreground"><span>{label}</span>{children}</label>;
}

/**
 * An input that owns its draft while focused: a poll that brings a new server
 * value never overwrites what the operator is typing, and the field is never
 * disabled by another field's save (no lost focus, no swallowed edits).
 * `commit` receives the draft on blur/Enter and decides whether it is a change.
 */
function EditField({ value, commit, multiline, className, ...rest }: {
  value: string; commit: (draft: string) => void; multiline?: boolean; className?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onBlur">) {
  const [draft, setDraft] = useState(value);
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(value); }, [value]);
  const done = () => { focused.current = false; commit(draft); };
  const common = {
    value: draft,
    onFocus: () => { focused.current = true; },
    onBlur: done,
    className,
  };
  if (multiline) {
    return <textarea {...common} rows={4} aria-label={rest["aria-label"]} disabled={rest.disabled} maxLength={rest.maxLength}
      onChange={(e) => setDraft(e.target.value)} />;
  }
  return <input {...rest} {...common} onChange={(e) => setDraft(e.target.value)}
    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />;
}

/**
 * A date field. Blur saves a CHANGED date only; an emptied or half-typed input
 * never clears a deadline by itself — clearing is the explicit × button. The
 * stored value may be a full timestamp; it is only rewritten when the day changes.
 */
function DateField({ label, value, disabled, onSave }: {
  label: string; value: string | null; disabled: boolean; onSave: (v: string | null) => void;
}) {
  const day = value?.slice(0, 10) ?? "";
  const fid = useId();
  return (
    <div className="space-y-1 text-xs text-muted-foreground">
      <label htmlFor={fid}>{label}</label>
      <div className="flex gap-1">
        <EditField id={fid} aria-label={label} type="date" className={inputCls} value={day} disabled={disabled}
          commit={(v) => { if (v && v !== day) onSave(v); }} />
        {value && !disabled && (
          <Button type="button" size="icon" variant="ghost" className="h-8 w-8 shrink-0" aria-label={`Clear ${label.toLowerCase()}`}
            onClick={() => onSave(null)}><X className="h-3.5 w-3.5" /></Button>
        )}
      </div>
    </div>
  );
}

const num = (raw: string, max = 100000): number | null => {
  if (raw.trim() === "") return null;
  const v = Number(raw);
  return Number.isFinite(v) ? Math.max(0, Math.min(max, v)) : null;
};
const sameText = (a: string | null | undefined, b: string | null | undefined) => (a ?? "").trim() === (b ?? "").trim();

const EVENT_LABEL: Record<string, string> = {
  "task_item.created": "Created", "task_item.updated": "Updated", "task_item.deleted": "Deleted",
  "task_item.restored": "Restored", "task_item.decision_recorded": "Decision recorded",
  "task_item.dependency_added": "Dependency added", "task_item.dependency_removed": "Dependency removed",
  "task_item.run_linked": "Run linked", "task_item.run_unlinked": "Run unlinked", "task_item.imported": "Imported batch",
};

function deltaText(h: HistoryEntry): string {
  const d = h.delta ?? {};
  if (h.event_type === "task_item.updated") {
    return Object.entries(d).map(([k, v]) => {
      const [a, b] = Array.isArray(v) ? v : [undefined, v];
      const long = typeof b === "string" && b.length > 40;
      return long || k === "description" ? `${k} changed` : `${k}: ${a ?? "—"} → ${b ?? "—"}`;
    }).join(" · ");
  }
  if (typeof d.imported_from === "string") return "from initiatives.json";
  if (d.approval_state) return `${(d.approval_state as unknown[]).join(" → ")}`;
  if (typeof d.cascade_count === "number" && d.cascade_count > 0) return `${d.cascade_count} children too`;
  if (typeof d.run_linked === "string") return d.run_linked;
  if (typeof d.run_unlinked === "string") return d.run_unlinked;
  return "";
}

export function DetailDrawer({ id, csrf, items, onClose, onSelect, onAddChild, onLinkedChange }: {
  id: string; csrf: string; items: Item[]; onClose: () => void; onSelect: (id: string) => void;
  onAddChild: (parent: Item) => void; onLinkedChange?: () => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["task-tracking", "item", id],
    queryFn: ({ signal }) => getTaskItem(id, signal),
    ...LIVE_QUERY,  // same cadence as the list — the drawer must never lag the row it details
  });
  const [notice, setNotice] = useState<string | null>(null);
  const [depPick, setDepPick] = useState("");
  const it = q.data?.item;
  const key = ["task-tracking", "item", id];

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["task-tracking"] });
    onLinkedChange?.();
  };
  const onErr = (e: unknown) => {
    if (e instanceof ApiError && e.status === 409) setNotice("This item changed elsewhere — reloaded the current version.");
    else if (e instanceof ApiError && e.status === 503) setNotice("The audit chain is unavailable — nothing was saved.");
    else setNotice(e instanceof Error ? e.message : "Request failed");
    refresh();
  };
  // Saves run one after another and each reads the version from the cache at
  // send time; the response is written back at once. Two quick edits therefore
  // never race each other into a self-inflicted 409.
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const [pending, setPending] = useState(0);
  const applyItem = (fresh: Item) => qc.setQueryData<ItemDetail>(key, (old) => old && { ...old, item: { ...old.item, ...fresh } });
  const enqueue = (fn: () => Promise<Item | unknown>) => {
    setPending((n) => n + 1);
    queue.current = queue.current.then(async () => {
      try {
        const res = await fn();
        if (res && typeof res === "object" && "version" in (res as Item)) applyItem(res as Item);
        setNotice(null);
        refresh();
      } catch (e) {
        onErr(e);
      } finally {
        setPending((n) => n - 1);
      }
    });
  };
  const currentVersion = () => qc.getQueryData<ItemDetail>(key)?.item.version ?? it!.version;
  const patch = { mutate: (body: Omit<ItemPatchBody, "version">) =>
    enqueue(() => patchTaskItem(id, { ...body, version: currentVersion() }, csrf)) };
  const act = { mutate: (fn: () => Promise<unknown>) => enqueue(fn) };
  const busy = pending > 0;

  if (q.isError && !q.data) {
    return (
      <aside className="rounded-lg border p-4 text-sm text-destructive">
        Could not load this item. <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
      </aside>
    );
  }
  if (!q.data || !it) {
    return <aside className="flex items-center gap-2 rounded-lg border p-4 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading…</aside>;
  }
  const d = q.data;
  const deleted = Boolean(it.deleted_at);
  const leaf = it.child_ids.length === 0;
  const candidates = items.filter((x) => x.id !== it.id && !d.depends_on.some((dd) => dd.id === x.id) && !x.deleted_at);

  return (
    <aside data-testid="detail-drawer" aria-label="Item details"
      className="flex max-h-[calc(100vh-7rem)] flex-col overflow-hidden rounded-lg border bg-background lg:sticky lg:top-4">
      <header className="space-y-2 border-b p-4">
        <div className="flex items-start justify-between gap-2">
          <nav aria-label="Path" className="flex min-w-0 flex-wrap items-center gap-0.5 text-xs text-muted-foreground">
            {d.ancestors.map((a) => (
              <span key={a.id} className="inline-flex items-center">
                <button type="button" className="max-w-[10rem] truncate hover:text-foreground hover:underline" onClick={() => onSelect(a.id)}>{a.title}</button>
                <ChevronRight className="h-3 w-3" />
              </span>
            ))}
            <KindTag item={it} />
          </nav>
          <Button size="icon" variant="ghost" className="h-7 w-7 shrink-0" onClick={onClose} aria-label="Close details"><X className="h-4 w-4" /></Button>
        </div>
        <EditField aria-label="Title" value={it.title} disabled={deleted} maxLength={200}
          className="w-full rounded-md border border-transparent bg-transparent px-1 text-lg font-semibold hover:border-border focus:border-border"
          commit={(v) => { if (v.trim() && !sameText(v, it.title)) patch.mutate({ title: v.trim() }); }} />
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={it.status} />
          <Deadline item={it} now={Date.parse(d.server_time)} />
          <ApprovalTag state={it.approval_state} />
          <EvidenceBadge evidence={it.evidence} conflict={it.claim_conflict} />
          {deleted && <span className="text-xs font-medium text-destructive">Deleted {formatUtc(it.deleted_at)}</span>}
        </div>
        {q.isError && (
          <p role="status" data-testid="drawer-stale" className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">
            Could not refresh — showing the state from {formatUtc(new Date(q.dataUpdatedAt).toISOString())}. Retrying.
          </p>
        )}
        {notice && <p role="status" className="rounded-md bg-amber-500/10 px-2 py-1 text-xs text-amber-800 dark:text-amber-300">{notice}</p>}
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Status">
            <select aria-label="Status" className={inputCls} value={it.status} disabled={deleted}
              onChange={(e) => patch.mutate({ status: e.target.value as Item["status"] })}>
              {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_META[s].label}</option>)}
            </select>
          </Field>
          <Field label="Priority">
            <select aria-label="Priority" className={inputCls} value={it.priority} disabled={deleted}
              onChange={(e) => patch.mutate({ priority: e.target.value as Item["priority"] })}>
              {PRIORITY_ORDER.map((p) => <option key={p} value={p}>{PRIORITY_META[p].label}</option>)}
            </select>
          </Field>
          <Field label="Assignee">
            <EditField aria-label="Assignee" className={inputCls} value={it.assignee ?? ""} placeholder="e.g. claude"
              disabled={deleted} maxLength={48}
              commit={(v) => { if (!sameText(v, it.assignee)) patch.mutate({ assignee: v.trim() || null }); }} />
          </Field>
          <DateField label="Deadline" value={it.deadline} disabled={deleted} onSave={(v) => patch.mutate({ deadline: v })} />
          <DateField label="Start" value={it.start_at} disabled={deleted} onSave={(v) => patch.mutate({ start_at: v })} />
          <Field label={leaf ? "Progress %" : "Progress (rollup)"}>
            {leaf ? (
              <EditField aria-label="Progress" type="number" min={0} max={100} className={inputCls}
                value={it.progress == null ? "" : String(it.progress)} disabled={deleted || it.status === "complete"}
                commit={(raw) => {
                  const n = num(raw, 100);
                  const v = n === null ? null : Math.round(n);
                  if (v !== it.progress) patch.mutate({ progress: v });
                }} />
            ) : <ProgressBar value={displayProgress(it)} status={it.status} className="h-8" />}
          </Field>
          <Field label="Estimate (h)">
            <EditField aria-label="Estimate hours" type="number" min={0} step={0.5} className={inputCls}
              value={it.work_estimate == null ? "" : String(it.work_estimate)} disabled={deleted}
              commit={(raw) => { const v = num(raw); if (v !== it.work_estimate) patch.mutate({ work_estimate: v }); }} />
          </Field>
          <Field label="Logged (h)">
            <EditField aria-label="Logged hours" type="number" min={0} step={0.5} className={inputCls}
              value={it.work_actual == null ? "" : String(it.work_actual)} disabled={deleted}
              commit={(raw) => { const v = num(raw); if (v !== it.work_actual) patch.mutate({ work_actual: v }); }} />
          </Field>
        </div>

        <Field label="Description">
          <EditField multiline aria-label="Description" className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
            value={it.description ?? ""} maxLength={8000} disabled={deleted}
            commit={(v) => { if (!sameText(v, it.description)) patch.mutate({ description: v.trim() || null }); }} />
        </Field>

        {it.approval_state !== "none" && (
          <Section title="Decision">
            <div className="flex flex-wrap items-center gap-2 rounded-md border p-2">
              <ApprovalTag state={it.approval_state} />
              <div className="ml-auto flex gap-1">
                {(["approved", "rejected", "pending"] as const).map((dec) => (
                  <Button key={dec} size="sm" variant={it.approval_state === dec ? "default" : "outline"} disabled={deleted || it.approval_state === dec}
                    onClick={() => act.mutate(() => decideTaskItem(it.id, dec, currentVersion(), csrf))}>
                    {dec === "approved" ? "Go" : dec === "rejected" ? "No-go" : "Reset"}
                  </Button>
                ))}
              </div>
            </div>
          </Section>
        )}

        {it.evidence && (
          <Section title="Evidence">
            <div className="rounded-md border p-2 text-xs">
              <div>{evidenceText(it.evidence)}</div>
              {it.evidence.missing_paths.length > 0 && <div className="text-muted-foreground">missing: {it.evidence.missing_paths.join(", ")}</div>}
              <div className="text-muted-foreground">checked {formatAgo(it.evidence.age_s)}{it.evidence.stale ? " (stale)" : ""}</div>
              {it.claim_conflict && <div className="mt-1 font-medium text-destructive">Marked complete, but the evidence does not pass.</div>}
            </div>
          </Section>
        )}

        <Section title="Children" count={d.children.length}>
          {d.children.length > 0 && (
            <ul className="divide-y rounded-md border">
              {d.children.map((c) => (
                <li key={c.id}>
                  <button type="button" className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm hover:bg-muted/40" onClick={() => onSelect(c.id)}>
                    <StatusIcon status={c.status} className="h-3.5 w-3.5" />
                    <span className="flex-1 truncate">{c.title}</span>
                    {c.rollup?.progress != null && c.rollup.descendants > 0 && <span className="text-xs tabular-nums text-muted-foreground">{c.rollup.progress}%</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {!deleted && it.kind !== "subtask" && (
            <Button size="sm" variant="outline" onClick={() => onAddChild(it)}><Plus className="mr-1 h-3.5 w-3.5" />Add child</Button>
          )}
        </Section>

        <Section title="Depends on" count={d.depends_on.length}>
          <ul className="space-y-1">
            {d.depends_on.map((x) => (
              <li key={x.id} className="flex items-center gap-2 text-sm">
                <StatusIcon status={x.status} className="h-3.5 w-3.5" />
                <button type="button" className="flex-1 truncate text-left hover:underline" onClick={() => onSelect(x.id)}>{x.title}</button>
                <Button size="icon" variant="ghost" className="h-6 w-6" aria-label={`Remove dependency on ${x.title}`} disabled={busy}
                  onClick={() => act.mutate(() => removeTaskDependency(it.id, x.id, csrf))}><X className="h-3.5 w-3.5" /></Button>
              </li>
            ))}
          </ul>
          {!deleted && (
            <div className="flex gap-1">
              <select aria-label="Add dependency" className={cn(inputCls, "flex-1")} value={depPick} onChange={(e) => setDepPick(e.target.value)}>
                <option value="">Add a dependency…</option>
                {candidates.map((c) => <option key={c.id} value={c.id}>{KIND_META[c.kind].label}: {c.title}</option>)}
              </select>
              <Button size="sm" variant="outline" disabled={!depPick || busy}
                onClick={() => { const v = depPick; setDepPick(""); act.mutate(() => addTaskDependency(it.id, v, csrf)); }}>Add</Button>
            </div>
          )}
          {d.required_by.length > 0 && (
            <p className="text-xs text-muted-foreground">Required by: {d.required_by.map((x) => x.title).join(", ")}</p>
          )}
        </Section>

        <Section title="Linked runs" count={d.runs.length}>
          {d.runs.length === 0
            ? <p className="text-xs text-muted-foreground">No runs linked. Link one from the Activity view.</p>
            : (
              <ul className="space-y-1">
                {d.runs.map((r) => (
                  <li key={`${r.run_type}:${r.run_ref}`} className="flex items-center gap-2 text-xs">
                    <span className="rounded bg-muted px-1.5 py-0.5">{r.type_label ?? r.run_type}</span>
                    <span className="flex-1 truncate" title={r.run_ref}>{r.found ? r.title : `${r.run_ref} — not in the current scan`}</span>
                    {r.status && <span className="text-muted-foreground">{r.status}</span>}
                    <Button size="icon" variant="ghost" className="h-6 w-6" aria-label="Unlink run" disabled={busy}
                      onClick={() => act.mutate(() => unlinkTaskRun(it.id, r.run_type, r.run_ref, csrf))}><X className="h-3.5 w-3.5" /></Button>
                  </li>
                ))}
              </ul>
            )}
        </Section>

        <Section title="History" count={d.history.length}>
          <ol className="space-y-1.5 border-l pl-3">
            {d.history.map((h) => (
              <li key={h.event_id} className="text-xs">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{EVENT_LABEL[h.event_type] ?? h.event_type}</span>
                  <span className="text-muted-foreground">{formatUtc(h.ts)} · {h.actor}</span>
                </div>
                {deltaText(h) && <div className="text-muted-foreground">{deltaText(h)}</div>}
              </li>
            ))}
          </ol>
          <p className="text-[11px] text-muted-foreground">Every entry is also recorded in the tenant's hash-chained audit log.</p>
        </Section>
      </div>

      <footer className="flex items-center justify-between gap-2 border-t p-3 text-xs text-muted-foreground">
        <span>Updated {formatUtc(it.updated_at)} · v{it.version}</span>
        {deleted
          ? <Button size="sm" variant="outline" disabled={busy} onClick={() => act.mutate(() => restoreTaskItem(it.id, csrf))}><RotateCcw className="mr-1 h-3.5 w-3.5" />Restore</Button>
          : <Button size="sm" variant="outline" className="text-destructive" disabled={busy}
              onClick={() => {
                const n = it.rollup?.descendants ?? 0;
                if (window.confirm(n ? `Delete "${it.title}" and its ${n} children? It can be restored.` : `Delete "${it.title}"? It can be restored.`)) {
                  act.mutate(() => deleteTaskItem(it.id, csrf));
                }
              }}><Trash2 className="mr-1 h-3.5 w-3.5" />Delete</Button>}
      </footer>
    </aside>
  );
}
