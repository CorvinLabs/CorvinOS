/**
 * Projects page - the mappings from data to what is drawn, kept out of the component so a wrong
 * mapping (a missing project drawn as healthy, a feature id shown raw) is caught by a unit test and
 * not by a user.
 */
import type { DefaultStorage, ProjectState, ProjectSummary, TemplateCatalogue } from "@/lib/api/projects";

export type Tone = "ok" | "warn" | "bad";

export const STATE_META: Record<ProjectState, { label: string; tone: Tone }> = {
  ok: { label: "Ready", tone: "ok" },
  missing: { label: "Directory missing", tone: "bad" },
  invalid: { label: "project.yaml not usable", tone: "bad" },
  pending: { label: "Setting up", tone: "warn" },
};

/** Display names of the template fragments the UI knows; any other id is shown as its own words. */
export const FEATURE_LABEL: Record<string, string> = {
  core: "Project",
  kb: "Knowledge base",
  tasks: "Tasks",
  graph: "Knowledge graph",
  gates: "Quality gates",
  claude: "Claude Code rules",
  ldd: "LDD",
  videos: "Videos",
  video_producer: "Video Producer",
  directory: "Peer directory",
  dashboard: "Overview",
  experiments: "Experiments",
  acs: "Agentic compute",
};

export function featureLabel(id: string): string {
  return FEATURE_LABEL[id] ?? id.replace(/_/g, " ");
}

/** `core` is always on and says nothing; it is not drawn as a chip. */
export function featureChips(p: ProjectSummary): string[] {
  return (p.active ?? []).filter((f) => f !== "core").map(featureLabel);
}

/** Features the template offers that this project has switched off (for the "off" hint). */
export function offFeatures(p: ProjectSummary): string[] {
  return Object.entries(p.features ?? {}).filter(([id, on]) => !on && id !== "core").map(([id]) => featureLabel(id));
}

export function stateOf(p: ProjectSummary): { label: string; tone: Tone; detail: string } {
  const m = STATE_META[p.state] ?? { label: String(p.state), tone: "warn" as Tone };
  return { ...m, detail: p.state === "ok" ? "" : (p.reason ?? "") };
}

/** "plugin:buildin-knowledge_management-project_templates" is never shown; "template@1" is. */
export function templateLabel(template: string): string {
  return template ? template.replace("@", " v") : "no template";
}

/** The empty state says WHAT is missing and where it comes from, never a stub button. */
export function emptyState(cat: TemplateCatalogue | undefined): { headline: string; body: string; needsPlugin: boolean } {
  if (cat && !cat.available) {
    return {
      headline: "Project templates are not enabled",
      body: cat.reason ?? "The project templates plugin is not enabled for this tenant.",
      needsPlugin: true,
    };
  }
  return {
    headline: "No projects yet",
    body: "A project is a folder you own with its own knowledge base, Claude Code rules and the features you pick. "
      + "It stays a plain folder: removing it from this list never deletes a file.",
    needsPlugin: false,
  };
}

/** Sort: Corvin itself first, then the ones that need attention, then by title. */
export function sortProjects(list: ProjectSummary[]): ProjectSummary[] {
  const rank = (p: ProjectSummary) => (p.builtin ? -1 : p.state === "ok" ? 1 : 0);
  return [...list].sort((a, b) => rank(a) - rank(b) || (a.title ?? a.id).localeCompare(b.title ?? b.id));
}

// ── where a project lives (T-0388, CONCEPT-0131 § C3) ──────────────────────────────────────────────

/** `root` is an own folder strictly below the projects folder the user chose; the server reports only managed / own / builtin. */
export type WhereKind = "managed" | "own" | "root" | "builtin";
export interface WhereContext { projects_root?: string; projects_root_set?: boolean }

export const WHERE_LABEL: Record<Exclude<WhereKind, "builtin">, string> = {
  managed: "Corvin workspace",
  own: "Own folder",
  root: "Projects folder",
};
/** The order of the filter and of the badges' explanation. */
export const WHERE_ORDER: Exclude<WhereKind, "builtin">[] = ["managed", "own", "root"];

function looksWindows(p: string): boolean {
  return /^[A-Za-z]:/.test(p) || p.includes("\\");
}

/** `path` lies strictly below `root` (a separator-aware prefix test; case-insensitive for Windows paths). */
export function isUnder(path: string, root: string): boolean {
  if (!path || !root) return false;
  const win = looksWindows(path) || looksWindows(root);
  const norm = (s: string) => {
    let x = win ? s.replace(/\//g, "\\").toLowerCase() : s;
    const sep = win ? "\\" : "/";
    while (x.length > 1 && x.endsWith(sep) && !/^[a-z]:\\$/.test(x)) x = x.slice(0, -1);
    return x;
  };
  const p = norm(path);
  const r = norm(root);
  const sep = win ? "\\" : "/";
  return p !== r && p.startsWith(r.endsWith(sep) ? r : r + sep);
}

export function whereKind(p: ProjectSummary, ctx: WhereContext = {}): WhereKind {
  if (p.builtin || p.storage === "builtin") return "builtin";
  if (p.storage === "managed") return "managed";
  if (ctx.projects_root_set && ctx.projects_root && isUnder(p.path, ctx.projects_root)) return "root";
  return "own";
}

/** The card's "Where" row: a badge, the shortened path, the full path for hover/long-press. */
export function whereLabel(p: ProjectSummary, ctx: WhereContext = {}): { kind: WhereKind; badge: string; text: string; title: string } {
  const kind = whereKind(p, ctx);
  if (kind === "builtin") return { kind, badge: "", text: "Corvin's knowledge base", title: p.path };
  const shown = p.display_path || p.path;
  // "Corvin workspace › alpha": the badge already says "Corvin workspace", the row keeps "› alpha"
  const text = kind === "managed" && shown.startsWith(`${WHERE_LABEL.managed} `) ? shown.slice(WHERE_LABEL.managed.length + 1) : shown;
  return { kind, badge: WHERE_LABEL[kind], text, title: p.path };
}

/** "all" keeps every project; a kind keeps only the user's projects of that kind (Corvin itself is never filtered in). */
export function whereFilter(list: ProjectSummary[], kind: WhereKind | "all", ctx: WhereContext = {}): ProjectSummary[] {
  return kind === "all" ? list : list.filter((p) => whereKind(p, ctx) === kind);
}

/** The kinds the user's own projects actually have, so the filter appears only when it can separate something. */
export function presentWhere(list: ProjectSummary[], ctx: WhereContext = {}): Exclude<WhereKind, "builtin">[] {
  const kinds = new Set(list.map((p) => whereKind(p, ctx)));
  return WHERE_ORDER.filter((k) => kinds.has(k));
}

/** The header sentence. An older server without `default_storage` creates under its projects folder. */
export function defaultPlaceSentence(l: { default_storage?: DefaultStorage; projects_root: string }): string {
  return l.default_storage === "managed" ? "New projects go to the Corvin workspace." : `New projects go to ${l.projects_root}.`;
}
