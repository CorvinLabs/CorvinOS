/** The scope decisions (pages/projects/scope.ts). A wrong label here puts two local "T-0001"s on one board. */
import { describe, expect, it } from "vitest";
import type { ProjectSummary } from "@/lib/api/projects";
import {
  graphScopes, mergeGraphs, parseScope, projectUnavailableReason, qualify, resolveScope, scopeLabel, scopeOptions, scopeParam,
  scopeList, selectableProjects,
} from "@/pages/projects/scope";

const p = (over: Partial<ProjectSummary>): ProjectSummary => ({ id: "alpha", path: "/p", template: "t@1", state: "ok", kb: true, ...over });

describe("which projects can be chosen", () => {
  it("only ok projects with a knowledge base", () => {
    const ids = selectableProjects([p({ id: "a" }), p({ id: "b", state: "missing" }), p({ id: "c", kb: false })]).map((x) => x.id);
    expect(ids).toEqual(["a"]);
    expect(selectableProjects(undefined)).toEqual([]);
  });
  it("an import that is not applied yet says so and cannot be chosen (ADR-2323 Decision 13)", () => {
    expect(projectUnavailableReason(p({ state: "pending", pending: "import", kb: undefined }))).toBe("Being imported");
    expect(projectUnavailableReason(p({ state: "pending" }))).toBe("still being created");
    expect(selectableProjects([p({ id: "i", state: "pending", pending: "import" })])).toEqual([]);
  });
  it("a project in the Trash is listed as unavailable with \"In Trash\" and links to the Trash (T-0405)", () => {
    const t = p({ id: "gone", title: "Gone", state: "trashed", storage: "trashed", kb: undefined, reason: "In Trash" });
    expect(projectUnavailableReason(t)).toBe("In Trash");
    const list = scopeList({ projects: [p({ id: "a" })], trashed: [t] });
    expect(list?.map((x) => x.id)).toEqual(["a", "gone"]);
    expect(selectableProjects(list).map((x) => x.id)).toEqual(["a"]);
    const opt = scopeOptions(list).find((o) => o.projectId === "gone");
    expect(opt).toMatchObject({ label: "Gone (In Trash)", disabled: true, reason: "In Trash", fixHref: "/app/projects?view=trash" });
    expect(scopeList(undefined)).toBeUndefined();
    expect(scopeList({ projects: [p({ id: "a" })] })?.map((x) => x.id)).toEqual(["a"]);
  });
});

describe("reading and writing ?scope=", () => {
  it("round-trips every kind", () => {
    for (const raw of ["bound", "all", "both", "project:alpha"]) {
      expect(scopeParam(parseScope(raw, ["alpha"], { kind: "bound" }))).toBe(raw);
    }
  });
  it("an unknown project id falls back instead of showing an empty board", () => {
    expect(parseScope("project:ghost", ["alpha"], { kind: "bound" })).toEqual({ kind: "bound" });
    expect(parseScope("project:ghost", ["alpha"], { kind: "all" })).toEqual({ kind: "all" });
  });
  it("garbage and a missing parameter use the fallback", () => {
    expect(parseScope(null, [], { kind: "all" })).toEqual({ kind: "all" });
    expect(parseScope("", [], { kind: "bound" })).toEqual({ kind: "bound" });
    expect(parseScope("nope", [], { kind: "both" })).toEqual({ kind: "both" });
  });
});

describe("labels", () => {
  it("a project's node is qualified with its id, the bound knowledge base's is not", () => {
    expect(qualify("T-0001", "alpha")).toBe("alpha/T-0001");
    expect(qualify("T-0001", null)).toBe("T-0001");
  });
  it("scope labels for the selector", () => {
    expect(scopeLabel({ kind: "project", id: "alpha" }, { alpha: "Plumbline" })).toBe("Plumbline");
    expect(scopeLabel({ kind: "project", id: "beta" }, {})).toBe("beta");
    expect(scopeLabel({ kind: "both" }, {})).toBe("All knowledge bases");
    expect(scopeLabel({ kind: "all" }, {})).toBe("All knowledge bases");
  });
});

