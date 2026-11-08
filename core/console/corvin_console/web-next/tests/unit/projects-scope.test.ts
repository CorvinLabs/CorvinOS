/** The scope decisions (pages/projects/scope.ts). A wrong label here puts two local "T-0001"s on one board. */
import { describe, expect, it } from "vitest";
import type { ProjectSummary } from "@/lib/api/projects";
import { graphScopes, mergeGraphs, parseScope, qualify, scopeLabel, scopeParam, selectableProjects } from "@/pages/projects/scope";

const p = (over: Partial<ProjectSummary>): ProjectSummary => ({ id: "alpha", path: "/p", template: "t@1", state: "ok", kb: true, ...over });

describe("which projects can be chosen", () => {
  it("only ok projects with a knowledge base", () => {
    const ids = selectableProjects([p({ id: "a" }), p({ id: "b", state: "missing" }), p({ id: "c", kb: false })]).map((x) => x.id);
    expect(ids).toEqual(["a"]);
    expect(selectableProjects(undefined)).toEqual([]);
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
    expect(scopeLabel({ kind: "both" }, {})).toBe("Corvin + projects");
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
      { projectId: null, entities: [{ id: "uid-1", label: "T-0001" }], relations: [{ from: "uid-1", to: "uid-1" }] },
      { projectId: "alpha", entities: [{ id: "uid-2", label: "T-0001" }], relations: [] },
    ]);
    expect(merged.entities.map((e) => e.label)).toEqual(["T-0001", "alpha/T-0001"]);
    expect(merged.entities.map((e) => e.scope)).toEqual([null, "alpha"]);
    expect(merged.entities).toHaveLength(2);
  });
  it("the same uid in two parts is kept once; relations are never invented across parts", () => {
    const merged = mergeGraphs([
      { projectId: null, entities: [{ id: "u", label: "A" }], relations: [{ from: "u", to: "u" }] },
      { projectId: "alpha", entities: [{ id: "u", label: "A" }], relations: [] },
    ]);
    expect(merged.entities).toHaveLength(1);
    expect(merged.relations).toEqual([{ from: "u", to: "u" }]);
  });
});
