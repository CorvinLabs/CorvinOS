/**
 * Share: Export (one project or a set), Import (upload -> preview with conflicts -> confirm -> result) and Duplicate. Every decision
 * lives in share-logic.ts (unit-tested); the server judges every action again (class gate, conflicts, place, secret scans). Nothing
 * here is invented: counts, left-out files, conflicts and sentences are the server's.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FolderPicker } from "@/components/folder-picker";
import { ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import {
  duplicateProject, exportJob, exportJobFileUrl, exportPreview, exportProjects, importApply, importCancel, importValidate, importVerdict,
  listProjects, type DataClass, type ExportParts, type ImportApplied, type ImportReport, type ProjectDetail,
} from "@/lib/api/projects";
import { cn } from "@/lib/utils";
import { ClassBadge, StorageBadge } from "./badges";
import { CLASS_META } from "./roles";
import { explain } from "./wizard";
import { parentOf } from "./picker-logic";
import {
  ARCHIVE_NOTE, ID_RULE, ORIGIN_HINT, ORIGIN_LABEL, PROTECTION_LATER, conflictRows, exportEnabled, exportGate, fromLine, importEnabled,
  importProtection, includedLine, initialChoices, jobLine, leftOutLines, offersKbPart, offersWithoutHistory, refusalOf, skippableParts, suggestId,
  verdictKey, willCreateLine, type ImportChoices,
} from "./share-logic";

const LIST = ["projects", "list"] as const;

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function Check({ checked, onChange, label, testid, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; testid: string; disabled?: boolean }) {
  return (
    <label className={cn("flex items-center gap-2 text-sm", disabled && "opacity-60")}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} data-testid={testid} />
      {label}
    </label>
  );
}

function Line({ label, children, testid }: { label: string; children: React.ReactNode; testid?: string }) {
  return (
    <div className="grid gap-1 text-sm sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-3" data-testid={testid}>
      <span className="text-xs text-muted-foreground sm:pt-0.5">{label}</span>
      <div className="min-w-0 break-words [overflow-wrap:anywhere]">{children}</div>
    </div>
  );
}

// ── Export ───────────────────────────────────────────────────────────────────────────────────────────────

type ExportDone = { kind: "file"; filename: string; warnings: string[] } | { kind: "job"; jobId: string; warnings: string[] };

export function ExportDialog({ open, onOpenChange, ids, label }: { open: boolean; onOpenChange: (o: boolean) => void; ids: string[]; label: string }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open && <ExportBody ids={ids} label={label} onClose={() => onOpenChange(false)} />}
    </Dialog>
  );
}

function ExportBody({ ids, label, onClose }: { ids: string[]; label: string; onClose: () => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const [parts, setParts] = useState<ExportParts>({ git_history: true, generated: false, kb: true });
  const [typed, setTyped] = useState("");
  const [pass, setPass] = useState("");
  const [error, setError] = useState<{ text: string; cls?: string } | null>(null);
  const [done, setDone] = useState<ExportDone | null>(null);
  const preview = useQuery({
    queryKey: ["projects", "export-preview", ids.join(","), parts.git_history, parts.generated, parts.kb],
    queryFn: ({ signal }) => exportPreview(ids, parts, signal),
    retry: false, staleTime: 0,
  });
  const pv = preview.data;
  const gate = exportGate(pv ? pv.projects.map((p) => ({ id: p.id, classification: p.classification })) : ids.map((id) => ({ id })), !!pv?.gpg);
  const mut = useMutation({
    mutationFn: () => exportProjects({ project_ids: ids, parts, confirm: gate.confirmWord !== null, ...(pass ? { passphrase: pass } : {}) }, csrf),
    onMutate: () => setError(null),
    onSuccess: (r) => {
      if (r.kind === "file") { saveBlob(r.blob, r.filename); setDone({ kind: "file", filename: r.filename, warnings: r.warnings }); }
      else setDone({ kind: "job", jobId: r.job_id, warnings: r.warnings });
    },
    onError: (e) => setError({ text: explain(e), cls: e instanceof ApiError ? refusalOf(e.detail).reason_class : undefined }),
  });
  const job = useQuery({
    queryKey: ["projects", "export-job", done?.kind === "job" ? done.jobId : ""],
    queryFn: ({ signal }) => exportJob((done as { jobId: string }).jobId, signal),
    enabled: done?.kind === "job",
    refetchInterval: (q) => (q.state.data?.state === "running" || !q.state.data ? 1000 : false),
  });
  const ready = !!pv && !preview.error;
  const can = exportEnabled({ gate, typed, passphrase: pass, busy: mut.isPending || !!done, ready });
  const withoutHistory = offersWithoutHistory(pv) || error?.cls === "secret_in_history";
  const single = ids.length === 1;
  return (
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl" data-testid="export-dialog">
      <DialogHeader>
        <DialogTitle className="break-words pr-6">{single ? `Export "${label}"` : `Export ${ids.length} projects`}</DialogTitle>
        <DialogDescription>One ZIP file you can import on another Corvin installation. Your project folder is not changed.</DialogDescription>
      </DialogHeader>
      {preview.isLoading && <p className="text-sm text-muted-foreground" data-testid="export-loading">Looking at what the ZIP would contain…</p>}
      {preview.error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="export-preview-error">{explain(preview.error)}</p>}
      {pv && (
        <div className="space-y-3" data-testid="export-preview">
          {pv.projects.map((p) => {
            const lo = leftOutLines(p.left_out);
            return (
              <div key={p.id} className="space-y-1.5" data-testid="export-project" data-project-id={p.id}>
                {!single && <p className="text-sm font-medium">{p.title} <span className="font-mono text-xs text-muted-foreground">{p.id}</span></p>}
                <Line label="Included" testid="export-included">{includedLine(p)}</Line>
                <Line label="Left out" testid="export-left-out">
                  {lo.length === 0 ? <span className="text-muted-foreground">nothing</span> : (
                    <ul className="space-y-0.5 font-mono text-xs">{lo.map((l) => <li key={l} data-testid="export-left-out-row">{l}</li>)}</ul>
                  )}
                </Line>
              </div>
            );
          })}
          <div className="flex flex-wrap gap-x-4 gap-y-2" role="group" aria-label="Parts">
            <Check checked={parts.git_history} onChange={(v) => { setParts({ ...parts, git_history: v }); setError(null); }} label="Git history" testid="export-part-history" disabled={!!done || mut.isPending} />
            <Check checked={parts.generated} onChange={(v) => setParts({ ...parts, generated: v })} label="Generated folders (node_modules, .venv …)" testid="export-part-generated" disabled={!!done || mut.isPending} />
            {/* only a project that has a knowledge base offers it (REVIEW-0067 #17) */}
            {offersKbPart(pv) && <Check checked={parts.kb} onChange={(v) => setParts({ ...parts, kb: v })} label="Knowledge base" testid="export-part-kb" disabled={!!done || mut.isPending} />}
          </div>
          {withoutHistory && parts.git_history && (
            <div className="space-y-1 rounded border border-amber-600/50 p-2 text-sm" data-testid="export-history-findings">
              <p>The git history contains files that look like secrets, so an export with history would be refused.</p>
              <Button size="sm" variant="outline" onClick={() => { setParts({ ...parts, git_history: false }); setError(null); }} data-testid="export-without-history">
                Export without git history
              </Button>
            </div>
          )}
          {pv.warnings.map((w) => <p key={w} className="text-xs text-muted-foreground" data-testid="export-warning">{w}</p>)}
          <p className={cn("text-sm", gate.refused && "text-red-700 dark:text-red-300")} data-testid="export-class-line">{gate.classLine}</p>
          {gate.refused && <p className="text-sm font-medium text-red-700 dark:text-red-300" data-testid="export-refused">{gate.refused}</p>}
          {gate.confirmWord !== null && !done && (
            <label className="block text-sm">Type <span className="font-mono">{gate.confirmWord}</span> to confirm
              <input className="mt-1 w-full rounded border bg-background px-2 py-1 font-mono text-sm" value={typed} onChange={(e) => setTyped(e.target.value)}
                autoComplete="off" spellCheck={false} data-testid="export-confirm" disabled={mut.isPending} />
            </label>
          )}
          {!gate.refused && !done && (gate.passphrase ? (
            <label className="block text-sm">Passphrase (optional, 8 or more characters)
              <input type="password" className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm" value={pass} onChange={(e) => setPass(e.target.value)}
                autoComplete="new-password" maxLength={128} data-testid="export-passphrase" disabled={mut.isPending} />
              <span className="mt-1 block text-xs text-muted-foreground">With a passphrase the file is encrypted (.zip.gpg); keep the passphrase, it cannot be recovered.</span>
            </label>
          ) : gate.passphraseNote && <p className="text-xs text-muted-foreground" data-testid="export-gpg-note">{gate.passphraseNote}</p>)}
          {(pv.notes.length ? pv.notes : [ARCHIVE_NOTE]).map((n) => <p key={n} className="text-xs text-muted-foreground" data-testid="export-note">{n}</p>)}
        </div>
      )}
      {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="export-error">{error.text}</p>}
      {mut.isPending && <p role="status" className="text-sm" data-testid="export-busy">Building the ZIP… a large project takes a while.</p>}
      {done?.kind === "file" && (
        <p role="status" className="break-words text-sm" data-testid="export-done">Downloaded <span className="font-mono text-xs">{done.filename}</span>. Check your browser's downloads.</p>
      )}
      {done?.kind === "job" && (
        <div className="space-y-2" data-testid="export-job" data-state={job.data?.state ?? "running"}>
          <p role="status" className={cn("text-sm", job.data?.state === "error" && "text-red-700 dark:text-red-300")} data-testid="export-job-line">
            {job.error ? explain(job.error) : jobLine(job.data)}
          </p>
          {job.data?.state === "done" && (
            <Button asChild size="sm" data-testid="export-download">
              <a href={exportJobFileUrl(done.jobId)} download>Download</a>
            </Button>
          )}
          {job.data?.state === "done" && <p className="text-xs text-muted-foreground">The file is kept for 24 hours.</p>}
        </div>
      )}
      {done && [...done.warnings, ...(job.data?.warnings ?? [])].map((w) => <p key={w} className="text-xs text-muted-foreground" data-testid="export-result-warning">{w}</p>)}
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onClose} data-testid="export-cancel">{done ? "Close" : "Cancel"}</Button>
        {!done && (
          <Button size="sm" onClick={() => mut.mutate()} disabled={!can} data-testid="export-submit">
            {mut.isPending ? "Exporting…" : "Export ZIP"}
          </Button>
        )}
      </div>
    </DialogContent>
  );
}

