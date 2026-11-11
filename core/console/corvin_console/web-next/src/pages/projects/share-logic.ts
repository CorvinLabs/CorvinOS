/**
 * The decisions of the Share dialogs (export, import, duplicate) as pure functions, unit-tested in tests/unit/projects-share.test.ts.
 * The server judges every action again (class gate, conflicts, place); these only decide what is drawn and when a button is on.
 * Every sentence here is shown as it is - English, no internal ids.
 */
import type {
  DataClass, ExportJob, ExportPreview, ExportPreviewProject, ImportCard, ImportConflict, LeftOut, PartRow,
} from "@/lib/api/projects";
import { CLASS_META } from "./roles";

/** The label every import carries: a bundle is not signed, anyone can make one. */
export const ORIGIN_LABEL = "Unverified origin";
export const ORIGIN_HINT = "Anyone can make a project bundle. Import it only if you know where it came from.";
export const ARCHIVE_NOTE = "Archives inside the project are not checked for secrets.";
export const GPG_MISSING = "A passphrase needs gpg, which is not installed on this machine.";
export const PROTECTION_LATER = "assistant: read only — change later in Settings";

/** The new-id rule of project.yaml (lowercase kebab-case, 1-28 characters); the server checks reserved words too. */
export const ID_RULE = /^[a-z0-9][a-z0-9-]{0,27}$/;

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "0 B";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** The export's "Knowledge base" part is offered only while one of the projects has one (REVIEW-0067 #17). Until the preview
 *  answered it is offered, so the first preview asks for it. */
