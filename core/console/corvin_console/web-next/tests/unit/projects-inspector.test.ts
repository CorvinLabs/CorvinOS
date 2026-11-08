/** The inspector's decisions (pages/projects/inspector-logic.ts). */
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { FeatureRow } from "@/lib/api/projects";
import { conflictOf, healthHeadline, parseTab, rowState } from "@/pages/projects/inspector-logic";

const row = (over: Partial<FeatureRow>): FeatureRow => ({ id: "kb", title: "kb", description: "", active: true, requires: ["core"],
  suggests: [], dependants: [], plugins: [], ...over });

describe("feature rows", () => {
  it("an active feature offers to switch off; an inactive one offers on and previews what it pulls in", () => {
    expect(rowState(row({ active: true }))).toMatchObject({ action: "off", canToggle: true, preview: [] });
    const off = rowState(row({ id: "acs", active: false, requires: ["core", "claude"] }));
    expect(off).toMatchObject({ action: "on", canToggle: true, preview: ["Claude Code rules"] });
  });
  it("core is never toggled", () => {
    expect(rowState(row({ id: "core", active: true })).canToggle).toBe(false);
  });
  it("a feature whose tenant plugin is not enabled cannot be switched on, and says why", () => {
    const s = rowState(row({ id: "video_producer", active: false, plugins: [{ plugin: "video_producer", state: "absent" }] }));
    expect(s.canToggle).toBe(false);
    expect(s.blockedBy).toMatch(/video producer plugin, which is not enabled/);
  });
});

describe("the server's refusal of a change", () => {
  const err = (status: number, detail: unknown) => new ApiError(status, detail);
  it("dependants: names the features that would have to go first", () => {
    const c = conflictOf(err(409, { detail: { message: "x", reason_class: "has_dependants", feature: "kb", dependants: ["tasks", "graph"] } }));
    expect(c).toEqual({ kind: "dependants", feature: "kb", dependants: ["Tasks", "Knowledge graph"] });
  });
  it("requires: names what switching on would also pull in", () => {
    expect(conflictOf(err(409, { detail: { reason_class: "needs_dependencies", requires: ["claude"], message: "m" } })))
      .toEqual({ kind: "requires", requires: ["Claude Code rules"] });
  });
  it("a plugin refusal and any other 409 keep the server's message", () => {
    expect(conflictOf(err(409, { detail: { reason_class: "plugin_missing", message: "enable it" } }))).toEqual({ kind: "plugin", message: "enable it" });
    expect(conflictOf(err(409, { detail: { message: "core cannot be switched off", reason_class: "core_required" } })))
      .toEqual({ kind: "other", message: "core cannot be switched off" });
  });
  it("is null for anything that is not a 409", () => {
    expect(conflictOf(err(500, { detail: "boom" }))).toBeNull();
    expect(conflictOf(new Error("x"))).toBeNull();
  });
});

describe("tabs and health", () => {
  it("an unknown tab falls back to the overview", () => {
    expect(parseTab("features")).toBe("features");
    expect(parseTab("nope")).toBe("overview");
    expect(parseTab(null)).toBe("overview");
  });
  it("never claims all is well while a check is red, and says so when nothing ran", () => {
    expect(healthHeadline([{ ok: true }, { ok: false }])).toEqual({ text: "1 of 2 checks need attention.", ok: false });
    expect(healthHeadline([{ ok: true }])).toEqual({ text: "Every check passes.", ok: true });
    expect(healthHeadline([])).toMatchObject({ ok: false });
  });
});

import { videoNameProblem } from "@/pages/projects/inspector-logic";
describe("video names (the same rule the server enforces)", () => {
  it("accepts kebab-case and says why anything else is refused", () => {
    expect(videoNameProblem("plumbline-intro")).toBeNull();
    expect(videoNameProblem("v2")).toBeNull();
    expect(videoNameProblem("")).toMatch(/Give the video a name/);
    expect(videoNameProblem("Bad Name")).toMatch(/lowercase/);
    expect(videoNameProblem("../escape")).toMatch(/lowercase/);
    expect(videoNameProblem("-leading")).toMatch(/lowercase/);
    expect(videoNameProblem("x".repeat(61))).toMatch(/at most 60/);
  });
});

import { TABS, tabGate } from "@/pages/projects/inspector-logic";
describe("inspector tabs and their features", () => {
  it("every tab of the design exists once: Overview, Structure, Docs, Knowledge, Tasks, Videos, Directory, Compute, Features, Settings", () => {
    expect(TABS.map((t) => t.id)).toEqual(["overview", "structure", "docs", "knowledge", "tasks", "videos", "directory", "compute", "features", "settings"]);
    expect(new Set(TABS.map((t) => t.id)).size).toBe(TABS.length);
  });
  it("a feature tab is shut while its feature is off and open while it is on", () => {
    expect(tabGate("docs", ["core", "kb"])).toEqual({ open: true, feature: "kb" });
    expect(tabGate("docs", ["core"])).toEqual({ open: false, feature: "kb" });
    expect(tabGate("compute", ["core", "claude"])).toEqual({ open: false, feature: "acs" });
  });
  it("overview, structure, features and settings never depend on a feature", () => {
    for (const t of ["overview", "structure", "features", "settings"] as const) {
      expect(tabGate(t, [])).toEqual({ open: true, feature: null });
    }
  });
});
