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
  out.push(sec("danger", { remove: ON, delete: managed ? off("Available in a later update") : off("(not available: own folder)") }));
  return out;
}

/** The Maintenance line: "<n> checks, <m> failed — <first failed detail>". Notes are observations, not checks. */
export function doctorSummary(checks: HealthCheck[]): string {
  const real = checks.filter((c) => !c.note);
  if (real.length === 0) return "No checks have run.";
  const failed = real.filter((c) => !c.ok);
  if (failed.length === 0) return `${real.length} checks, none failed`;
  return `${real.length} checks, ${failed.length} failed — ${failed[0].detail}`;
}
