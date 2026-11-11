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
  // an import nobody applies any more is not "being imported": its repair is on the project's page (REVIEW-0080 N25)
  if (p.state === "pending") return p.pending === "import" ? (p.stale ? "import unfinished - Repair is on the project's page" : "Being imported") : "still being created";
  if (p.state === "trashed") return "In Trash";
  if (p.state !== "ok") return p.reason ?? p.state;
  // the knowledge-base switch is in the project's Features tab (the Settings tab has none: REVIEW-0067 M2)
  if (p.features && p.features.kb !== true) return NO_KB_REASON;
  if (p.kb !== true) return p.features?.kb === true ? "knowledge base not created yet" : NO_KB_REASON;
  // the board's and the graph's own rule, reported by the server: offering it would end in their refusal (REVIEW-0081 N28)
  if (p.kb_problem) return `knowledge base cannot be read: ${p.kb_problem}`;
  return null;
}

export const NO_KB_REASON = "no knowledge base - switch it on in Features";

/**
 * How the "bound" scope is shown. Normally it IS the built-in Corvin project (the board's knowledge base) and is called "Corvin".
 * The Knowledge Graph has its own Repository setting; when that points somewhere else, `boundLabel` names that repository and the
 * built-in project is listed on its own, so one name never stands for two knowledge bases (REVIEW-0067 M4).
 */
export interface ScopeOpts {
  boundLabel?: string | null;
  /** The page does not know yet what the bound scope reads (the graph's setting is loading): it is shown as "…", never as "Corvin". */
  pendingBound?: boolean;
}

/** The bound scope's display name under these options. */
export function boundName(opts: ScopeOpts = {}): string {
  return scopeLabel({ kind: "bound" }, {}, opts.boundLabel ?? (opts.pendingBound ? "…" : null));
}

const trimSlash = (x: string): string => x.replace(/[\\/]+$/, "");

/**
 * The name of the graph's repository when it is NOT the built-in project's folder, else null (same folder, or either unknown).
 * The folder name is used; a generic last folder (docs, knowledge, kb) keeps its parent: "LLM_lie_detector/docs".
 */
export function boundRepoLabel(repoPath: string | null | undefined, builtinPath: string | null | undefined): string | null {
  const repo = trimSlash((repoPath ?? "").trim());
  const own = trimSlash((builtinPath ?? "").trim());
  if (!repo || !own) return null;
  if (repo === own) return null;
  if (repo.startsWith("~") && own.endsWith(repo.slice(1))) return null;     // "~/x" is the same folder as "/home/u/x"
  const parts = repo.split(/[\\/]+/).filter(Boolean);
  const last = parts[parts.length - 1] ?? repo;
  return ["docs", "knowledge", "kb"].includes(last.toLowerCase()) && parts.length > 1 ? `${parts[parts.length - 2]}/${last}` : last;
}

/** The list the switcher works on: every project plus the ones in the Trash (listed, never selectable: "In Trash"). */
export function scopeList(data: { projects?: ProjectSummary[]; trashed?: ProjectSummary[] } | undefined): ProjectSummary[] | undefined {
  if (!data) return undefined;
  return [...(data.projects ?? []), ...(data.trashed ?? [])];
}

/** Every project the switcher lists. The built-in Corvin project IS the "bound" scope, unless the bound scope names another repository. */
export function listedProjects(list: ProjectSummary[] | undefined, opts: ScopeOpts = {}): ProjectSummary[] {
  return (list ?? []).filter((p) => !p.builtin || !!opts.boundLabel);
}

/** Projects the scope selector may offer: ok and with the knowledge base on. */
export function selectableProjects(list: ProjectSummary[] | undefined, opts: ScopeOpts = {}): ProjectSummary[] {
  return listedProjects(list, opts).filter((p) => projectUnavailableReason(p) === null);
}

/** The two pages that switch by scope. Tasks starts on every knowledge base, the graph on Corvin's own. */
export type ScopePage = "tasks" | "graph";
export const DEFAULT_SCOPE: Record<ScopePage, Scope> = { tasks: { kind: "all" }, graph: { kind: "bound" } };

export interface ScopeOption {
  value: string; label: string; title: string; disabled: boolean; reason: string | null; projectId: string | null;
  /** Where an unavailable project is fixed: its Features tab (knowledge base off), else its page. */
  fixHref: string | null;
}