export function offersKbPart(pv: { projects: { features?: Record<string, boolean> }[] } | undefined): boolean {
  if (!pv) return true;
  return pv.projects.some((p) => p.features?.kb === true);
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function part(parts: PartRow[] | undefined, name: PartRow["name"]): PartRow | undefined {
  return (parts ?? []).find((p) => p.name === name);
}

/** "212 files · 5.1 MB · knowledge base (41 entries) · git history (38 commits)" for one preview project. */
export function includedLine(p: Pick<ExportPreviewProject, "counts" | "parts" | "kb_entries" | "commits">): string {
  const bits = [plural(p.counts.files, "file"), formatBytes(p.counts.bytes)];
  if (part(p.parts, "kb")?.included) bits.push(`knowledge base (${plural(p.kb_entries ?? 0, "entry", "entries")})`);
  if (part(p.parts, "git_history")?.included) bits.push(p.commits == null ? "git history" : `git history (${plural(p.commits, "commit")})`);
  if (part(p.parts, "videos_index")?.included) bits.push("videos index");
  if (part(p.parts, "generated")?.included) bits.push("generated folders");
  return bits.join(" · ");
}

/** The words for a left-out reason (the server's closed vocabulary; an unknown reason is shown as it is). */
export const LEFT_OUT_LABEL: Record<string, string> = {
  machine_local: "machine-local", git_internal: "git internals", protected_name: "Corvin's own data", engine_copy: "engine copy",
  graph_cache: "rebuilt on import", secret_name: "secret name", generated: "generated", link: "link", hard_link: "hard link",
  mount: "another disk", nested_manifest: "another project", unportable_name: "name not portable", special_file: "special file",
};

/** "Left out" as short lines, at most *max*, then "and N more". */
export function leftOutLines(rows: LeftOut[] | undefined, max = 8): string[] {
  const all = (rows ?? []).map((r) => `${r.path} (${LEFT_OUT_LABEL[r.reason] ?? r.reason.replace(/_/g, " ")})`);
  if (all.length <= max) return all;
  return [...all.slice(0, max), `and ${all.length - max} more`];
}

export interface ExportGate {
  /** the export cannot be made at all (a secret project): the reason, the button stays off */
  refused: string | null;
  /** a typed confirmation is needed; the word to type, else null */
  confirmWord: string | null;
  /** the class line under the parts */
  classLine: string;
  /** the passphrase field is drawn (gpg present) */
  passphrase: boolean;
  /** shown instead of the field when gpg is missing */
  passphraseNote: string | null;
}

/** Data-class gating of an export: secret never, confidential with a typed confirmation, the rest freely. */
export function exportGate(projects: { id: string; classification?: DataClass }[], gpg: boolean): ExportGate {
  const cls = (c?: DataClass): DataClass => (c && c in CLASS_META ? c : "internal");
  const secret = projects.filter((p) => cls(p.classification) === "secret");
  const conf = projects.filter((p) => cls(p.classification) === "confidential");
  const passphrase = gpg;
  const passphraseNote = gpg ? null : GPG_MISSING;
  if (secret.length) {
    const who = projects.length === 1 ? "" : ` (${secret.map((p) => p.id).join(", ")})`;
    return { refused: `Secret projects cannot be exported${who}.`, confirmWord: null,
      classLine: "Data class: Secret — never leaves this machine.", passphrase: false, passphraseNote: null };
  }
  if (conf.length) {
    const word = conf.length === 1 ? conf[0].id : "export confidential";
    const which = projects.length === 1 ? "This project is" : conf.length === 1 ? `${conf[0].id} is` : `${conf.length} projects are`;
    return { refused: null, confirmWord: word,
      classLine: `Data class: Confidential — ${which} confidential. Type ${word} to confirm the export.`, passphrase, passphraseNote };
  }
  const label = projects.length === 1 ? CLASS_META[cls(projects[0].classification)].label : "Public or internal";
  return { refused: null, confirmWord: null, classLine: `Data class: ${label} — can be shared.`, passphrase, passphraseNote };
}

/** The class line of one project in the dialog (task wording: exportClassLine(detail, gpg)). */
export function exportClassLine(d: { id: string; classification?: DataClass }, gpg: boolean): string {
  return exportGate([d], gpg).classLine;
}

export interface ExportFormState {
  gate: ExportGate;
  typed: string;
  passphrase: string;
  busy: boolean;
  /** the preview is loaded and not refused */
  ready: boolean;
}

/** "Export ZIP" is on when the preview is ready, nothing refuses, the typed word matches and a given passphrase is 8-128 long. */
export function exportEnabled(s: ExportFormState): boolean {
  if (!s.ready || s.busy || s.gate.refused) return false;
  if (s.gate.confirmWord !== null && s.typed.trim() !== s.gate.confirmWord) return false;
  if (s.passphrase && (s.passphrase.length < 8 || s.passphrase.length > 128)) return false;
  return true;
}

/** True when the preview found secret-looking files in the git history of a project whose history is included. */
export function offersWithoutHistory(pv: ExportPreview | undefined): boolean {
  return !!pv?.projects.some((p) => (p.history_findings ?? 0) > 0 && !!part(p.parts, "git_history")?.included);
}

/** The progress line of a large export that runs as a job. */
export function jobLine(j: ExportJob | undefined): string {
  if (!j) return "Starting the export…";
  if (j.state === "running") return `Building the ZIP: ${plural(j.progress?.files ?? 0, "file")}, ${formatBytes(j.progress?.bytes ?? 0)} so far…`;
  if (j.state === "done") return "The ZIP is ready.";
  return j.message || "The export failed; nothing was kept.";
}

// ── import ───────────────────────────────────────────────────────────────────────────────────────────────

export type ConflictAction = "new_id" | "choose_place" | "copy" | "none";
export interface ConflictRow {
  kind: string;
  message: string;
  /** the single fix this conflict offers */
  action: ConflictAction;
  fixLabel: string;
  suggestedId?: string;
}

/** One row per conflict, each with its single fix: a new id, another place, or "import as a copy". */
export function conflictRows(conflicts: ImportConflict[] | undefined): ConflictRow[] {
  return (conflicts ?? []).map((c) => {
    const sid = c.fix?.suggested_id;
    if (c.kind === "id_taken" || c.kind === "invalid_id") return { kind: c.kind, message: c.message, action: "new_id", fixLabel: "New id", suggestedId: sid };
    if (c.kind === "same_source") {
      return { kind: c.kind, message: c.message, action: "copy", fixLabel: "Import as a copy", suggestedId: sid };
    }
    if (c.fix?.choose_location) return { kind: c.kind, message: c.message, action: "choose_place", fixLabel: "Choose another place" };
    return { kind: c.kind, message: c.message, action: "none", fixLabel: "" };
  });
}

export interface ImportChoices {
  /** the id the project gets here (default: the bundle's) */
  projectId: string;
  /** an own folder; null = the default place */
  location: string | null;
  skipHistory: boolean;
  skipKb: boolean;
}

export function initialChoices(card: ImportCard): ImportChoices {
  return { projectId: card.id, location: null, skipHistory: false, skipKb: false };
}

export interface ImportGateState {
  choices: ImportChoices;
  /** the conflicts the server answered for exactly these choices; undefined while the verdict is loading */
  verdict: { for: string; conflicts: ImportConflict[] } | undefined;
  applying: boolean;
}

/** The key a verdict belongs to: a verdict for other choices is stale. */
export function verdictKey(c: Pick<ImportChoices, "projectId" | "location">): string {
  return `${c.projectId}\u0000${c.location ?? ""}`;
}

/** "Import" is on only when the verdict for the CURRENT id and place says there is no conflict. */
export function importEnabled(s: ImportGateState): boolean {
  if (s.applying || !ID_RULE.test(s.choices.projectId)) return false;
  if (!s.verdict || s.verdict.for !== verdictKey(s.choices)) return false;
  return s.verdict.conflicts.length === 0;
}

/** Which "Skip …" boxes the card offers: only parts the bundle carries. */
export function skippableParts(card: Pick<ImportCard, "parts" | "git">): { history: boolean; kb: boolean } {
  return { history: !!card.git && !!part(card.parts, "git_history")?.included, kb: !!part(card.parts, "kb")?.included };
}

/** "212 files · 5.1 MB · knowledge base · git history" for an import card. */
export function willCreateLine(card: Pick<ImportCard, "counts" | "parts">, choices?: Pick<ImportChoices, "skipHistory" | "skipKb">): string {
  const bits = [plural(card.counts.files, "file"), formatBytes(card.counts.bytes)];
  if (part(card.parts, "kb")?.included && !choices?.skipKb) bits.push("knowledge base");
  if (part(card.parts, "git_history")?.included && !choices?.skipHistory) bits.push("git history");
  if (part(card.parts, "videos_index")?.included) bits.push("videos index");
  return bits.join(" · ");
}

/** "From <file> · exported 2026-11-10 by CorvinOS 1.9" */
export function fromLine(file: string, report: { created_at?: string; exporter?: { app?: string; version?: string } }): string {
  const day = (report.created_at ?? "").slice(0, 10);
  const app = report.exporter?.app === "corvinos" ? "CorvinOS" : report.exporter?.app || "an unknown program";
  const ver = report.exporter?.version ? ` ${report.exporter.version}` : "";
  return `From ${file}${day ? ` · exported ${day}` : ""} by ${app}${ver}`;
}

/** The protection an import gets: the bundle's class is a floor (public becomes internal), the assistant reads only. */
export function importProtection(cls: DataClass | undefined): DataClass {
  return cls === "confidential" || cls === "secret" ? cls : "internal";
}

// ── duplicate ────────────────────────────────────────────────────────────────────────────────────────────────

/** The server's rule for a free id (<id>-2, -3, …, cut to 28 characters). */
export function suggestId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let n = 2; n < 100; n += 1) {
    const suffix = `-${n}`;
    const cand = base.slice(0, 28 - suffix.length).replace(/-+$/, "") + suffix;
    if (!used.has(cand)) return cand;
  }
  return "";
}

// ── list multi-select ────────────────────────────────────────────────────────────────────────────────────────

/** Toggles one id in the selection (order kept). */
export function toggleSelected(sel: string[], id: string): string[] {
  return sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id];
}

/** Only selectable projects stay selected: usable, not built in, not being imported (a refresh may drop one). */
export function selectable(p: { id: string; state: string; builtin?: boolean }): boolean {
  return p.state === "ok" && !p.builtin;
}

export function pruneSelected(sel: string[], projects: { id: string; state: string; builtin?: boolean }[]): string[] {
  const ok = new Set(projects.filter(selectable).map((p) => p.id));
  return sel.filter((x) => ok.has(x));
}

/** "Export selected (n)" */
export function exportSelectedLabel(n: number): string {
  return `Export selected (${n})`;
}

/** Anything the API answered under `detail`, as `{reason_class, message}`. */
export function refusalOf(detail: unknown): { reason_class?: string; message?: string } {
  const d = detail && typeof detail === "object" && "detail" in detail ? (detail as { detail: unknown }).detail : detail;
  return d && typeof d === "object" ? (d as { reason_class?: string; message?: string }) : {};
}
