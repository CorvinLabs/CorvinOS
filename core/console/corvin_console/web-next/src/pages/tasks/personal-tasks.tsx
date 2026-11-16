/**
 * Tasks → "Only for me": the owner's personal tasks from the encrypted personal space. They never
 * enter the shared task store; this list exists only in the response to the unlocked owner.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import * as pk from "@/lib/api/personal-kb";

interface PersonalItem { id: string; title: string; status: string; status_reason: string | null; display_id?: string; dod?: string | null; description?: string; idea_uid?: string | null }

/** Why a blocked or cancelled plan task failed: the decoder goes back to the step where the problem began. */
const REVISIT_LABEL: Record<string, string> = {
  unclear_goal: "The goal was unclear", missing_decision: "A decision was missing",
  missing_step: "A step was missing", too_big: "The task was too big", external: "Something outside the plan",
};
interface PersonalItems { items: PersonalItem[]; unlocked: boolean }

/** Board status -> the personal task state (same rules as the project knowledge base). */
const TO_STATE: Record<string, string> = { open: "open", in_progress: "in_progress", blocked: "blocked", complete: "done", archived: "cancelled" };
const NEXT: Record<string, { to: string; label: string; needs?: "reason" | "dod" }[]> = {
  open: [{ to: "in_progress", label: "Start" }, { to: "cancelled", label: "Cancel", needs: "reason" }],
  in_progress: [{ to: "done", label: "Done", needs: "dod" }, { to: "blocked", label: "Blocked", needs: "reason" }, { to: "open", label: "Back to open" }],
  blocked: [{ to: "in_progress", label: "Resume" }, { to: "cancelled", label: "Cancel", needs: "reason" }],
  done: [], cancelled: [],
};
const COLUMNS = ["open", "in_progress", "blocked", "done", "cancelled"] as const;
const COLUMN_LABEL: Record<string, string> = { open: "Open", in_progress: "In progress", blocked: "Blocked", done: "Done", cancelled: "Cancelled" };

export function PersonalTasks() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["personal-kb", "tasks"],
    queryFn: ({ signal }) => api<PersonalItems>("/task-tracking/items?scope=personal", { signal }), retry: false });
  const [input, setInput] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const move = useMutation({
    mutationFn: ({ item, to, extra }: { item: PersonalItem; to: string; extra: { reason?: string; dod?: string } }) =>
      pk.transitionTask(item.id.replace(/^pkb:/, ""), to, csrf, extra),
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey: ["personal-kb"] }); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Not changed."),
  });
  const [why, setWhy] = useState<Record<string, string>>({});
  const revisit = useMutation({
    mutationFn: ({ item, category }: { item: PersonalItem; category: string }) =>
      pk.revisitTask(item.idea_uid as string, item.id.replace(/^pkb:/, ""), category, csrf),
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey: ["personal-kb"] }); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "The idea could not be reworked."),
  });
  const add = useMutation({
    mutationFn: () => pk.createEntity({ type: "task", title }, csrf),
    onSuccess: () => { setTitle(""); qc.invalidateQueries({ queryKey: ["personal-kb"] }); },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Not saved."),
  });
  if (q.isPending) return <div className="py-16 flex justify-center"><Loader2 className="w-8 h-8 animate-spin text-muted-foreground" /></div>;
  if (q.isError) {
    const missing = q.error instanceof ApiError && q.error.status === 404;
    return <Card><CardContent className="py-6 text-sm text-muted-foreground">{missing ? "Personal tasks are not available on this build." : "Your personal tasks could not be loaded."}</CardContent></Card>;
  }
  if (!q.data.unlocked) {
    return (
      <Card data-testid="personal-tasks-locked"><CardContent className="py-6 text-sm text-muted-foreground space-y-2">
        <p>Your personal space is locked. Open it to see your own tasks.</p>
        <Link className="text-accent underline" to="/app/corvin-knowledge?space=personal">Open my personal space</Link>
      </CardContent></Card>
    );
  }
  const items = q.data.items;
  return (
    <div className="space-y-4" data-testid="personal-tasks">
      <form className="flex flex-wrap gap-2 max-w-xl" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <Input aria-label="New personal task" placeholder="New personal task" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        <Button type="submit" size="sm" variant="accent" disabled={!csrf || !title.trim() || add.isPending}>Add</Button>
      </form>
      {err && <p role="alert" className="text-xs text-destructive">{err}</p>}
      {items.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground border border-dashed border-border rounded-lg">No personal tasks yet. Add one above, or develop an idea into a plan.</div>
      ) : (
        <div className="grid gap-3 md:grid-cols-5">
          {COLUMNS.map((col) => {
            const colItems = items.filter((i) => TO_STATE[i.status] === col);
            return (
              <div key={col} className="space-y-2">
                <h3 className="text-xs font-medium text-muted-foreground">{COLUMN_LABEL[col]} <span className="font-mono">{colItems.length}</span></h3>
                {colItems.map((i) => (
                  <Card key={i.id}><CardContent className="p-3 space-y-2 text-sm">
                    <div className="flex items-start gap-2"><span className="font-mono text-xs text-muted-foreground">{i.display_id}</span><span>{i.title}</span></div>
                    {i.dod && <p className="text-xs text-muted-foreground">Done when: {i.dod}</p>}
                    {i.status_reason && <Badge variant="warn">{i.status_reason}</Badge>}
                    <div className="flex flex-wrap gap-1">
                      {NEXT[col].map((n) => (
                        <Button key={n.to} size="sm" variant="outline" disabled={!csrf || move.isPending || (!!n.needs && !(input[`${i.id}:${n.to}`] ?? "").trim() && !(n.needs === "dod" && i.dod))}
                          onClick={() => move.mutate({ item: i, to: n.to, extra: n.needs === "reason" ? { reason: input[`${i.id}:${n.to}`] } : n.needs === "dod" ? { dod: input[`${i.id}:${n.to}`] || i.dod || undefined } : {} })}>
                          {n.label}
                        </Button>
                      ))}
                    </div>
                    {(col === "blocked" || col === "cancelled") && i.idea_uid && (
                      <div className="flex flex-wrap items-center gap-1" data-testid="personal-task-revisit">
                        <select aria-label="Why it did not work" className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                          value={why[i.id] ?? ""} onChange={(e) => setWhy({ ...why, [i.id]: e.target.value })}>
                          <option value="">Why did it not work?</option>
                          {Object.entries(REVISIT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                        </select>
                        <Button size="sm" variant="outline" disabled={!csrf || revisit.isPending || !why[i.id]}
                          onClick={() => revisit.mutate({ item: i, category: why[i.id] })}>Rework the idea</Button>
                      </div>
                    )}
                    {NEXT[col].filter((n) => n.needs).map((n) => (
                      <Input key={n.to} className="h-8" aria-label={`${n.label}: ${n.needs === "dod" ? "what done means" : "reason"}`}
                        placeholder={n.needs === "dod" ? (i.dod ? "Done when (optional)" : "Done when …") : `Reason to ${n.label.toLowerCase()}`}
                        value={input[`${i.id}:${n.to}`] ?? ""} onChange={(e) => setInput({ ...input, [`${i.id}:${n.to}`]: e.target.value })} />
                    ))}
                  </CardContent></Card>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
