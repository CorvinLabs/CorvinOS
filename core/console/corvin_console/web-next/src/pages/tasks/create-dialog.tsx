/** Create a work item — the parent picker offers only parents the hierarchy rules allow. */
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { Item, ItemCreateBody, ItemKind, Priority } from "@/lib/api/task-tracking";
import { KIND_META, KIND_ORDER, PARENT_RULES, PRIORITY_META, PRIORITY_ORDER } from "./encodings";

const inputCls = "h-9 w-full rounded-md border bg-background px-2 text-sm";

export function CreateDialog({ open, onOpenChange, items, defaultParent, busy, error, onCreate }: {
  open: boolean; onOpenChange: (v: boolean) => void; items: Item[]; defaultParent: Item | null;
  busy: boolean; error: string | null; onCreate: (body: ItemCreateBody) => void;
}) {
  const initialKind = (p: Item | null): ItemKind =>
    !p ? "initiative" : p.kind === "initiative" ? "epic" : p.kind === "task" || p.kind === "issue" || p.kind === "proposal" ? "subtask" : "task";
  const [kind, setKind] = useState<ItemKind>(initialKind(defaultParent));
  const [parent, setParent] = useState<string>(defaultParent?.id ?? "");
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState<Priority>("medium");
  const [deadline, setDeadline] = useState("");
  const [assignee, setAssignee] = useState("");
  const [description, setDescription] = useState("");

  useEffect(() => {
    if (open) {
      setKind(initialKind(defaultParent));
      setParent(defaultParent?.id ?? "");
      setTitle(""); setPriority("medium"); setDeadline(""); setAssignee(""); setDescription("");
    }
  }, [open, defaultParent]);

  const allowed = PARENT_RULES[kind];
  const parents = useMemo(() => items.filter((i) => !i.deleted_at && allowed.includes(i.kind)), [items, allowed]);
  // Switching the kind can make the chosen parent invalid — drop it rather than
  // keep an id the select cannot show (which left Create stuck disabled).
  useEffect(() => {
    if (parent && !parents.some((p) => p.id === parent)) setParent("");
  }, [parent, parents]);
  const parentOk = parent ? parents.some((p) => p.id === parent) : allowed.includes(null);
  const article = /^[aeiou]/i.test(KIND_META[kind].label) ? "An" : "A";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New item</DialogTitle>
          <DialogDescription>Saved to the task store and recorded in the audit log.</DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={(e) => {
          e.preventDefault();
          if (!title.trim() || !parentOk) return;
          onCreate({
            kind, title: title.trim(), parent_id: parent || null, priority,
            deadline: deadline || null, assignee: assignee.trim() || null, description: description.trim() || null,
          });
        }}>
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-xs text-muted-foreground">Kind
              <select aria-label="Kind" className={inputCls} value={kind} onChange={(e) => setKind(e.target.value as ItemKind)}>
                {KIND_ORDER.map((k) => <option key={k} value={k}>{KIND_META[k].label}</option>)}
              </select>
            </label>
            <label className="space-y-1 text-xs text-muted-foreground">Parent
              <select aria-label="Parent" className={inputCls} value={parent} onChange={(e) => setParent(e.target.value)}>
                {allowed.includes(null) ? <option value="">None (top level)</option> : <option value="">Choose a parent…</option>}
                {parents.map((p) => <option key={p.id} value={p.id}>{KIND_META[p.kind].label}: {p.title}</option>)}
              </select>
            </label>
          </div>
          <label className="block space-y-1 text-xs text-muted-foreground">Title
            <input aria-label="New item title" className={inputCls} value={title} maxLength={200} autoFocus
              onChange={(e) => setTitle(e.target.value)} />
          </label>
          <div className="grid grid-cols-3 gap-3">
            <label className="space-y-1 text-xs text-muted-foreground">Priority
              <select aria-label="New item priority" className={inputCls} value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
                {PRIORITY_ORDER.map((p) => <option key={p} value={p}>{PRIORITY_META[p].label}</option>)}
              </select>
            </label>
            <label className="space-y-1 text-xs text-muted-foreground">Deadline
              <input aria-label="New item deadline" type="date" className={inputCls} value={deadline} onChange={(e) => setDeadline(e.target.value)} />
            </label>
            <label className="space-y-1 text-xs text-muted-foreground">Assignee
              <input aria-label="New item assignee" className={inputCls} value={assignee} maxLength={48} placeholder="optional"
                onChange={(e) => setAssignee(e.target.value)} />
            </label>
          </div>
          <label className="block space-y-1 text-xs text-muted-foreground">Description
            <textarea aria-label="New item description" rows={3} className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
              value={description} maxLength={8000} onChange={(e) => setDescription(e.target.value)} />
          </label>
          {!parentOk && <p className="text-xs text-destructive">{article} {KIND_META[kind].label.toLowerCase()} needs a parent.</p>}
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || !title.trim() || !parentOk}>Create</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
