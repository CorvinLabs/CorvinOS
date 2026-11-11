/** The inspector's decisions (pages/projects/inspector-logic.ts). */
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { FeatureRow, ProjectDetail } from "@/lib/api/projects";
import { conflictOf, doctorSummary, healthHeadline, parseTab, rowState, settingsSections } from "@/pages/projects/inspector-logic";

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
    const on = rowState(row({ id: "video_producer", active: true, plugins: [{ plugin: "video_producer", state: "absent" }] }));
    expect(on).toMatchObject({ canToggle: true, action: "off", blockedBy: null });
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

describe("settings sections (T-0387)", () => {
  const base: ProjectDetail = { id: "alpha", path: "/w/alpha", template: "plain@1", state: "ok", title: "Alpha", storage: "managed",
    display_path: "Corvin workspace › alpha", location_warnings: [] };
  const ids = (d: ProjectDetail, ctx = {}) => settingsSections(d, ctx).map((s) => s.id);
  const act = (d: ProjectDetail, sec: string, a: string, ctx = {}) => settingsSections(d, ctx).find((s) => s.id === sec)?.actions[a as never];

  it("a ready project has the seven sections in their order, with their titles", () => {
    expect(ids(base)).toEqual(["general", "location", "protection", "chat", "maintenance", "share", "danger"]);
    expect(settingsSections(base, {}).map((s) => s.title))
      .toEqual(["General", "Location", "Protection", "Use in a chat", "Maintenance", "Share", "Danger zone"]);
  });
  it("ready: rename, move, repair and remove are on; locate is not offered; export and duplicate say they come later", () => {
    expect(act(base, "general", "rename")).toEqual({ enabled: true, note: null });
    expect(act(base, "location", "move")).toEqual({ enabled: true, note: null });
    expect(act(base, "location", "locate")).toBeUndefined();
    expect(act(base, "maintenance", "repair")).toEqual({ enabled: true, note: null });
    expect(act(base, "share", "export")).toEqual({ enabled: false, note: "Available soon" });
    expect(act(base, "share", "duplicate")).toEqual({ enabled: false, note: "Available soon" });
    expect(act(base, "danger", "remove")).toEqual({ enabled: true, note: null });
  });
  it("delete is a row for a managed project (later update) and an explained absence for an own folder", () => {
    expect(act(base, "danger", "delete")).toEqual({ enabled: false, note: "Available in a later update" });
    expect(act({ ...base, storage: "own" }, "danger", "delete")).toEqual({ enabled: false, note: "(not available: own folder)" });
  });
  it("missing: the folder can be located, the project removed from the list, nothing that needs the folder is offered", () => {
    const m: ProjectDetail = { ...base, state: "missing", title: undefined, reason: "the directory does not exist" };
    expect(ids(m)).toEqual(["general", "location", "danger"]);
    expect(act(m, "general", "rename")?.enabled).toBe(false);
    expect(act(m, "location", "locate")).toEqual({ enabled: true, note: null });
    expect(act(m, "location", "move")).toBeUndefined();
    expect(act(m, "danger", "remove")?.enabled).toBe(true);
  });
  it("invalid: the doctor is shown but repair needs a readable project.yaml; no locate (the folder is where the list says)", () => {
    const i: ProjectDetail = { ...base, state: "invalid", reason: "project.yaml does not match the registry entry" };
    expect(ids(i)).toEqual(["general", "location", "maintenance", "danger"]);
    expect(act(i, "maintenance", "repair")).toEqual({ enabled: false, note: "Repair needs a readable project.yaml." });
    expect(act(i, "location", "locate")).toBeUndefined();
  });
  it("a foreign workspace offers a new workspace in every state; an own folder with a hint word carries the warning", () => {
    expect(act(base, "location", "rekey")).toBeUndefined();
    expect(act(base, "location", "rekey", { workspace: "foreign" })).toEqual({ enabled: true, note: null });
    expect(act({ ...base, state: "missing" }, "location", "rekey", { workspace: "foreign" })?.enabled).toBe(true);
    const warn = "The assistant may refuse some shell commands in a folder whose path contains 'forge'.";
    const own: ProjectDetail = { ...base, storage: "own", location_warnings: [{ class: "hint_word", message: warn }] };
    expect(settingsSections(own, {}).find((s) => s.id === "location")?.notes).toEqual([warn]);
    expect(settingsSections(base, {}).find((s) => s.id === "location")?.notes).toEqual([]);
  });
  it("Corvin itself has only 'Use in a chat'", () => {
    expect(ids({ ...base, builtin: true, storage: "builtin" })).toEqual(["chat"]);
  });
  it("the doctor line counts failures and names the first one; notes are not checks", () => {
    expect(doctorSummary([])).toBe("No checks have run.");
    expect(doctorSummary([{ id: "a", ok: true, detail: "x", at: "" }, { id: "b", ok: true, detail: "y", at: "" }])).toBe("2 checks, none failed");
    expect(doctorSummary([{ id: "a", ok: true, detail: "x", at: "" }, { id: "b", ok: false, detail: "generated CLAUDE.md out of date", at: "" },
      { id: "c", ok: false, detail: "z", at: "" }, { id: "n", ok: true, note: true, detail: "hint", at: "" }]))
      .toBe("3 checks, 2 failed — generated CLAUDE.md out of date");
  });
});