/** The switcher's entries, identical on both pages: All knowledge bases, Corvin, then every project. */
export function scopeOptions(list: ProjectSummary[] | undefined, opts: ScopeOpts = {}): ScopeOption[] {
  const bound = boundName(opts);
  const fixed: ScopeOption[] = [
    { value: "all", label: scopeLabel({ kind: "all" }, {}), title: scopeLabel({ kind: "all" }, {}), disabled: false, reason: null, projectId: null, fixHref: null },
    { value: "bound", label: bound, title: bound, disabled: false, reason: null, projectId: null, fixHref: null },
  ];
  return [...fixed, ...listedProjects(list, opts).map((p) => {
    const reason = projectUnavailableReason(p);
    const title = p.title || p.id;
    const page = `/app/projects/${encodeURIComponent(p.id)}`;
    return { value: `project:${p.id}`, label: reason ? `${title} (${reason})` : title, title, disabled: reason !== null, reason,
      projectId: p.id, fixHref: reason === null ? null : p.state === "ok" ? `${page}?tab=features` : p.state === "trashed" ? "/app/projects?view=trash" : page };
  })];
}

/**
 * Reads ?scope= for a page: "both" (the graph's former word) is "all"; a project that is listed but cannot be
 * shown, or that does not exist, falls back to the page's default and says why (`note`). While the project list
 * is still loading (`list` undefined) a project id is kept, so a shared link does not flash the default first.
 */
export function resolveScope(raw: string | null, list: ProjectSummary[] | undefined, page: ScopePage, opts: ScopeOpts = {}):
  { scope: Scope; note: string | null; pending: boolean } {
  const fallback = DEFAULT_SCOPE[page];
  const fbLabel = fallback.kind === "bound" ? boundName(opts) : scopeLabel(fallback, {});
  if (raw === "both") return { scope: { kind: "all" }, note: null, pending: false };
  // the built-in project IS the bound scope - unless the bound scope names another repository (then it is a scope of its own)
  if (raw === "project:corvin" && !opts.boundLabel) return { scope: { kind: "bound" }, note: null, pending: false };
  if (raw?.startsWith("project:") && list === undefined) return { scope: { kind: "project", id: raw.slice(8) }, note: null, pending: true };
  const scope = parseScope(raw, selectableProjects(list, opts).map((p) => p.id), fallback);
  if (scope.kind === "both") return { scope: { kind: "all" }, note: null, pending: false };
  if (raw?.startsWith("project:") && scope.kind !== "project") {
    const id = raw.slice(8);
    const p = listedProjects(list, opts).find((x) => x.id === id);
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

/** The node id a project's entity gets in a joined view when its uid is already taken by another knowledge base. */
export function partNodeId(uid: string, projectId: string | null): string {
  return projectId ? `${uid}@${projectId}` : uid;
}

/**
 * Joins the parts of a "both" view. Node ids are uids, which are unique within one knowledge base but not across them: a
 * duplicated or imported-as-copy project carries its original's knowledge base verbatim, uids included. The first part to
 * use a uid keeps it as the node id (so a shared ?node=<uid> link still opens that node); a later part's node with a taken uid
 * becomes ``<uid>@<project>`` (``uid`` keeps the raw one for the document request) and that part's relation endpoints are
 * remapped the same way, so every knowledge base stays its own island and no entity is dropped (REVIEW-0071 N6). Each node
 * keeps its scope and carries a qualified label.
 */
export function mergeGraphs<E extends { id: string; label: string }, R extends { from_id: string; to_id: string }>(
  parts: GraphPart<E, R>[],
): { entities: (E & { scope: string | null; uid: string })[]; relations: R[] } {
  const taken = new Set<string>();
  const entities: (E & { scope: string | null; uid: string })[] = [];
  const relations: R[] = [];
  for (const part of parts) {
    const local = new Map<string, string>();       // this part's uid -> its node id in the joined graph
    for (const e of part.entities) {
      if (local.has(e.id)) continue;               // the same uid twice in ONE knowledge base: one node
      let id = e.id;
      if (taken.has(id)) id = partNodeId(e.id, part.projectId);
      if (taken.has(id)) continue;                 // cannot happen for distinct project ids; never two nodes with one id
      taken.add(id);
      local.set(e.id, id);
      entities.push({ ...e, id, uid: e.id, label: qualify(e.label, part.projectId), scope: part.projectId });
    }
    // an endpoint outside this part (a dangling link) gets a key no node has, so it can never land on another knowledge base's
    // node; in a single-part view there is no other knowledge base and it stays as it was
    const end = (x: string) => local.get(x) ?? (parts.length > 1 ? `${x}@${part.projectId ?? ""}` : x);
    for (const r of part.relations) relations.push({ ...r, from_id: end(r.from_id), to_id: end(r.to_id) });
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
export function scopeLabel(s: Scope, titles: Record<string, string>, boundLabel?: string | null): string {
  switch (s.kind) {
    case "all": return "All knowledge bases";
    case "bound": return boundLabel || "Corvin";
    case "both": return "All knowledge bases";
    case "project": return titles[s.id] ?? s.id;
  }
}
