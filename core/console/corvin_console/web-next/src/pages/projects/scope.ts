/**
 * Scope: which knowledge base a view shows (T-0360, ADR-2316). Pure, so the decisions are unit-tested:
 * how the URL is read and written, which project ids are real, how a second knowledge base is labelled so two
 * local ids (both start at T-0001) cannot be mistaken for one another.
 */
import type { ProjectSummary } from "@/lib/api/projects";

/** "bound" = the tenant's own board/graph; "project" = one project's KB; "all" = bound plus every project; "both" is the graph's word for it. */
export type Scope = { kind: "all" } | { kind: "bound" } | { kind: "project"; id: string } | { kind: "both" };

/**
 * Why a project cannot be chosen as a scope, or null when it can. Every project is listed in the switcher; one
 * that cannot be shown is listed disabled with this reason, so the operator sees that it exists and what to do.
 */
export function projectUnavailableReason(p: ProjectSummary): string | null {
  if (p.state === "missing") return p.reason ? `folder missing: ${p.reason}` : "folder missing";
  if (p.state === "invalid") return p.reason ? `invalid: ${p.reason}` : "invalid project.yaml";
  if (p.state === "pending") return p.pending === "import" ? "Being imported" : "still being created";
  if (p.state !== "ok") return p.reason ?? p.state;
  if (p.features && p.features.kb !== true) return "no knowledge base - switch it on in Settings";
  if (p.kb !== true) return p.features?.kb === true ? "knowledge base not created yet" : "no knowledge base - switch it on in Settings";
  return null;
}

/** Every project the switcher lists (the built-in Corvin project IS the "bound" scope, labelled "Corvin"). */
export function listedProjects(list: ProjectSummary[] | undefined): ProjectSummary[] {
  return (list ?? []).filter((p) => !p.builtin);
}

/** Projects the scope selector may offer: ok and with the knowledge base on. */
export function selectableProjects(list: ProjectSummary[] | undefined): ProjectSummary[] {
  return listedProjects(list).filter((p) => projectUnavailableReason(p) === null);
}

/** The two pages that switch by scope. Tasks starts on every knowledge base, the graph on Corvin's own. */
export type ScopePage = "tasks" | "graph";
export const DEFAULT_SCOPE: Record<ScopePage, Scope> = { tasks: { kind: "all" }, graph: { kind: "bound" } };

export interface ScopeOption {
  value: string; label: string; title: string; disabled: boolean; reason: string | null; projectId: string | null;
  /** Where an unavailable project is fixed: its Settings tab (knowledge base off), else its page. */
  fixHref: string | null;
}

/** The switcher's entries, identical on both pages: All knowledge bases, Corvin, then every project. */
export function scopeOptions(list: ProjectSummary[] | undefined): ScopeOption[] {
  const fixed: ScopeOption[] = [
    { value: "all", label: scopeLabel({ kind: "all" }, {}), title: scopeLabel({ kind: "all" }, {}), disabled: false, reason: null, projectId: null, fixHref: null },
    { value: "bound", label: scopeLabel({ kind: "bound" }, {}), title: scopeLabel({ kind: "bound" }, {}), disabled: false, reason: null, projectId: null, fixHref: null },
  ];
  return [...fixed, ...listedProjects(list).map((p) => {
    const reason = projectUnavailableReason(p);
    const title = p.title || p.id;
    const page = `/app/projects/${encodeURIComponent(p.id)}`;
    return { value: `project:${p.id}`, label: reason ? `${title} (${reason})` : title, title, disabled: reason !== null, reason,
      projectId: p.id, fixHref: reason === null ? null : p.state === "ok" ? `${page}?tab=settings` : page };
  })];
}

/**
 * Reads ?scope= for a page: "both" (the graph's former word) is "all"; a project that is listed but cannot be
 * shown, or that does not exist, falls back to the page's default and says why (`note`). While the project list
 * is still loading (`list` undefined) a project id is kept, so a shared link does not flash the default first.
 */
