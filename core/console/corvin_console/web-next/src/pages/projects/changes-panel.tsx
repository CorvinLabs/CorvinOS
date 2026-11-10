/**
 * The change view (CONCEPT-0127 D6/D7): what Corvin created or changed in the attached project, like the source-control view
 * of an IDE. A side panel next to the chat - it does not cover the conversation. List -> open a file -> diff; recent commits
 * keep already-committed work traceable; ids in file names and commit subjects link to Tasks and the Knowledge Graph.
 * Read-only: nothing here writes to the project.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getChangeFile, getChanges, type ChangeLink, type ChangedFile } from "@/lib/api/projects";
import { STATUS_META, ago, emptyMessage, groupByArea, linkTarget, parseDiff, splitPath, summaryLine } from "./changes-logic";

export const changesKey = (projectId: string, commit: string | null) => ["projects", "changes", projectId, commit] as const;

function Links({ projectId, links }: { projectId: string; links: ChangeLink[] }) {
  if (!links.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {links.map((l) => (
        <Link key={l.id} to={linkTarget(projectId, l)} data-testid="change-link" data-id={l.id}
          title={l.kind === "task" ? "Open on the Tasks board" : "Open in the Knowledge Graph"}
          className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground hover:text-foreground">
          {l.id}
        </Link>
      ))}
    </span>
  );
}

function FileRow({ projectId, f, onOpen, stale }: { projectId: string; f: ChangedFile; onOpen: () => void; stale?: boolean }) {
  const { dir, name } = splitPath(f.path);
  const m = STATUS_META[f.status];
  return (
    <li className={`flex items-center gap-2 border-b px-3 py-1.5 last:border-b-0 ${stale ? "opacity-50" : ""}`} data-testid="change-row" data-path={f.path} data-status={f.status}>
      <span className="w-5 shrink-0 text-center font-mono text-xs font-semibold" title={m.label} aria-label={m.label}>{m.letter}</span>
      <button type="button" onClick={onOpen} disabled={stale} className="min-w-0 flex-1 truncate text-left text-sm hover:underline" data-testid="change-open" title={f.path}>
        <span className="text-muted-foreground">{dir}</span>{name}
      </button>
      <Links projectId={projectId} links={f.links} />
      <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
        {f.additions === null && f.deletions === null ? "binary" : `+${f.additions ?? 0} -${f.deletions ?? 0}`}
      </span>
    </li>
  );
}

function FileView({ projectId, path, commit }: { projectId: string; path: string; commit: string | null }) {
  const q = useQuery({ queryKey: ["projects", "change-file", projectId, commit, path], queryFn: ({ signal }) => getChangeFile(projectId, path, commit, signal), retry: false });
  if (q.isLoading) return <Skeleton className="h-64 w-full" data-testid="change-file-loading" />;
  if (q.error || !q.data) return <p role="alert" className="p-3 text-sm text-red-700 dark:text-red-300" data-testid="change-file-error">This file cannot be shown (it may have changed or left the list). Go back and open it again.</p>;
  const v = q.data;
  if (v.binary) return <p className="p-3 text-sm text-muted-foreground" data-testid="change-file-binary">This is a binary file; it is listed but not shown.</p>;
  const lines = v.text !== null ? v.text.split("\n").map((t) => ({ kind: "add" as const, text: "+" + t })) : parseDiff(v.diff);
  if (lines.length === 0) return <p className="p-3 text-sm text-muted-foreground" data-testid="change-file-empty">No textual difference to show.</p>;
  const cls = { add: "bg-emerald-500/10", del: "bg-red-500/10", hunk: "bg-muted text-muted-foreground", ctx: "", meta: "text-muted-foreground" } as const;
  return (
    <div className="overflow-auto" data-testid="change-file">
      <pre className="min-w-max text-xs leading-5" data-testid="change-diff">
        {lines.map((l, i) => <div key={i} className={`px-3 ${cls[l.kind]}`} data-kind={l.kind}>{l.text || " "}</div>)}
      </pre>
      {v.truncated && <p className="p-3 text-xs text-muted-foreground" data-testid="change-file-truncated">The file is long; only the first part is shown.</p>}
    </div>
  );
}

export function ChangesPanel({ projectId, title, onClose }: { projectId: string; title: string; onClose: () => void }) {
  const [commit, setCommit] = useState<string | null>(null);
  const [file, setFile] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const q = useQuery({ queryKey: changesKey(projectId, commit), queryFn: ({ signal }) => getChanges(projectId, commit, signal), refetchInterval: 10_000, retry: false,
    staleTime: 0, refetchOnMount: "always",            // opening the panel always shows the files as they are now
    placeholderData: (prev) => prev });                 // switching commits keeps the picker (and its focus) on screen
  useEffect(() => { closeRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { if (file) setFile(null); else onClose(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [file, onClose]);
  const d = q.data;
  // a commit that cannot be read goes back to the uncommitted view instead of leaving the panel without its picker
  const [commitError, setCommitError] = useState<string | null>(null);
  useEffect(() => { if (q.error && commit) { setCommitError(`Commit ${commit.slice(0, 9)} could not be read; showing the uncommitted changes.`); setCommit(null); } }, [q.error, commit]);
  const loadingOther = q.isPlaceholderData;                // the rows below still belong to the PREVIOUS selection
  const groups = d ? groupByArea(d.files) : [];
  return (
    <aside role="dialog" aria-label={`Changes in ${title}`} data-testid="changes-panel"
      className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[34rem] flex-col border-l border-border bg-background shadow-xl">
      <header className="flex items-center gap-2 border-b px-3 py-2">
        {file && <Button variant="ghost" size="sm" onClick={() => setFile(null)} aria-label="Back to the list" data-testid="changes-back"><ArrowLeft className="h-4 w-4" /></Button>}
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium" data-testid="changes-title">{file ? file : `Changes in ${title}`}</div>
          {!file && d && !loadingOther && <div className="text-xs text-muted-foreground" data-testid="changes-summary">{commit ? `Commit ${commit.slice(0, 9)}: ` : ""}{summaryLine(d.files)}</div>}
        </div>
        <Button ref={closeRef} variant="ghost" size="sm" onClick={onClose} aria-label="Close the changes" data-testid="changes-close"><X className="h-4 w-4" /></Button>
      </header>

      {file ? (
        <div className="min-h-0 flex-1 overflow-auto"><FileView projectId={projectId} path={file} commit={commit} /></div>
      ) : (
        <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
          {q.isLoading && <Skeleton className="h-40 w-full" data-testid="changes-loading" />}
          {q.error && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="changes-error">The changes could not be read. Try again in a moment.</p>}
          {d && (
            <>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">Show
                <select className="min-w-0 flex-1 rounded border bg-background px-2 py-1 text-xs text-foreground" value={commit ?? ""} data-testid="changes-source"
                  onChange={(e) => { setCommitError(null); setCommit(e.target.value || null); }}>
                  <option value="">Changes not yet committed</option>
                  {d.commits.map((c) => <option key={c.sha} value={c.sha}>{`${c.short} · ${ago(c.at)} · ${c.subject}`.slice(0, 90)}</option>)}
                </select>
              </label>
              {commitError && <p role="alert" className="text-sm text-red-700 dark:text-red-300" data-testid="changes-commit-error">{commitError}</p>}
              {d.repository_note && <p className="text-xs text-muted-foreground" data-testid="changes-repo-note">Not listed: {d.repository_note}.</p>}
              {commit && d.commits.find((c) => c.sha === commit)?.links.length ? <Links projectId={projectId} links={d.commits.find((c) => c.sha === commit)!.links} /> : null}
              {groups.length === 0 ? (
                <p className="text-sm text-muted-foreground" data-testid="changes-empty">{loadingOther ? "Loading…" : emptyMessage(d.has_repository, commit !== null, d.repository_note)}</p>
              ) : groups.map((g) => (
                <section key={g.area} aria-label={g.label} data-testid="changes-group" data-area={g.area}>
                  <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{g.label} ({g.files.length})</h3>
                  <ul className="rounded-md border">{g.files.map((f) => <FileRow key={f.path} projectId={projectId} f={f} stale={q.isPlaceholderData} onOpen={() => setFile(f.path)} />)}</ul>
                </section>
              ))}
              {d.truncated && <p className="text-xs text-muted-foreground" data-testid="changes-truncated">The list stops at {d.files.length} files.</p>}
            </>
          )}
        </div>
      )}
    </aside>
  );
}
