/**
 * The Projects page's mappings (pages/projects/encodings.ts). A wrong mapping - a missing project drawn
 * as healthy, a raw feature id, a raw plugin index id - is invisible in a screenshot until a tenant hits it.
 */
import { describe, expect, it } from "vitest";
import type { ProjectSummary, TemplateCatalogue } from "@/lib/api/projects";
import { defaultPlaceSentence, emptyState, featureChips, featureLabel, offFeatures, presentWhere, sortProjects, stateOf, templateLabel, whereFilter, whereKind, whereLabel } from "@/pages/projects/encodings";

const ok: ProjectSummary = {
  id: "plumbline", path: "/p/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline",
  docs_mode: "nested", active: ["core", "kb", "video_producer", "tasks"],
  features: { core: true, kb: true, video_producer: true, tasks: true, acs: false, experiments: false },
};

describe("project state", () => {
  it("never draws a missing or unusable project as ready", () => {
    expect(stateOf(ok)).toMatchObject({ label: "Ready", tone: "ok", detail: "" });
    expect(stateOf({ ...ok, state: "missing", reason: "project.yaml is missing or unreadable" }))
      .toMatchObject({ label: "Directory missing", tone: "bad", detail: "project.yaml is missing or unreadable" });
    expect(stateOf({ ...ok, state: "invalid", reason: "x" }).tone).toBe("bad");
  });
  it("an unknown state is shown as itself, in a cautious tone - not as ready", () => {
    const s = stateOf({ ...ok, state: "weird" as never });
    expect(s.label).toBe("weird");
    expect(s.tone).toBe("warn");
  });
});

describe("features", () => {
  it("chips are readable names, core is not a chip, order follows the manifest", () => {
    expect(featureChips(ok)).toEqual(["Knowledge base", "Video Producer", "Tasks"]);
  });
  it("an unknown feature id is shown as words, never as a raw id with underscores", () => {
    expect(featureLabel("some_new_feature")).toBe("some new feature");
  });
  it("lists what the template offers but the project switched off, without core", () => {
    expect(offFeatures(ok)).toEqual(["Agentic compute", "Experiments"]);
    expect(offFeatures({ ...ok, features: undefined })).toEqual([]);
  });
  it("a project without a manifest has no chips", () => {
    expect(featureChips({ ...ok, state: "missing", active: undefined })).toEqual([]);
  });
});

describe("template label", () => {
  it("shows the template and its version; an absent template says so", () => {
    expect(templateLabel("video-engineering@1")).toBe("video engineering");
    expect(templateLabel("video-engineering@1", [{ id: "video-engineering", title: "Video engineering" }])).toBe("Video engineering");
    expect(templateLabel("adopted@1")).toBe("Adopted folder");
    expect(templateLabel("builtin@1")).toBe("Built in");
    expect(templateLabel("research-compute@2", [{ id: "research-compute", title: "Research and experiments" }])).toBe("Research and experiments v2");
    expect(templateLabel("")).toBe("no template");
  });
});

describe("empty state", () => {
  const cat = (over: Partial<TemplateCatalogue>): TemplateCatalogue => ({ available: true, templates: [], features: [], ...over });
  it("explains the folder model and never promises a deletion", () => {
    const e = emptyState(cat({}));
    expect(e.headline).toBe("No projects yet");
    expect(e.body).toMatch(/never deletes a file/);
    expect(e.needsPlugin).toBe(false);
  });
  it("names the missing plugin and the reason the server gave", () => {
    const e = emptyState(cat({ available: false, reason: "the project templates plugin is absent for this tenant - enable it in the Marketplace" }));
    expect(e.needsPlugin).toBe(true);
    expect(e.body).toMatch(/enable it in the Marketplace/);
  });
  it("claims nothing before the catalogue has answered", () => {
    expect(emptyState(undefined).needsPlugin).toBe(false);
  });
  it("never shows an ADR id or a plugin index id", () => {
    for (const e of [emptyState(undefined), emptyState(cat({ available: false, reason: "r" }))]) {
      expect(e.headline + e.body).not.toMatch(/ADR-\d|plugin:buildin/);
    }
  });
});

describe("sorting", () => {
  it("puts projects that need attention first, then orders by title", () => {
    const l = sortProjects([
      { ...ok, id: "b", title: "Beta" }, { ...ok, id: "m", title: "Gone", state: "missing" },
      { ...ok, id: "a", title: "Alpha" },
    ]);
    expect(l.map((p) => p.id)).toEqual(["m", "a", "b"]);
  });
});

