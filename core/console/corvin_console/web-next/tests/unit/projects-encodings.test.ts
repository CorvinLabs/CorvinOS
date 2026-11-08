/**
 * The Projects page's mappings (pages/projects/encodings.ts). A wrong mapping - a missing project drawn
 * as healthy, a raw feature id, a raw plugin index id - is invisible in a screenshot until a tenant hits it.
 */
import { describe, expect, it } from "vitest";
import type { ProjectSummary, TemplateCatalogue } from "@/lib/api/projects";
import { emptyState, featureChips, featureLabel, offFeatures, sortProjects, stateOf, templateLabel } from "@/pages/projects/encodings";

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
    expect(templateLabel("video-engineering@1")).toBe("video-engineering v1");
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
