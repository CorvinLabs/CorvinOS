/**
 * Projects page - the mappings from data to what is drawn, kept out of the component so a wrong
 * mapping (a missing project drawn as healthy, a feature id shown raw) is caught by a unit test and
 * not by a user.
 */
import type { ProjectState, ProjectSummary, TemplateCatalogue } from "@/lib/api/projects";

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
