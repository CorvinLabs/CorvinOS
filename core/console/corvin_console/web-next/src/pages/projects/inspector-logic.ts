/**
 * The inspector's decisions, kept pure so they are unit-tested: what a feature row says, what the server's
 * 409 means for the user, and which tabs exist. The page only draws these.
 */
import { ApiError } from "@/lib/api/client";
import type { ChangeList, FeatureRow, HealthCheck, ProjectDetail, VideoRow, WorkspaceState } from "@/lib/api/projects";
import type { Summary } from "@/lib/api/task-tracking";
import { featureLabel } from "./encodings";
import { formatBytes } from "./share-logic";

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

/** The label of a tab, as the tab bar draws it. */
export function tabLabel(tab: Tab): string {
  return TABS.find((t) => t.id === tab)?.label ?? tab;
}

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
  /** On for this project. `core` is always on, also when an adopted project's manifest lists no features (REVIEW-0067 #6). */
  on: boolean;
  /** "On", "Always on" or "Feature off". */
  status: string;
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
  const core = f.id === "core";
  return {
    label: featureLabel(f.id, f.title),
    on: core || f.active,
    status: core ? "Always on" : f.active ? "On" : "Feature off",
    canToggle: !core && blockedBy === null,
    action: f.active ? "off" : "on",
    preview: f.active || core ? [] : f.requires.filter((r) => r !== "core").map((r) => featureLabel(r)),
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
    return { kind: "dependants", feature: String(d.feature ?? ""), dependants: (d.dependants as string[]).map((x) => featureLabel(x)) };
  }
  if (reason === "needs_dependencies" && Array.isArray(d.requires)) {
    return { kind: "requires", requires: (d.requires as string[]).map((x) => featureLabel(x)) };
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

/** Rows per Trash page: the server's page size (GET /projects/-/trash, at most 200). */
export const TRASH_PAGE = 200;

export interface TrashPager {
  /** "Showing 201–400 of 523" while the Trash spans more than one page, else null. */
  label: string | null;
  /** The offset of the newer page, or null on the first page. */
  newer: number | null;
  /** The offset of the older page (the server's `next_offset`), or null on the last page. */
  older: number | null;
  /** Set when this page came back empty although it is not the first (its rows were restored or deleted): go here. */
  stepBack?: number;
}

/** The Trash pager from one server page (REVIEW-0063 m6). A server without paging fields is one page. */
export function trashPager(d: { entries?: unknown[]; total?: number; offset?: number; limit?: number; next_offset?: number | null } | undefined): TrashPager {
  if (!d) return { label: null, newer: null, older: null };
  const n = d.entries?.length ?? 0;
  const off = Math.max(0, d.offset ?? 0);
  const limit = d.limit && d.limit > 0 ? d.limit : TRASH_PAGE;
  const total = d.total ?? off + n;
  const older = d.next_offset ?? null;
  const newer = off > 0 ? Math.max(0, off - limit) : null;
  if (n === 0 && off > 0) return { label: null, newer, older: null, stepBack: newer ?? 0 };
  const label = older === null && newer === null ? null : `Showing ${off + 1}–${off + n} of ${total}`;
  return { label, newer, older };
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
  return `${real.length} checks, ${failed.length} failed — ${checkDetail(failed[0])}`;
}

/** "['a', 'b']" (a Python list the server formats into some details) as "a, b". */
function plainList(s: string): string {
  return s.replace(/\[((?:\s*'[^']*'\s*,?)*)\]/g, (_m, inner: string) => (inner.match(/'([^']*)'/g) ?? []).map((x) => x.slice(1, -1)).join(", "));
}

function sentence(s: string): string {
  const t = s.trim();
  if (!t) return "";
  // a sentence that starts with a file name ("project.yaml is valid") keeps the name as it is written
  const first = t.split(/\s/)[0];
  const c = /[./]/.test(first) ? t : t.charAt(0).toUpperCase() + t.slice(1);
  return /[.!?]$/.test(c) ? c : `${c}.`;
}

/**
 * A health check's detail in words (REVIEW-0067 #9). The server's details are fixed sentences for an operator log; the ones a
 * user cannot read ("kb check: blocking 0", a Python list, "the path gate's hint test is loadable") are said again here, every
 * other text is kept as it is (capitalised, with a period). The server's own text stays available as a tooltip in the UI.
 */
export function checkDetail(c: HealthCheck): string {
  const d = (c.detail ?? "").trim();
  let m = /^kb check: blocking (\d+)$/.exec(d);
  if (m) {
    const n = Number(m[1]);
    return n === 0 ? "The knowledge base check finds no blocking problem." : `The knowledge base check finds ${n} blocking problem${n === 1 ? "" : "s"}.`;
  }
  if (d === "the path gate's hint test is loadable") return "The rules that guard file paths in a chat are available.";
  m = /^out of sync \(repair fixes\): (.*)$/.exec(d);
  if (m) return `Generated files are out of date: ${plainList(m[1])}. Repair rewrites them.`;
  m = /^missing requirements: (\[.*\])$/.exec(d);
  if (m) return `Features this project needs are off: ${(m[1].match(/'([^']*)'/g) ?? []).map((x) => featureLabel(x.slice(1, -1))).join(", ")}.`;
  return sentence(plainList(d));
}

/** The Docs tab's rows: every kind the server counted, in words, biggest first (REVIEW-0067 #10). */
export function docsRows(counts: Record<string, number> | undefined): { kind: string; label: string; n: number }[] {
  return Object.entries(counts ?? {}).filter(([, n]) => Number.isFinite(n))
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([kind, n]) => {
      const w = KB_KIND[kind];
      const word = w ? w[1] : kind.replace(/[-_]/g, " ");
      return { kind, label: word.charAt(0).toUpperCase() + word.slice(1), n };
    });
}

const TASK_STATUS: Record<string, string> = { open: "Open", in_progress: "In progress", blocked: "Blocked", done: "Done", cancelled: "Cancelled" };

/** A task's status as words: "In progress", never "in_progress" (REVIEW-0067 #11). */
export function taskStatusLabel(status: string | null | undefined): string {
  const s = String(status ?? "");
  if (TASK_STATUS[s]) return TASK_STATUS[s];
  const w = s.replace(/_/g, " ");
  return w.charAt(0).toUpperCase() + w.slice(1);
}

/** A file size for the Structure tab: "2.0 KB", not "2048 B". */
export function sizeLabel(bytes: number): string {
  return formatBytes(bytes);
}


// ── "At a glance": the few numbers a project owner wants before opening a tab ─────────────────────────────────────────────────
// Every number comes from an endpoint that already exists (task summary, structure, change list); nothing here is invented. A tile
// whose source is off is not drawn at all: there is nothing to count, and a zero would claim otherwise.

export type GlanceId = "tasks" | "knowledge" | "videos" | "changes";
export interface GlanceTile { id: GlanceId; label: string; /** the tab the tile leads to, null = the tile only informs */ tab: Tab | null }
export interface GlanceValue { value: string; detail: string }

export function glanceTiles(d: Pick<ProjectDetail, "active" | "kb" | "builtin">): GlanceTile[] {
  const active = d.active ?? [];
  const out: GlanceTile[] = [];
  if (d.kb === true && active.includes("tasks")) out.push({ id: "tasks", label: "Tasks", tab: "tasks" });
  // the Knowledge tab needs the graph feature; with only the knowledge base on, the numbers lead to the Docs tab instead of a dead end
  if (d.kb === true) out.push({ id: "knowledge", label: "Knowledge", tab: active.includes("graph") ? "knowledge" : "docs" });
  if (active.includes("videos")) out.push({ id: "videos", label: "Videos", tab: "videos" });
  if (!d.builtin) out.push({ id: "changes", label: "Changes", tab: null });
  return out;
}

/** A leading tile's accessible name: its number, and the tab it really opens (Knowledge may lead to Docs; REVIEW-0067 #12). */
export function glanceAriaLabel(tile: GlanceTile, v: GlanceValue): string {
  return `${tile.label}: ${v.value} ${v.detail}. Open the ${tile.tab ? tabLabel(tile.tab) : tile.label} tab`;
}

/** The task board of a project: its open tasks first, because that is the question ("what is left?"). */
export function tasksGlance(s: Summary): GlanceValue {
  return { value: String(s.open), detail: `open · ${s.in_progress} in progress · ${s.blocked} blocked` };
}

// singular, plural: "1 epic", "2 epics" - a count that reads wrong makes the whole tile look unreliable
const KB_KIND: Record<string, [string, string]> = { initiatives: ["initiative", "initiatives"], epics: ["epic", "epics"], tasks: ["task", "tasks"],
  decisions: ["decision", "decisions"], concepts: ["concept", "concepts"], ideas: ["idea", "ideas"], plans: ["plan", "plans"], reviews: ["review", "reviews"],
  notes: ["note", "notes"],
  // the folder names the server counts (projects_service structure): shown as the words a person would use
  "implementation-plans": ["plan", "plans"], "adversarial-reviews": ["review", "reviews"] };
const kindName = (k: string, n: number): string => { const w = KB_KIND[k]; return w ? w[n === 1 ? 0 : 1] : k; };

/** All knowledge-base entries, with the three biggest kinds named. */
export function knowledgeGlance(counts: Record<string, number> | undefined): GlanceValue {
  const rows = Object.entries(counts ?? {}).filter(([, n]) => Number.isFinite(n) && n > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const total = rows.reduce((n, [, c]) => n + c, 0);
  if (total === 0) return { value: "0", detail: "nothing written yet" };
  return { value: String(total), detail: rows.slice(0, 3).map(([k, n]) => `${n} ${kindName(k, n)}`).join(" · ") };
}

export function videosGlance(rows: VideoRow[] | undefined): GlanceValue {
  const list = rows ?? [];
  if (list.length === 0) return { value: "0", detail: "no video yet" };
  const both = list.filter((v) => v.de && v.en).length;
  return { value: String(list.length), detail: both === list.length ? "all with German and English" : `${both} with German and English` };
}

/** Files that differ from the last commit. `null` when the folder is not a git repository (then there is nothing to say). */
export function changesGlance(c: Pick<ChangeList, "files" | "truncated" | "has_repository">): GlanceValue | null {
  if (!c.has_repository) return null;
  const n = c.files.length;
  if (n === 0) return { value: "0", detail: "everything is committed" };
  return { value: c.truncated ? `${n}+` : String(n), detail: n === 1 ? "file not committed yet" : "files not committed yet" };
}
