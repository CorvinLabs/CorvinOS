/**
 * The change view's decisions, pure and unit-tested (CONCEPT-0127 D6/D7): how files are grouped, how a status is
 * told in words AND a letter (never colour alone), how a unified diff becomes lines, where an id links to.
 */
import type { ChangeLink, ChangeStatus, ChangedFile } from "@/lib/api/projects";

export const AREA_LABEL: Record<ChangedFile["area"], string> = { code: "Code", docs: "Documents", knowledge: "Knowledge base" };
const AREA_ORDER: ChangedFile["area"][] = ["code", "docs", "knowledge"];

export const STATUS_META: Record<ChangeStatus, { letter: string; label: string }> = {
  modified: { letter: "M", label: "Changed" },
  added: { letter: "A", label: "Added" },
  untracked: { letter: "N", label: "New" },
  deleted: { letter: "D", label: "Deleted" },
  renamed: { letter: "R", label: "Renamed" },
  committed: { letter: "C", label: "Committed" },
};

export interface AreaGroup { area: ChangedFile["area"]; label: string; files: ChangedFile[] }

/** Code first, then documents, then the knowledge base; empty groups are dropped. */
export function groupByArea(files: ChangedFile[]): AreaGroup[] {
  return AREA_ORDER.map((a) => ({ area: a, label: AREA_LABEL[a], files: files.filter((f) => f.area === a) })).filter((g) => g.files.length > 0);
}

export function totals(files: ChangedFile[]): { files: number; additions: number; deletions: number } {
  return files.reduce((t, f) => ({ files: t.files + 1, additions: t.additions + (f.additions ?? 0), deletions: t.deletions + (f.deletions ?? 0) }),
    { files: 0, additions: 0, deletions: 0 });
}

/** "3 files, +12 -4" - the sentence the panel opens with; nothing invented when there are no changes. */
export function summaryLine(files: ChangedFile[]): string {
  if (files.length === 0) return "No changes";
  const t = totals(files);
  return `${t.files} ${t.files === 1 ? "file" : "files"}, +${t.additions} -${t.deletions}`;
}

export function splitPath(path: string): { dir: string; name: string } {
  const i = path.lastIndexOf("/");
  return i < 0 ? { dir: "", name: path } : { dir: path.slice(0, i + 1), name: path.slice(i + 1) };
}

export type DiffKind = "add" | "del" | "hunk" | "ctx" | "meta";
export interface DiffLine { kind: DiffKind; text: string }

/** A unified diff as typed lines. The leading +/- stays in the text, so a reader without colour still sees it. */
export function parseDiff(diff: string): DiffLine[] {
  if (!diff) return [];
  const lines = diff.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  let inHunk = false;
  return lines.map((text) => {
    if (text.startsWith("@@")) { inHunk = true; return { kind: "hunk" as const, text }; }
    if (!inHunk) return { kind: "meta" as const, text };
    if (text.startsWith("+")) return { kind: "add" as const, text };
    if (text.startsWith("-")) return { kind: "del" as const, text };
    return { kind: "ctx" as const, text };
  });
}

/**
 * Where an id found in a file name or commit subject leads: a task to the project's board filtered to that id (a KB task's title
 * starts with it), anything else to that node of the project's graph (`?node=<human id>` resolves in one project's view).
 */
export function linkTarget(projectId: string, l: ChangeLink): string {
  const scope = `scope=project:${encodeURIComponent(projectId)}`;
  const id = encodeURIComponent(l.id);
  return l.kind === "task" ? `/app/initiatives?${scope}&q=${id}` : `/app/corvin-knowledge?${scope}&node=${id}`;
}

/** "5 min ago" for the commit list; absolute date once it is older than two days. */
export function ago(unixSeconds: number, nowMs: number = Date.now()): string {
  if (!unixSeconds) return "";
  const s = Math.max(0, Math.round(nowMs / 1000 - unixSeconds));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 2 * 86400) return "yesterday";
  return new Date(unixSeconds * 1000).toISOString().slice(0, 10);
}

/** What the panel says when there is nothing to list - the reason decides, never a bare blank. */
export function emptyMessage(hasRepository: boolean, viewingCommit: boolean, note?: string | null): string {
  if (!hasRepository && note) return `Changes cannot be listed: ${note}.`;
  if (!hasRepository) return "This folder is not a git repository, so changes cannot be listed. Corvin can still read and write files here.";
  if (viewingCommit) return "This commit changed no files.";
  return "No changes yet. Files Corvin creates or edits in this project appear here.";
}
