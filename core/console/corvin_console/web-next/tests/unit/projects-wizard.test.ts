/**
 * The wizard's decisions (pages/projects/wizard.ts). The UI only shows these; a wrong closure or a
 * refusal that says nothing is invisible until a user hits it.
 */
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { ProjectPlan, TemplateCatalogue } from "@/lib/api/projects";
import { INITIAL, canAdvance, choosable, chooseTemplate, explain, planRequest, requiredClosure, toggleFeature, type WizardState } from "@/pages/projects/wizard";

const feat = (id: string, requires: string[] = [], plugins: { plugin: string; state: "absent" | "installed" | "enabled" }[] = []) =>
  ({ id, title: id, description: "", requires, suggests: [], plugins });
const cat: TemplateCatalogue = {
  available: true,
  templates: [
    { id: "video-engineering", version: 1, title: "Video", description: "", docs_mode: "nested",
      features: ["core", "kb", "tasks", "videos"], offered: ["core", "kb", "tasks", "gates", "videos", "acs"] },
    { id: "knowledge-only", version: 1, title: "Notes", description: "", docs_mode: "plain", features: ["core", "kb"], offered: ["core", "kb"] },
  ],
  features: [
    feat("core"), feat("kb", ["core"], [{ plugin: "corvin_knowledge", state: "enabled" }]), feat("tasks", ["kb"]),
    feat("gates", ["kb"]), feat("videos", ["core"]), feat("acs", ["claude"]), feat("claude", ["core"]),
  ],
};

describe("choosing a template", () => {
  it("starts from the template's defaults and its docs mode, never from core", () => {
    const s = chooseTemplate(INITIAL, cat, "video-engineering");
    expect(s.templateId).toBe("video-engineering");
    expect(s.features).toEqual(["core", "kb", "tasks", "videos"]);
    expect(s.docsMode).toBe("nested");
    expect(chooseTemplate(INITIAL, cat, "knowledge-only").docsMode).toBe("plain");
  });
  it("offers every feature except core as a choice", () => {
    expect(choosable(cat.templates[0])).toEqual(["kb", "tasks", "gates", "videos", "acs"]);
  });
});

describe("toggling a feature", () => {
  const base = chooseTemplate(INITIAL, cat, "knowledge-only");          // core, kb
  it("switching on pulls in what it requires, dependencies first", () => {
    // dependencies first: acs needs claude, claude needs core
    expect(requiredClosure(cat, "acs")).toEqual(["core", "claude"]);
    const s = toggleFeature(base, cat, "acs");
    expect(s.features).toEqual(["core", "kb", "claude", "acs"]);
  });
  it("switching off removes only that feature; core is never removed", () => {
    const on = toggleFeature(base, cat, "tasks");
    expect(toggleFeature(on, cat, "tasks").features).toEqual(base.features);
    expect(toggleFeature(base, cat, "core").features).toEqual(base.features);
  });
  it("a requires cycle or an unknown id never loops", () => {
    const cyc = requiredClosure({ ...cat, features: [feat("a", ["b"]), feat("b", ["a"])] }, "a");
    expect([...cyc].sort()).toEqual(["a", "b"]);                       // terminates, both reached once
    expect(requiredClosure(cat, "ghost")).toEqual([]);
  });
});

describe("advancing", () => {
  const ready: ProjectPlan = { plan_id: "p", project_id: "x", title: "X", template: "t@1", docs_mode: "nested", location: "/x",
    features: ["core"], pulled: [], files: [], actions: [], warnings: [], conflicts: [], plugins: [], suggestions: [],
    ready: true, expires_in_s: 900 };
  const s = (over: Partial<WizardState>): WizardState => ({ ...INITIAL, ...over });
  it("step 1 needs a template; step 2 a name of 1-120 characters", () => {
    expect(canAdvance(s({ step: 1 }), undefined)).toBe(false);
    expect(canAdvance(s({ step: 1, templateId: "knowledge-only" }), undefined)).toBe(true);
    expect(canAdvance(s({ step: 2, title: "   " }), undefined)).toBe(false);
    expect(canAdvance(s({ step: 2, title: "x".repeat(121) }), undefined)).toBe(false);
    expect(canAdvance(s({ step: 2, title: "Plumbline" }), undefined)).toBe(true);
  });
  it("step 3 needs at least one feature; step 4 only a plan the server called ready", () => {
    expect(canAdvance(s({ step: 3, features: [] }), undefined)).toBe(false);
    expect(canAdvance(s({ step: 4 }), undefined)).toBe(false);
    expect(canAdvance(s({ step: 4 }), { ...ready, ready: false })).toBe(false);
    expect(canAdvance(s({ step: 4 }), ready)).toBe(true);
  });
});