export function resolveScope(raw: string | null, list: ProjectSummary[] | undefined, page: ScopePage):
  { scope: Scope; note: string | null; pending: boolean } {
  const fallback = DEFAULT_SCOPE[page];
  const fbLabel = scopeLabel(fallback, {});
  if (raw === "both") return { scope: { kind: "all" }, note: null, pending: false };
  if (raw === "project:corvin") return { scope: { kind: "bound" }, note: null, pending: false };   // the built-in project IS Corvin
  if (raw?.startsWith("project:") && list === undefined) return { scope: { kind: "project", id: raw.slice(8) }, note: null, pending: true };
  const scope = parseScope(raw, selectableProjects(list).map((p) => p.id), fallback);
  if (scope.kind === "both") return { scope: { kind: "all" }, note: null, pending: false };
  if (raw?.startsWith("project:") && scope.kind !== "project") {
    const id = raw.slice(8);
    const p = listedProjects(list).find((x) => x.id === id);
    const note = p
      ? `${p.title || p.id} cannot be shown: ${projectUnavailableReason(p) ?? "unavailable"}. Showing ${fbLabel} instead.`
      : `There is no project "${id}". Showing ${fbLabel} instead.`;
    return { scope, note, pending: false };
  }
  return { scope, note: null, pending: false };
}

/** Reads ?scope=. An unknown project id falls back to the default rather than showing nothing silently. */
export function parseScope(raw: string | null, projectIds: string[], fallback: Scope): Scope {
  if (raw === null || raw === "") return fallback;
  if (raw === "bound") return { kind: "bound" };
  if (raw === "all") return { kind: "all" };
  if (raw === "both") return { kind: "both" };
  if (raw.startsWith("project:")) {
    const id = raw.slice("project:".length);
    return projectIds.includes(id) ? { kind: "project", id } : fallback;
  }
  return fallback;
}

/** The value written to ?scope= (and the value the <select> holds). */
export function scopeParam(s: Scope): string {
  switch (s.kind) {
    case "all": return "all";
    case "bound": return "bound";
    case "both": return "both";
    case "project": return `project:${s.id}`;
  }
}

/** The label a graph node carries: a project's nodes are qualified with the project id. */
export function qualify(label: string, projectId: string | null): string {
  return projectId ? `${projectId}/${label}` : label;
}

export interface GraphPart<E, R> {
  projectId: string | null;      // null = the bound knowledge base
  entities: E[];
  relations: R[];
}

/**
 * Joins the parts of a "both" view. Node ids are uids (unique across knowledge bases); each node keeps its
 * scope and carries a qualified label. Relations never cross knowledge bases, so the parts form separate islands.
 */
export function mergeGraphs<E extends { id: string; label: string }, R>(
  parts: GraphPart<E, R>[],
): { entities: (E & { scope: string | null })[]; relations: R[] } {
  const seen = new Set<string>();
  const entities: (E & { scope: string | null })[] = [];
  const relations: R[] = [];
  for (const part of parts) {
    for (const e of part.entities) {
      if (seen.has(e.id)) continue;               // the same uid in two parts: keep the first
      seen.add(e.id);
      entities.push({ ...e, label: qualify(e.label, part.projectId), scope: part.projectId });
    }
    relations.push(...part.relations);
  }
  return { entities, relations };
}

/** The scopes a graph request must fetch for a given choice (one request per knowledge base). */
export function graphScopes(s: Scope, projectIds: string[]): (string | null)[] {
  switch (s.kind) {
    case "bound": return [null];
    case "project": return [s.id];
    case "both": return [null, ...projectIds];
    case "all": return [null, ...projectIds];
  }
}

/** Human label of a scope for the selector and the chip. */
export function scopeLabel(s: Scope, titles: Record<string, string>): string {
  switch (s.kind) {
    case "all": return "All knowledge bases";
    case "bound": return "Corvin";
    case "both": return "All knowledge bases";
    case "project": return titles[s.id] ?? s.id;
  }
}