describe("a 'both' view", () => {
  it("fetches the bound knowledge base and every project", () => {
    expect(graphScopes({ kind: "both" }, ["alpha", "beta"])).toEqual([null, "alpha", "beta"]);
    expect(graphScopes({ kind: "project", id: "alpha" }, ["alpha"])).toEqual(["alpha"]);
    expect(graphScopes({ kind: "bound" }, ["alpha"])).toEqual([null]);
  });
  it("two local ids that are equal stay distinct: labels are qualified, uids keep the nodes apart", () => {
    const merged = mergeGraphs([
      { projectId: null, entities: [{ id: "uid-1", label: "T-0001" }], relations: [{ from_id: "uid-1", to_id: "uid-1" }] },
      { projectId: "alpha", entities: [{ id: "uid-2", label: "T-0001" }], relations: [] },
    ]);
    expect(merged.entities.map((e) => e.label)).toEqual(["T-0001", "alpha/T-0001"]);
    expect(merged.entities.map((e) => e.scope)).toEqual([null, "alpha"]);
    expect(merged.entities.map((e) => e.id)).toEqual(["uid-1", "uid-2"]);       // no collision: ids unchanged
    expect(merged.entities).toHaveLength(2);
  });
  // REVIEW-0071 N6: a duplicated or imported-as-copy project carries its original's uids; its entities used to vanish
  it("the same uid in two parts is kept in both; each part's relations stay inside its own island", () => {
    const merged = mergeGraphs([
      { projectId: "orig", entities: [{ id: "u1", label: "A" }, { id: "u2", label: "B" }], relations: [{ from_id: "u1", to_id: "u2", relation: "r" }] },
      { projectId: "copy", entities: [{ id: "u1", label: "A" }, { id: "u2", label: "B" }, { id: "u3", label: "C" }],
        relations: [{ from_id: "u1", to_id: "u2", relation: "r" }, { from_id: "u3", to_id: "u1", relation: "r" }] },
    ]);
    expect(merged.entities).toHaveLength(5);
    expect(merged.entities.map((e) => e.id)).toEqual(["u1", "u2", "u1@copy", "u2@copy", "u3"]);
    expect(merged.entities.map((e) => e.uid)).toEqual(["u1", "u2", "u1", "u2", "u3"]);
    expect(merged.entities.map((e) => e.label)).toEqual(["orig/A", "orig/B", "copy/A", "copy/B", "copy/C"]);
    expect(merged.relations).toEqual([
      { from_id: "u1", to_id: "u2", relation: "r" },
      { from_id: "u1@copy", to_id: "u2@copy", relation: "r" },
      { from_id: "u3", to_id: "u1@copy", relation: "r" },
    ]);
    const scopeOf = new Map(merged.entities.map((e) => [e.id, e.scope]));
    for (const r of merged.relations) expect(scopeOf.get(r.from_id)).toBe(scopeOf.get(r.to_id));
    expect(new Set(merged.entities.map((e) => e.id)).size).toBe(merged.entities.length);
  });
  it("the bound knowledge base and a copy of it share uids: both kept, the count is the sum of the parts", () => {
    const part = (pid: string | null, n: number) => ({ projectId: pid, entities: Array.from({ length: n }, (_, i) => ({ id: `u${i}`, label: `E${i}` })), relations: [] });
    const merged = mergeGraphs([part(null, 3), part("t", 3), part("t-copy", 2)]);
    expect(merged.entities).toHaveLength(8);
    expect(merged.entities.filter((e) => e.scope === "t-copy").map((e) => e.id)).toEqual(["u0@t-copy", "u1@t-copy"]);
  });
  it("a dangling endpoint of a joined part never lands on another knowledge base's node; one part alone is unchanged", () => {
    const merged = mergeGraphs([
      { projectId: null, entities: [{ id: "x", label: "X" }], relations: [] },
      { projectId: "alpha", entities: [{ id: "y", label: "Y" }], relations: [{ from_id: "y", to_id: "x", relation: "r" }] },
    ]);
    expect(merged.relations).toEqual([{ from_id: "y", to_id: "x@alpha", relation: "r" }]);
    const one = mergeGraphs([{ projectId: "alpha", entities: [{ id: "y", label: "Y" }], relations: [{ from_id: "y", to_id: "gone", relation: "r" }] }]);
    expect(one.relations).toEqual([{ from_id: "y", to_id: "gone", relation: "r" }]);
    expect(one.entities.map((e) => e.id)).toEqual(["y"]);
  });
  it("the same uid twice within one knowledge base is one node", () => {
    const merged = mergeGraphs([{ projectId: "a", entities: [{ id: "u", label: "A" }, { id: "u", label: "A" }], relations: [] }]);
    expect(merged.entities).toHaveLength(1);
  });
});