// ── Import ───────────────────────────────────────────────────────────────────────────────────────────────

export function ImportDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [busy, setBusy] = useState(false);
  const cancelRef = useRef<(() => void) | null>(null);
  return (
    <Dialog open={open} onOpenChange={(o) => { if (o) onOpenChange(true); else if (!busy) { cancelRef.current?.(); onOpenChange(false); } }}>
      {open && <ImportBody onClose={() => onOpenChange(false)} setBusy={setBusy} cancelRef={cancelRef} />}
    </Dialog>
  );
}

type Stage = { s: "choose" } | { s: "upload"; file: File } | { s: "preview"; file: File; report: ImportReport } | { s: "done"; result: ImportApplied };

function ImportBody({ onClose, setBusy, cancelRef }: { onClose: () => void; setBusy: (b: boolean) => void; cancelRef: React.MutableRefObject<(() => void) | null> }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const [stage, setStage] = useState<Stage>({ s: "choose" });
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: (file: File) => importValidate(file, csrf),
    onMutate: (file) => { setError(null); setStage({ s: "upload", file }); },
    onSuccess: (report, file) => setStage({ s: "preview", file, report }),
    onError: (e) => { setError(explain(e)); setStage({ s: "choose" }); },
  });
  const stagingId = stage.s === "preview" ? stage.report.staging_id : null;
  // closing the dialog while a file is staged removes it (Cancel)
  useEffect(() => {
    cancelRef.current = stagingId ? () => { void importCancel(stagingId, csrf).catch(() => undefined); } : null;
    return () => { cancelRef.current = null; };
  }, [stagingId, csrf, cancelRef]);
  useEffect(() => { setBusy(upload.isPending); }, [upload.isPending, setBusy]);
  const cancel = () => { cancelRef.current?.(); cancelRef.current = null; onClose(); };
  const finished = (r: ImportApplied) => {
    cancelRef.current = null;
    setStage({ s: "done", result: r });
    void qc.invalidateQueries({ queryKey: LIST });
  };
  return (
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl" data-testid="import-dialog" data-stage={stage.s}>
      {stage.s !== "preview" && stage.s !== "done" && (
        <>
          <DialogHeader>
            <DialogTitle>Import a project</DialogTitle>
            <DialogDescription>Choose a project bundle (.zip) exported from Corvin. It is checked before anything is created; nothing is written until you confirm.</DialogDescription>
          </DialogHeader>
          <input ref={fileRef} type="file" accept=".zip,application/zip" className="sr-only" data-testid="import-file"
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload.mutate(f); }} />
          {stage.s === "upload" ? (
            <p role="status" className="break-words text-sm" data-testid="import-checking">Uploading and checking {stage.file.name}…</p>
          ) : (
            <div className="space-y-2">
              <Button size="sm" onClick={() => fileRef.current?.click()} data-testid="import-choose">Choose a bundle file…</Button>
              <p className="text-xs text-muted-foreground">At most 500 MB. A passphrase-protected file (.zip.gpg) has to be decrypted with gpg first.</p>
            </div>
          )}
          {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="import-error">{error}</p>}
          <div className="flex justify-end">
            <Button size="sm" variant="ghost" onClick={cancel} disabled={upload.isPending} data-testid="import-cancel">Cancel</Button>
          </div>
        </>
      )}
      {stage.s === "preview" && <ImportPreview file={stage.file} report={stage.report} csrf={csrf} onCancel={cancel} onDone={finished} setBusy={setBusy} />}
      {stage.s === "done" && <ImportResult r={stage.result} onClose={onClose} />}
    </DialogContent>
  );
}

