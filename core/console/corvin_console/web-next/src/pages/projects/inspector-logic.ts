/**
 * The inspector's decisions, kept pure so they are unit-tested: what a feature row says, what the server's
 * 409 means for the user, and which tabs exist. The page only draws these.
 */
import { ApiError } from "@/lib/api/client";
import type { FeatureRow, HealthCheck, ProjectDetail, WorkspaceState } from "@/lib/api/projects";
import { featureLabel } from "./encodings";

export type Tab = "overview" | "structure" | "docs" | "knowledge" | "tasks" | "videos" | "directory" | "compute" | "features" | "settings";

/** Each tab, and the feature it needs (null = always there). A tab whose feature is off shows "Feature off". */
export const TABS: { id: Tab; label: string; feature: string | null }[] = [
  { id: "overview", label: "Overview", feature: null },
  { id: "structure", label: "Structure", feature: null },
  { id: "docs", label: "Docs", feature: "kb" },
  { id: "knowledge", label: "Knowledge", feature: "graph" },
  { id: "tasks", label: "Tasks", feature: "tasks" },
  { id: "videos", label: "Videos", feature: "videos" },
  { id: "directory", label: "Directory", feature: "directory" },
  { id: "compute", label: "Compute", feature: "acs" },
  { id: "features", label: "Features", feature: null },
  { id: "settings", label: "Settings", feature: null },
];

export function parseTab(v: string | null): Tab {
  return TABS.some((t) => t.id === v) ? (v as Tab) : "overview";
}

/** Whether a tab's content may be shown, and which feature to offer when it may not. */
export function tabGate(tab: Tab, active: string[]): { open: boolean; feature: string | null } {
  const t = TABS.find((x) => x.id === tab);
  if (!t || t.feature === null) return { open: true, feature: null };
  return { open: active.includes(t.feature), feature: t.feature };
}

/** What one feature row offers: switch on/off, and why it cannot be switched on yet. */
export interface RowState {
  label: string;
  canToggle: boolean;
  action: "on" | "off";
  /** Shown when the feature is off: the requirements that would be pulled in when it is switched on. */
  preview: string[];
  blockedBy: string | null;
}

export function rowState(f: FeatureRow): RowState {
  const missingPlugins = f.plugins.filter((p) => p.state !== "enabled");
  // a missing plugin blocks switching a feature ON; switching an active one OFF is always allowed (the server checks plugins
  // only for features being added), so a plugin disabled later never traps a feature in the project
  const blockedBy = missingPlugins.length && !f.active
    ? `needs the ${missingPlugins.map((p) => p.plugin.replace(/_/g, " ")).join(", ")} plugin, which is not enabled for this tenant`
    : null;
  return {
    label: featureLabel(f.id),
    canToggle: f.id !== "core" && blockedBy === null,
    action: f.active ? "off" : "on",
    preview: f.active ? [] : f.requires.filter((r) => r !== "core").map(featureLabel),
    blockedBy,
  };
}

/** The server's 409 for a feature change, turned into the choice the user has to make. */
export type Conflict =
  | { kind: "dependants"; feature: string; dependants: string[] }
  | { kind: "requires"; requires: string[] }
  | { kind: "plugin"; message: string }
  | { kind: "other"; message: string };

export function conflictOf(err: unknown): Conflict | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.detail as { detail?: Record<string, unknown> } | undefined;
  const d = (body?.detail ?? {}) as Record<string, unknown>;
  const reason = typeof d.reason_class === "string" ? d.reason_class : "";
  const message = typeof d.message === "string" ? d.message : "The change was refused.";
  if (reason === "has_dependants" && Array.isArray(d.dependants)) {
    return { kind: "dependants", feature: String(d.feature ?? ""), dependants: (d.dependants as string[]).map(featureLabel) };
  }
  if (reason === "needs_dependencies" && Array.isArray(d.requires)) {
    return { kind: "requires", requires: (d.requires as string[]).map(featureLabel) };
  }
  if (reason === "plugin_missing") return { kind: "plugin", message };
  return { kind: "other", message };
}

/** The Overview's headline from the checks: never "all good" while a check is red. */
export function healthHeadline(checks: { ok: boolean }[]): { text: string; ok: boolean } {
  const failing = checks.filter((c) => !c.ok).length;
  if (checks.length === 0) return { text: "No checks have run.", ok: false };
  if (failing === 0) return { text: "Every check passes.", ok: true };
  return { text: `${failing} of ${checks.length} checks need attention.`, ok: false };
}

/** The same rule the server enforces (VIDEO_NAME_RE): shown before the request, never instead of it. */
export function videoNameProblem(name: string): string | null {
  const n = name.trim();
  if (n.length === 0) return "Give the video a name.";
  if (n.length > 60) return "The name is at most 60 characters.";
  if (!/^[a-z0-9][a-z0-9-]*$/.test(n)) return "Use lowercase letters, digits and dashes, starting with a letter or digit.";
  return null;
}

// ── the Settings tab (T-0387, CONCEPT-0131 § C1) ──────────────────────────────────────────────────────

export type SectionId = "general" | "location" | "protection" | "chat" | "maintenance" | "share" | "danger";
export type ActionId = "rename" | "move" | "locate" | "rekey" | "repair" | "export" | "duplicate" | "remove" | "delete";
/** An action that is listed. `note` says why it is off (or, for an own folder's delete row, why it does not exist). */
export interface ActionGate { enabled: boolean; note: string | null }
export interface SettingsSection {
  id: SectionId;
  title: string;
  /** Present = drawn; absent = not offered in this state. */
  actions: Partial<Record<ActionId, ActionGate>>;
  /** Sentences the section shows as they are (the hint-word warning of an own folder). */
  notes: string[];
}

