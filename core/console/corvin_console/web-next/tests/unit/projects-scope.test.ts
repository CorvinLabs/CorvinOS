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