function ImportPreview({ file, report, csrf, onCancel, onDone, setBusy }: {
  file: File; report: ImportReport; csrf: string; onCancel: () => void; onDone: (r: ImportApplied) => void; setBusy: (b: boolean) => void;
}) {
  const [cardId, setCardId] = useState(report.projects[0]?.id ?? "");
  const card = report.projects.find((p) => p.id === cardId) ?? report.projects[0];
  const [choices, setChoices] = useState<ImportChoices>(() => initialChoices(card));
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [idDraft, setIdDraft] = useState(card.id);
  // the id is judged once typing pauses
  useEffect(() => {
    const t = setTimeout(() => setChoices((c) => (c.projectId === idDraft.trim() ? c : { ...c, projectId: idDraft.trim() })), 300);
    return () => clearTimeout(t);
  }, [idDraft]);
  const pick = (id: string) => {
    const c = report.projects.find((p) => p.id === id);
    if (!c) return;
    setCardId(id); setChoices(initialChoices(c)); setIdDraft(c.id); setError(null);
  };
  const initial = choices.projectId === card.id && choices.location === null;
  const key = verdictKey(choices);
  const v = useQuery({
    queryKey: ["projects", "import-verdict", report.staging_id, card.id, choices.projectId, choices.location ?? ""],
    queryFn: ({ signal }) => importVerdict(report.staging_id, { project: card.id, projectId: choices.projectId, location: choices.location }, signal),
    enabled: !initial && ID_RULE.test(choices.projectId), retry: false, staleTime: 0,
  });
  const verdict = initial ? { for: key, conflicts: card.conflicts } : v.data ? { for: key, conflicts: v.data.conflicts } : undefined;
  const place = choices.location ? { storage: "own" as const, path: choices.location } : initial ? card.default_location : (v.data?.location ?? null);
  const rows = conflictRows(verdict?.conflicts);
  const skip = skippableParts(card);
  const apply = useMutation({
    mutationFn: () => importApply(report.staging_id, {
      project: card.id, ...(choices.projectId !== card.id ? { project_id: choices.projectId } : {}),
      ...(choices.location ? { location: choices.location } : {}),
      parts: { git_history: !choices.skipHistory, kb: !choices.skipKb },
    }, csrf),
    onMutate: () => { setError(null); setBusy(true); },
    onSettled: () => setBusy(false),
    onSuccess: onDone,
    onError: (e) => { setError(explain(e)); void v.refetch(); },
  });
  // the typed id must have caught up with the judged one: a verdict for an older id never enables Import
  const enabled = importEnabled({ choices: { ...choices, projectId: idDraft.trim() }, verdict, applying: apply.isPending });
  const cls = importProtection(card.classification);
  const typedBad = !!idDraft.trim() && !ID_RULE.test(idDraft.trim());
  const storageKind = place?.storage === "managed" ? "managed" : place?.storage === "root" ? "root" : "own";
  return (
    <div className="space-y-3" data-testid="import-preview">
      <DialogHeader>
        <div className="flex flex-wrap items-center justify-between gap-2 pr-6">
          <DialogTitle className="break-words">Import "{card.title}"</DialogTitle>
          <span className="inline-flex items-center rounded-full border border-amber-600/50 px-2 py-0.5 text-[11px] text-amber-800 dark:text-amber-300"
            title={ORIGIN_HINT} data-testid="import-origin">{ORIGIN_LABEL}</span>
        </div>
        <DialogDescription>{ORIGIN_HINT}</DialogDescription>
      </DialogHeader>
      {report.projects.length > 1 && (
        <label className="block text-sm">Project in this file
          <select className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm" value={card.id} onChange={(e) => pick(e.target.value)} data-testid="import-card-select">
            {report.projects.map((p) => <option key={p.id} value={p.id}>{p.title} ({p.id})</option>)}
          </select>
        </label>
      )}
      <Line label="From" testid="import-from">{fromLine(file.name, report)}</Line>
      <Line label="Will create" testid="import-will-create">{willCreateLine(card, choices)}</Line>
      <Line label="Id here" testid="import-id-row">
        <input className="w-full max-w-xs rounded border bg-background px-2 py-1 font-mono text-sm" value={idDraft} maxLength={28}
          onChange={(e) => { setIdDraft(e.target.value); setError(null); }} aria-label="Project id" spellCheck={false} data-testid="import-id" disabled={apply.isPending} />
        {typedBad && <p className="mt-1 text-xs text-red-700 dark:text-red-300" data-testid="import-id-rule">Use lowercase letters, digits and dashes, at most 28 characters.</p>}
      </Line>
      <Line label="Where" testid="import-where">
        <span className="flex flex-wrap items-center gap-2">
          {place ? <StorageBadge kind={storageKind} /> : null}
          <span className="min-w-0 break-all font-mono text-xs" data-testid="import-where-path">{place?.path ?? "…"}</span>
          <button type="button" className="text-xs underline" onClick={() => setPicking(true)} disabled={apply.isPending} data-testid="import-change">Change…</button>
          {choices.location && (
            <button type="button" className="text-xs underline" onClick={() => setChoices({ ...choices, location: null })} disabled={apply.isPending}
              data-testid="import-default-place">Use the default place</button>
          )}
        </span>
      </Line>
      <Line label="Protection" testid="import-protection">
        <span className="flex flex-wrap items-center gap-1.5"><ClassBadge cls={cls} />
          <span data-testid="import-protection-text">({card.classification === cls ? "from the export" : `the export says ${CLASS_META[card.classification as DataClass]?.label ?? card.classification}; imports start at Internal`}) · {PROTECTION_LATER}</span>
        </span>
      </Line>
      <div className="space-y-1.5" data-testid="import-conflicts" data-count={rows.length}>
        <p className="text-xs text-muted-foreground">Conflicts</p>
        {!verdict && <p className="text-sm text-muted-foreground" data-testid="import-checking-choice">{v.error ? explain(v.error) : "Checking the id and place…"}</p>}
        {verdict && rows.length === 0 && <p className="text-sm" data-testid="import-no-conflicts">None. The project can be imported.</p>}
        <ul className="space-y-1.5">
          {rows.map((r) => (
            <li key={r.kind} className="flex flex-col gap-1.5 rounded border border-amber-600/40 p-2 text-sm sm:flex-row sm:items-center sm:justify-between"
              data-testid="import-conflict" data-kind={r.kind}>
              <span className="min-w-0 break-words">{r.message}</span>
              {r.action === "new_id" && r.suggestedId && (
                <Button size="sm" variant="outline" className="shrink-0 self-start sm:self-auto" onClick={() => { setIdDraft(r.suggestedId!); setChoices({ ...choices, projectId: r.suggestedId! }); }}
                  data-testid="import-fix-id">Use {r.suggestedId}</Button>
              )}
              {r.action === "copy" && r.suggestedId && (
                <Button size="sm" variant="outline" className="shrink-0 self-start sm:self-auto" onClick={() => { setIdDraft(r.suggestedId!); setChoices({ ...choices, projectId: r.suggestedId! }); }}
                  data-testid="import-fix-copy">Import as a copy ({r.suggestedId})</Button>
              )}
              {r.action === "choose_place" && (
                <Button size="sm" variant="outline" className="shrink-0 self-start sm:self-auto" onClick={() => setPicking(true)} data-testid="import-fix-place">Choose another place</Button>
              )}
            </li>
          ))}
        </ul>
        {rows.some((r) => r.kind === "same_source") && <p className="text-xs text-muted-foreground">Replacing the existing project is not offered: an import never overwrites files.</p>}
      </div>
      {(skip.history || skip.kb) && (
        <div className="flex flex-wrap gap-x-4 gap-y-2" role="group" aria-label="Parts">
          {skip.history && <Check checked={choices.skipHistory} onChange={(b) => setChoices({ ...choices, skipHistory: b })} label="Skip git history" testid="import-skip-history" disabled={apply.isPending} />}
          {skip.kb && <Check checked={choices.skipKb} onChange={(b) => setChoices({ ...choices, skipKb: b })} label="Skip knowledge base" testid="import-skip-kb" disabled={apply.isPending} />}
        </div>
      )}
      {card.warnings.map((w) => <p key={w} className="text-xs text-muted-foreground" data-testid="import-card-warning">{w}</p>)}
      {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="import-apply-error">{error}</p>}
      {apply.isPending && <p role="status" className="text-sm" data-testid="import-applying">Importing… the files are written, then the git history and the knowledge base are checked.</p>}
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={apply.isPending} data-testid="import-cancel">Cancel</Button>
        <Button size="sm" onClick={() => apply.mutate()} disabled={!enabled} data-testid="import-submit">{apply.isPending ? "Importing…" : "Import"}</Button>
      </div>
      <FolderPicker open={picking} purpose="import" title={card.title} initialPath={place?.path ? parentOf(place.path) : null} inputTestId="import-picker-input"
        onChoose={(p) => { setChoices({ ...choices, location: p }); setPicking(false); }} onCancel={() => setPicking(false)} />
    </div>
  );
}