describe("the request", () => {
  it("sends the trimmed name, an empty folder means the server default, never an empty string", () => {
    const req = planRequest({ ...INITIAL, templateId: "knowledge-only", title: "  Plumbline  ", location: "  ", features: ["core", "kb"], docsMode: "plain" });
    expect(req).toEqual({ template: "knowledge-only", title: "Plumbline", features: ["core", "kb"], docs_mode: "plain",
      classification: "internal", assistant_access: "read" });
    expect(req).not.toHaveProperty("location");
  });
  it("a typed folder is sent trimmed", () => {
    expect(planRequest({ ...INITIAL, templateId: "t", title: "x", location: " /p/x " }).location).toBe("/p/x");
  });
});

describe("a role proposes its protection, the user decides", () => {
  const cat = { available: true, features: [], templates: [
    { id: "legal-compliance", version: 1, title: "Legal", description: "", features: ["core", "kb"], offered: ["core", "kb"], docs_mode: "nested" as const,
      role: "legal" as const, classification: "confidential" as const, assistant_access: "read" as const },
    { id: "software-development", version: 1, title: "Software", description: "", features: ["core"], offered: ["core"], docs_mode: "nested" as const,
      role: "software" as const, classification: "internal" as const, assistant_access: "write" as const },
  ] };
  it("choosing a template takes its class and access as the starting point", () => {
    const s = chooseTemplate(INITIAL, cat, "legal-compliance");
    expect([s.classification, s.access]).toEqual(["confidential", "read"]);
    const t = chooseTemplate(s, cat, "software-development");
    expect([t.classification, t.access]).toEqual(["internal", "write"]);
  });
  it("what the user changed is what the request sends", () => {
    const s = { ...chooseTemplate(INITIAL, cat, "legal-compliance"), title: "x", classification: "secret" as const, access: "write" as const };
    const req = planRequest(s);
    expect([req.classification, req.assistant_access]).toEqual(["secret", "write"]);
  });
  it("a template with no metadata falls back to internal and read only", () => {
    const bare = { ...cat, templates: [{ ...cat.templates[0], role: undefined, classification: undefined, assistant_access: undefined }] };
    const s = chooseTemplate(INITIAL, bare, "legal-compliance");
    expect([s.classification, s.access]).toEqual(["internal", "read"]);
  });
});

describe("refusals become sentences", () => {
  it("shows the server's own message when it has one", () => {
    // the real shape: the whole body, the message one level down
    expect(explain(new ApiError(409, { detail: { message: "acs is required by: tasks", reason_class: "has_dependants" } }))).toBe("acs is required by: tasks");
    expect(explain(new ApiError(422, { detail: { message: "the location must not contain '..'", reason_class: "path_rule" } }))).toBe("the location must not contain '..'");
    expect(explain(new ApiError(404, { detail: "no such plan (it expires after 15 minutes) - plan again" }))).toBe("no such plan (it expires after 15 minutes) - plan again");
  });
  it("falls back to an honest sentence per status, never a raw code", () => {
    expect(explain(new ApiError(404, undefined), "wizard")).toMatch(/no longer exists/);
    expect(explain(new ApiError(404, undefined))).toMatch(/no longer in your list/);
    expect(explain(new ApiError(422, { detail: [{ msg: "String should have at most 1000 characters" }] }))).toMatch(/at most 1000/);
    expect(explain(new ApiError(403, undefined))).toMatch(/Sign in again/);
    expect(explain(new Error("network"), "wizard")).toMatch(/whether the project was created/);
    expect(explain(new Error("network"))).toMatch(/may or may not have been applied/);
  });
});