describe("the switcher lists every project, and says why one cannot be chosen", () => {
  const list = [
    p({ id: "corvin", builtin: true, title: "Corvin" }),
    p({ id: "alpha", title: "Plumbline", features: { kb: true } }),
    p({ id: "demo", kb: false, features: { kb: false } }),
    p({ id: "fresh", kb: false, features: { kb: true } }),
    p({ id: "gone", state: "missing", reason: "directory /p/gone does not exist" }),
    p({ id: "bad", state: "invalid" }),
  ];
  it("names the reason a project is not selectable", () => {
    expect(projectUnavailableReason(list[1])).toBeNull();
    expect(projectUnavailableReason(list[2])).toBe("no knowledge base - switch it on in Features");
    expect(projectUnavailableReason(list[3])).toBe("knowledge base not created yet");
    expect(projectUnavailableReason(list[4])).toBe("folder missing: directory /p/gone does not exist");
    expect(projectUnavailableReason(list[5])).toBe("invalid project.yaml");
  });
  it("the same entries on both pages: All, Corvin, then every project (disabled ones with their reason)", () => {
    const o = scopeOptions(list);
    expect(o.map((x) => x.value)).toEqual(["all", "bound", "project:alpha", "project:demo", "project:fresh", "project:gone", "project:bad"]);
    expect(o.map((x) => x.label).slice(0, 3)).toEqual(["All knowledge bases", "Corvin", "Plumbline"]);
    expect(o.filter((x) => x.disabled).map((x) => x.projectId)).toEqual(["demo", "fresh", "gone", "bad"]);
    const demo = o.find((x) => x.projectId === "demo")!;
    expect(demo.label).toBe("demo (no knowledge base - switch it on in Features)");
    expect(demo.fixHref).toBe("/app/projects/demo?tab=features");
    expect(o.find((x) => x.projectId === "gone")!.fixHref).toBe("/app/projects/gone");
    expect(selectableProjects(list).map((x) => x.id)).toEqual(["alpha"]);
  });
  it("reads ?scope= per page: defaults, the former 'both', unknown and unavailable projects", () => {
    expect(resolveScope(null, list, "tasks")).toEqual({ scope: { kind: "all" }, note: null, pending: false });
    expect(resolveScope(null, list, "graph")).toEqual({ scope: { kind: "bound" }, note: null, pending: false });
    expect(resolveScope("both", list, "graph").scope).toEqual({ kind: "all" });
    expect(resolveScope("project:alpha", list, "graph").scope).toEqual({ kind: "project", id: "alpha" });
    expect(resolveScope("project:corvin", list, "tasks").scope).toEqual({ kind: "bound" });
    const ghost = resolveScope("project:ghost", list, "tasks");
    expect(ghost.scope).toEqual({ kind: "all" });
    expect(ghost.note).toBe('There is no project "ghost". Showing All knowledge bases instead.');
    const demo = resolveScope("project:demo", list, "graph");
    expect(demo.scope).toEqual({ kind: "bound" });
    expect(demo.note).toBe("demo cannot be shown: no knowledge base - switch it on in Features. Showing Corvin instead.");
  });
  it("a shared project link waits for the project list instead of showing the default first", () => {
    expect(resolveScope("project:alpha", undefined, "tasks")).toEqual({ scope: { kind: "project", id: "alpha" }, note: null, pending: true });
    expect(resolveScope("bound", undefined, "tasks").pending).toBe(false);
  });
});

describe("an unfinished import is named as such in the switcher (REVIEW-0080 N25)", () => {
  const p = { id: "x", path: "/x", template: "t@1", state: "pending", pending: "import" } as const;
  it("running: being imported; stale: unfinished, with where the repair is", () => {
    expect(projectUnavailableReason({ ...p } as never)).toBe("Being imported");
    expect(projectUnavailableReason({ ...p, stale: true } as never)).toBe("import unfinished - Repair is on the project's page");
  });
});