function ImportResult({ r, onClose }: { r: ImportApplied; onClose: () => void }) {
  const p = r.project;
  return (
    <div className="space-y-3" data-testid="import-result">
      <DialogHeader>
        <DialogTitle className="break-words">Imported "{p.title || p.id}"</DialogTitle>
        <DialogDescription>It is in your list as <span className="font-mono">{p.id}</span>. The assistant can only read it until you change that in Settings.</DialogDescription>
      </DialogHeader>
      <Line label="Where"><span className="break-all font-mono text-xs" data-testid="import-result-path">{p.display_path || p.path}</span></Line>
      {r.warnings.length > 0 && (
        <ul className="space-y-1 text-sm" data-testid="import-result-warnings">{r.warnings.map((w) => <li key={w} data-testid="import-result-warning">{w}</li>)}</ul>
      )}
      {r.notes.length > 0 && (
        <ul className="space-y-1 text-xs text-muted-foreground" data-testid="import-result-notes">{r.notes.map((n) => <li key={n}>{n}</li>)}</ul>
      )}
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onClose} data-testid="import-close">Close</Button>
        <Button asChild size="sm"><Link to={`/app/projects/${encodeURIComponent(p.id)}`} onClick={onClose} data-testid="import-open">Open project</Link></Button>
      </div>
    </div>
  );
}