import { selectableProjects } from "@/pages/projects/scope";
describe("the built-in Corvin project (CONCEPT-0129)", () => {
  const mk = (id: string, over: Record<string, unknown> = {}) => ({ id, path: "/p/" + id, template: "x@1", state: "ok", title: id, kb: true, ...over }) as never;
  it("is always first, even before a project that needs attention", () => {
    const list = [mk("zeta"), mk("alpha", { state: "missing" }), mk("corvin", { builtin: true, title: "Corvin" })];
    expect(sortProjects(list).map((p) => p.id)).toEqual(["corvin", "alpha", "zeta"]);
  });
  it("is not a second scope entry: the 'bound' scope is Corvin", () => {
    const list = [mk("corvin", { builtin: true }), mk("alpha")];
    expect(selectableProjects(list).map((p) => p.id)).toEqual(["alpha"]);
  });
});

describe("where a project lives (T-0388)", () => {
  const ctx = { projects_root: "/home/u/work", projects_root_set: true };
  const managed: ProjectSummary = { ...ok, id: "alpha", path: "/home/u/.local/share/corvin/projects/w-0123456789/alpha", storage: "managed",
    display_path: "Corvin workspace \u203a alpha" };
  const own: ProjectSummary = { ...ok, id: "beta", path: "/home/u/code/beta", storage: "own", display_path: "~/code/beta" };
  const rooted: ProjectSummary = { ...ok, id: "gamma", path: "/home/u/work/gamma", storage: "own", display_path: "~/work/gamma" };
  const corvin: ProjectSummary = { ...ok, id: "corvin", path: "/x/kb", builtin: true, storage: "builtin", display_path: "/x/kb" };

  it("labels the three kinds and the built-in project; the full path is the title", () => {
    expect(whereLabel(managed, ctx)).toEqual({ kind: "managed", badge: "Corvin workspace", text: "\u203a alpha", title: managed.path });
    expect(whereLabel(own, ctx)).toEqual({ kind: "own", badge: "Own folder", text: "~/code/beta", title: own.path });
    expect(whereLabel(rooted, ctx)).toEqual({ kind: "root", badge: "Projects folder", text: "~/work/gamma", title: rooted.path });
    expect(whereLabel(corvin, ctx)).toEqual({ kind: "builtin", badge: "", text: "Corvin's knowledge base", title: "/x/kb" });
  });
  it("an own folder is a projects-folder project only while a projects folder is set, and only strictly below it", () => {
    expect(whereKind(rooted, { ...ctx, projects_root_set: false })).toBe("own");
    expect(whereKind({ ...rooted, path: "/home/u/work" }, ctx)).toBe("own");
    expect(whereKind({ ...rooted, path: "/home/u/workshop/x" }, ctx)).toBe("own");
    expect(whereKind({ ...rooted, path: "D:\\Work\\gamma" }, { projects_root: "d:\\work", projects_root_set: true })).toBe("root");
    expect(whereKind({ ...rooted, path: "/home/u/work/gamma" }, { projects_root: "/home/u/work/", projects_root_set: true })).toBe("root");
  });
  it("an entry from an older server (no storage, no display_path) reads own with its path", () => {
    const old: ProjectSummary = { ...ok, path: "/p/plumbline" };
    expect(whereLabel(old)).toEqual({ kind: "own", badge: "Own folder", text: "/p/plumbline", title: "/p/plumbline" });
  });
  it("the filter keeps one kind, 'all' keeps everything, and Corvin itself only under 'all'", () => {
    const list = [corvin, managed, own, rooted];
    expect(whereFilter(list, "all", ctx).map((p) => p.id)).toEqual(["corvin", "alpha", "beta", "gamma"]);
    expect(whereFilter(list, "managed", ctx).map((p) => p.id)).toEqual(["alpha"]);
    expect(whereFilter(list, "own", ctx).map((p) => p.id)).toEqual(["beta"]);
    expect(whereFilter(list, "root", ctx).map((p) => p.id)).toEqual(["gamma"]);
  });
  it("the kinds present ignore Corvin itself, so the filter appears only with two kinds of the user's own projects", () => {
    expect(presentWhere([corvin, managed], ctx)).toEqual(["managed"]);
    expect(presentWhere([corvin, managed, own, rooted], ctx)).toEqual(["managed", "own", "root"]);
  });
  it("the header sentence names the default place", () => {
    expect(defaultPlaceSentence({ default_storage: "managed", projects_root: "/home/u/work" })).toBe("New projects go to the Corvin workspace.");
    expect(defaultPlaceSentence({ default_storage: "root", projects_root: "/home/u/work" })).toBe("New projects go to /home/u/work.");
    expect(defaultPlaceSentence({ projects_root: "/home/u/projects" })).toBe("New projects go to /home/u/projects.");
  });
});
