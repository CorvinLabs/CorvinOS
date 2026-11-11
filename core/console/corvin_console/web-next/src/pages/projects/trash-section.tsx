/**
 * The Trash of the Projects page (T-0405, ADR-2321 Decision 12): the managed projects whose files were deleted, kept for 30 days.
 * Restore brings a folder back to the Corvin workspace; "Delete permanently…" needs the typed id. Rows, dates and refusals are the
 * server's (GET /projects/-/trash); the row logic is in inspector-logic.ts (unit-tested). The server pages the Trash (200 per page,
 * newest first); "Older entries" / "Newer entries" page with it (REVIEW-0063 m6).
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { listTrash, purgeTrashEntry, restoreTrashEntry } from "@/lib/api/projects";
import { PURGE_SENTENCE, TRASH_DAYS, confirmMatches, trashPager, trashRows, type TrashRow } from "./inspector-logic";
import { explain } from "./wizard";

export const TRASH_KEY = ["projects", "trash"] as const;
const LIST = ["projects", "list"] as const;

export function TrashSection({ onBack }: { onBack: () => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const [offset, setOffset] = useState(0);
  const trash = useQuery({ queryKey: [...TRASH_KEY, offset], queryFn: ({ signal }) => listTrash(signal, offset), refetchInterval: 30_000,
    placeholderData: (prev) => prev });                         // paging keeps the rows on screen until the next page arrives
  const rows = trashRows(trash.data?.entries);
  const pager = trashPager(trash.data);
  useEffect(() => {                                             // the page emptied (restored/deleted rows): show the newer one
    if (pager.stepBack !== undefined && !trash.isPlaceholderData) setOffset(pager.stepBack);
  }, [pager.stepBack, trash.isPlaceholderData]);
  const [note, setNote] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ entry: string; text: string } | null>(null);
  const [purging, setPurging] = useState<TrashRow | null>(null);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: TRASH_KEY });
    void qc.invalidateQueries({ queryKey: LIST });
  };
  const restore = useMutation({
    mutationFn: (r: TrashRow) => restoreTrashEntry(r.entry, csrf),
    onMutate: () => { setNote(null); setRowError(null); },
    onSuccess: (_d, r) => { setNote(`${r.title} was restored to the Corvin workspace.`); refresh(); },
    onError: (e, r) => setRowError({ entry: r.entry, text: explain(e) }),
  });
  const loadError = trash.error instanceof ApiError ? trash.error.message : trash.error ? "Could not load the Trash." : null;
  return (
    <section className="space-y-3" aria-label="Trash" data-testid="trash-section">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">Trash</h2>
          <p className="text-sm text-muted-foreground">
            Projects from the Corvin workspace whose files you deleted. Each one can be restored for {TRASH_DAYS} days, then it is removed for good.
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={onBack} data-testid="trash-back">Back to projects</Button>
      </div>
      {note && <p role="status" className="text-sm" data-testid="trash-note">{note}</p>}
      {loadError && (
        <div role="alert" className="rounded-lg border border-red-500/40 p-3 text-sm text-red-700 dark:text-red-300" data-testid="trash-error">{loadError}</div>
      )}
      {trash.isLoading && <Skeleton className="h-20 w-full" data-testid="trash-loading" />}
      {trash.data && rows.length === 0 && pager.stepBack === undefined && (
        <Card data-testid="trash-empty"><CardContent className="p-6 text-center text-sm text-muted-foreground">Trash is empty.</CardContent></Card>
      )}
      {rows.length > 0 && (
        <ul className="divide-y rounded-lg border" data-testid="trash-list">
          {rows.map((r) => (
            <li key={r.entry} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between" data-testid="trash-row" data-entry={r.entry}
              data-project-id={r.id}>
              <div className="min-w-0 space-y-0.5">
                <p className="truncate text-sm font-medium" title={r.title} data-testid="trash-title">{r.title}</p>
                <p className="font-mono text-xs text-muted-foreground" data-testid="trash-id">{r.id}</p>
                <p className="text-xs text-muted-foreground">
                  Deleted <span data-testid="trash-deleted">{r.deleted}</span> · restorable until <span data-testid="trash-until">{r.until}</span>
                </p>
                {r.note && <p className="text-xs text-muted-foreground">{r.note}</p>}
                {rowError?.entry === r.entry && <p role="alert" className="text-xs text-red-700 dark:text-red-300" data-testid="trash-row-error">{rowError.text}</p>}
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button size="sm" onClick={() => restore.mutate(r)} disabled={!r.canAct || restore.isPending} data-testid="trash-restore">
                  {restore.isPending && restore.variables?.entry === r.entry ? "Restoring…" : "Restore"}
                </Button>
                <Button size="sm" variant="outline" className="border-red-500/40 text-red-700 hover:bg-red-500/10 dark:text-red-300"
                  onClick={() => { setNote(null); setPurging(r); }} disabled={!r.canAct} data-testid="trash-purge">Delete permanently…</Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {(pager.newer !== null || pager.older !== null) && (
        <nav className="flex flex-wrap items-center justify-between gap-2" aria-label="Trash pages" data-testid="trash-pager">
          <p className="text-xs text-muted-foreground" data-testid="trash-range">{pager.label}</p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={pager.newer === null || trash.isPlaceholderData}
              onClick={() => { if (pager.newer !== null) { setRowError(null); setOffset(pager.newer); } }} data-testid="trash-newer">Newer entries</Button>
            <Button size="sm" variant="outline" disabled={pager.older === null || trash.isPlaceholderData}
              onClick={() => { if (pager.older !== null) { setRowError(null); setOffset(pager.older); } }} data-testid="trash-older">Older entries</Button>
          </div>
        </nav>
      )}
      <PurgeDialog row={purging} csrf={csrf} onClose={() => setPurging(null)}
        onDone={(r) => { setPurging(null); setNote(`${r.title} was deleted permanently.`); refresh(); }} />
    </section>
  );
}

function PurgeDialog({ row, csrf, onClose, onDone }: { row: TrashRow | null; csrf: string; onClose: () => void; onDone: (r: TrashRow) => void }) {
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: (r: TrashRow) => purgeTrashEntry(r.entry, typed, csrf),
    onMutate: () => setError(null),
    onSuccess: (_d, r) => { setTyped(""); onDone(r); },
    onError: (e) => setError(explain(e)),
  });
  const ok = row !== null && confirmMatches(typed, row.id);
  const close = (o: boolean) => { if (mut.isPending || o) return; setTyped(""); setError(null); onClose(); };
  return (
    <Dialog open={row !== null} onOpenChange={close}>
      {row && (
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md" data-testid="purge-dialog">
          <DialogHeader>
            <DialogTitle className="break-words pr-6">Delete "{row.title}" permanently?</DialogTitle>
            <DialogDescription data-testid="purge-sentence">{PURGE_SENTENCE}</DialogDescription>
          </DialogHeader>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (ok && !mut.isPending) mut.mutate(row); }}>
            <label className="block text-sm">Type the project id <span className="font-mono font-semibold">{row.id}</span> to confirm
              <input className="mt-1 w-full rounded border bg-background px-2 py-1 font-mono text-sm" value={typed} maxLength={28} spellCheck={false}
                autoComplete="off" autoFocus onChange={(e) => { setTyped(e.target.value); setError(null); }} disabled={mut.isPending}
                aria-label="Project id" data-testid="purge-confirm" />
            </label>
            {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="purge-error">{error}</p>}
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" size="sm" variant="ghost" onClick={() => close(false)} disabled={mut.isPending} data-testid="purge-cancel">Cancel</Button>
              <Button type="submit" size="sm" variant="destructive" disabled={!ok || mut.isPending} data-testid="purge-submit">
                {mut.isPending ? "Deleting…" : "Delete permanently"}
              </Button>
            </div>
          </form>
        </DialogContent>
      )}
    </Dialog>
  );
}