// ── Duplicate ────────────────────────────────────────────────────────────────────────────────────────────

export function DuplicateDialog({ open, onOpenChange, d }: { open: boolean; onOpenChange: (o: boolean) => void; d: ProjectDetail }) {
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!busy) onOpenChange(o); }}>
      {open && <DuplicateBody d={d} onClose={() => onOpenChange(false)} setBusy={setBusy} />}
    </Dialog>
  );
}

function DuplicateBody({ d, onClose, setBusy }: { d: ProjectDetail; onClose: () => void; setBusy: (b: boolean) => void }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const list = useQuery({ queryKey: LIST, queryFn: ({ signal }) => listProjects(signal), staleTime: 10_000 });
  const suggested = useMemo(() => (list.data ? suggestId(d.id, list.data.projects.map((p) => p.id)) : ""), [list.data, d.id]);
  const [id, setId] = useState<string | null>(null);
  const value = id ?? suggested;
  // the copy's name: told apart from the original in the list, the scope switchers and the graph (the server uses the same default)
  const [title, setTitle] = useState(`${d.title || d.id} (copy)`);
  const [location, setLocation] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [parts, setParts] = useState({ git_history: true, kb: true, generated: false });
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<ImportApplied | null>(null);
  const mut = useMutation({
    mutationFn: () => duplicateProject(d.id, { project_id: value.trim(), ...(title.trim() ? { title: title.trim() } : {}), ...(location ? { location } : {}), parts }, csrf),
    onMutate: () => { setError(null); setBusy(true); },
    onSettled: () => setBusy(false),
    onSuccess: (r) => { setDone(r); void qc.invalidateQueries({ queryKey: LIST }); },
    onError: (e) => setError(explain(e)),
  });
  const valid = ID_RULE.test(value.trim()) && value.trim() !== d.id && title.trim().length > 0;
  const defaultPlace = list.data?.default_storage === "root" && list.data.projects_root_set
    ? `Projects folder › ${value.trim() || "…"}` : `Corvin workspace › ${value.trim() || "…"}`;
  if (done) {
    return (
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg" data-testid="duplicate-dialog" data-stage="done">
        <DialogHeader>
          <DialogTitle className="break-words">Copied to "{done.project.id}"</DialogTitle>
          <DialogDescription>The copy has its own folder and history; the original is unchanged. Chats are not attached to the copy.</DialogDescription>
        </DialogHeader>
        <p className="break-all font-mono text-xs" data-testid="duplicate-result-path">{done.project.display_path || done.project.path}</p>
        {done.warnings.map((w) => <p key={w} className="text-sm" data-testid="duplicate-warning">{w}</p>)}
        {done.notes.length > 0 && <ul className="space-y-1 text-xs text-muted-foreground">{done.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onClose} data-testid="duplicate-close">Close</Button>
          <Button asChild size="sm"><Link to={`/app/projects/${encodeURIComponent(done.project.id)}`} onClick={onClose} data-testid="duplicate-open">Open the copy</Link></Button>
        </div>
      </DialogContent>
    );
  }
  return (
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg" data-testid="duplicate-dialog" data-stage="form">
      <DialogHeader>
        <DialogTitle className="break-words pr-6">Duplicate "{d.title || d.id}"</DialogTitle>
        <DialogDescription>A copy under a new id, with its own folder. The protection is copied; chat attachments are not.</DialogDescription>
      </DialogHeader>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (valid && !mut.isPending) mut.mutate(); }}>
        <label className="block text-sm">New id
          <input className="mt-1 w-full rounded border bg-background px-2 py-1 font-mono text-sm" value={value} maxLength={28} spellCheck={false}
            onChange={(e) => { setId(e.target.value); setError(null); }} data-testid="duplicate-id" disabled={mut.isPending} />
          {value.trim() && !ID_RULE.test(value.trim()) && <span className="mt-1 block text-xs text-red-700 dark:text-red-300">Use lowercase letters, digits and dashes, at most 28 characters.</span>}
        </label>
        <label className="block text-sm">Title
          <input className="mt-1 w-full rounded border bg-background px-2 py-1 text-sm" value={title} maxLength={120}
            onChange={(e) => { setTitle(e.target.value); setError(null); }} data-testid="duplicate-title" disabled={mut.isPending} />
          {!title.trim() && <span className="mt-1 block text-xs text-red-700 dark:text-red-300">Give the copy a title.</span>}
        </label>
        <Line label="Where" testid="duplicate-where">
          <span className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 break-all font-mono text-xs" data-testid="duplicate-where-path">{location ?? defaultPlace}</span>
            <button type="button" className="text-xs underline" onClick={() => setPicking(true)} disabled={mut.isPending} data-testid="duplicate-change">Change…</button>
            {location && <button type="button" className="text-xs underline" onClick={() => setLocation(null)} disabled={mut.isPending}>Use the default place</button>}
          </span>
        </Line>
        <div className="flex flex-wrap gap-x-4 gap-y-2" role="group" aria-label="Parts">
          <Check checked={parts.git_history} onChange={(v) => setParts({ ...parts, git_history: v })} label="Git history" testid="duplicate-part-history" disabled={mut.isPending} />
          {(d.kb === true || (d.active ?? []).includes("kb")) && <Check checked={parts.kb} onChange={(v) => setParts({ ...parts, kb: v })} label="Knowledge base" testid="duplicate-part-kb" disabled={mut.isPending} />}
          <Check checked={parts.generated} onChange={(v) => setParts({ ...parts, generated: v })} label="Generated folders" testid="duplicate-part-generated" disabled={mut.isPending} />
        </div>
        <p className="text-xs text-muted-foreground">{ARCHIVE_NOTE}</p>
        {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="duplicate-error">{error}</p>}
        {mut.isPending && <p role="status" className="text-sm" data-testid="duplicate-busy">Copying… a large project takes a while.</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={onClose} disabled={mut.isPending} data-testid="duplicate-cancel">Cancel</Button>
          <Button type="submit" size="sm" disabled={!valid || mut.isPending || !list.data} data-testid="duplicate-submit">{mut.isPending ? "Copying…" : "Duplicate"}</Button>
        </div>
      </form>
      <FolderPicker open={picking} purpose="import" title={value} initialPath={d.path ? parentOf(d.path) : null} inputTestId="duplicate-picker-input"
        onChoose={(p) => { setLocation(p); setPicking(false); }} onCancel={() => setPicking(false)} />
    </DialogContent>
  );
}
