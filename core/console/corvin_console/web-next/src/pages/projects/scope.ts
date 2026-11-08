/**
 * Scope: which knowledge base a view shows (T-0360, ADR-2316). Pure, so the decisions are unit-tested:
 * how the URL is read and written, which project ids are real, how a second knowledge base is labelled so two
 * local ids (both start at T-0001) cannot be mistaken for one another.
 */
import type { ProjectSummary } from "@/lib/api/projects";

/** "bound" = the tenant's own board/graph; "project" = one project's KB; "all" = bound plus every project; "both" is the graph's word for it. */
export type Scope = { kind: "all" } | { kind: "bound" } | { kind: "project"; id: string } | { kind: "both" };

/** Projects the scope selector may offer: ok and with the knowledge base on. */
export function selectableProjects(list: ProjectSummary[] | undefined): ProjectSummary[] {
  return (list ?? []).filter((p) => p.state === "ok" && p.kb === true);
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
    case "all": return "All";
    case "bound": return "Corvin";
    case "both": return "Corvin + projects";
    case "project": return titles[s.id] ?? s.id;
  }
}