export const SECTION_TITLE: Record<SectionId, string> = {
  general: "General", location: "Location", protection: "Protection", chat: "Use in a chat",
  maintenance: "Maintenance", share: "Share", danger: "Danger zone",
};

const ON: ActionGate = { enabled: true, note: null };
const off = (note: string): ActionGate => ({ enabled: false, note });

/** Which sections and actions the Settings tab offers for a project in its state (ok, missing, invalid) and place (managed, own,
 *  built in). The server judges every action again; this only decides what is drawn. */
export function settingsSections(d: ProjectDetail, ctx: { workspace?: WorkspaceState }): SettingsSection[] {
  const sec = (id: SectionId, actions: SettingsSection["actions"] = {}, notes: string[] = []): SettingsSection =>
    ({ id, title: SECTION_TITLE[id], actions, notes });
  if (d.builtin || d.storage === "builtin") return [sec("chat")];
  const ok = d.state === "ok";
  const missing = d.state === "missing";
  const managed = d.storage === "managed";
  const location: SettingsSection["actions"] = {};
  if (ok) location.move = ON;
  if (missing) location.locate = ON;
  if (ctx.workspace === "foreign") location.rekey = ON;
  const hints = d.storage === "own" ? (d.location_warnings ?? []).filter((w) => w.class === "hint_word").map((w) => w.message) : [];
  const out: SettingsSection[] = [
    sec("general", { rename: ok ? ON : off("The title can be changed once the project's folder is usable again.") }),
    sec("location", location, hints),
  ];
  if (ok) out.push(sec("protection"), sec("chat"));
  if (ok || d.state === "invalid") out.push(sec("maintenance", { repair: ok ? ON : off("Repair needs a readable project.yaml.") }));
  // Export always opens: its dialog says why a secret project cannot leave; a secret project is never duplicated either
  if (ok) out.push(sec("share", { export: ON, duplicate: d.classification === "secret" ? off("Secret projects cannot be duplicated.") : ON }));
  // Deleting files exists only for a project in the Corvin workspace whose folder is there (ADR-2321 Decision 6); an own folder
  // has no delete row at all - Corvin never deletes in a folder it does not own
  out.push(sec("danger", managed && ok ? { remove: ON, delete: ON } : { remove: ON }));
  return out;
}

// ── the Trash (T-0405, ADR-2321 Decision 12) ─────────────────────────────────────────────────────────

/** Deploy marker of the trash UI (a string literal only this code contains). */
export const MARKER_TRASH = "Move to Trash";
export const TRASH_DAYS = 30;
export const DELETE_ROW_TEXT = "Delete project files — only for projects in the Corvin workspace; goes to Trash for 30 days.";
export const DELETE_SENTENCE = "The folder moves to Corvin's trash for 30 days; restore it from the Projects page.";
export const PURGE_SENTENCE = "This removes the folder and every file in it for good. It cannot be undone.";

/** The typed confirmation: the exact id, nothing trimmed, no case folding (the server compares the same way). */
export function confirmMatches(typed: string, id: string): boolean {
  return typed === id && id.length > 0;
}

const DAY_MS = 86_400_000;

/** `deleted_at` + 30 days as an ISO timestamp, or null for a missing or unreadable date. */
export function restorableUntil(deletedAt: string | null | undefined, days: number = TRASH_DAYS): string | null {
  if (!deletedAt) return null;
  const t = Date.parse(deletedAt);
  if (Number.isNaN(t)) return null;
  return new Date(t + days * DAY_MS).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** A date for the Trash rows, pinned to en-US and UTC so every viewer reads the same day ("Nov 11, 2026"). */
export function trashDate(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "unknown";
  return new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(t));
}

export interface TrashRow {
  entry: string; id: string; title: string; deleted: string; until: string; canAct: boolean; note: string | null;
}

/** The Trash section's rows, newest first. The restore deadline comes from the server when it sent one, else from `deleted_at`. */
export function trashRows(entries: { entry: string; id: string; title: string; deleted_at: string | null; restorable_until: string | null; pending: boolean }[] | undefined): TrashRow[] {
  return [...(entries ?? [])]
    .sort((a, b) => String(b.deleted_at ?? "").localeCompare(String(a.deleted_at ?? "")) || b.entry.localeCompare(a.entry))
    .map((e) => ({
      entry: e.entry, id: e.id, title: e.title || e.id, deleted: trashDate(e.deleted_at),
      until: trashDate(e.restorable_until ?? restorableUntil(e.deleted_at)),
      canAct: !e.pending, note: e.pending ? "Still being moved to the Trash; try again in a moment." : null,
    }));
}

/** "Trash (n)" - shown only when there is something in it. */
export function trashLinkLabel(n: number | undefined): string | null {
  return n && n > 0 ? `Trash (${n})` : null;
}

/** The Maintenance line: "<n> checks, <m> failed — <first failed detail>". Notes are observations, not checks. */
export function doctorSummary(checks: HealthCheck[]): string {
  const real = checks.filter((c) => !c.note);
  if (real.length === 0) return "No checks have run.";
  const failed = real.filter((c) => !c.ok);
  if (failed.length === 0) return `${real.length} checks, none failed`;
  return `${real.length} checks, ${failed.length} failed — ${failed[0].detail}`;
}
