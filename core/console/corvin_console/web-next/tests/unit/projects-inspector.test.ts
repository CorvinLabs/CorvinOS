/** The inspector's decisions (pages/projects/inspector-logic.ts). */
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { FeatureRow, ProjectDetail } from "@/lib/api/projects";
import { MARKER_TRASH, confirmMatches, conflictOf, doctorSummary, healthHeadline, parseTab, restorableUntil, rowState, settingsSections, trashDate,
  trashLinkLabel, trashPager, trashRows } from "@/pages/projects/inspector-logic";

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
  it("ready: rename, move, repair, export, duplicate and remove are on; locate is not offered", () => {
    expect(act(base, "general", "rename")).toEqual({ enabled: true, note: null });
    expect(act(base, "location", "move")).toEqual({ enabled: true, note: null });
    expect(act(base, "location", "locate")).toBeUndefined();
    expect(act(base, "maintenance", "repair")).toEqual({ enabled: true, note: null });
    expect(act(base, "share", "export")).toEqual({ enabled: true, note: null });
    expect(act(base, "share", "duplicate")).toEqual({ enabled: true, note: null });
    expect(act(base, "danger", "remove")).toEqual({ enabled: true, note: null });
  });
  it("a secret project: Export opens and says why it cannot run; Duplicate is off with the reason", () => {
    const s = { ...base, classification: "secret" as const };
    expect(act(s, "share", "export")).toEqual({ enabled: true, note: null });
    expect(act(s, "share", "duplicate")).toEqual({ enabled: false, note: "Secret projects cannot be duplicated." });
  });
  it("delete is on for a ready managed project and not offered at all for an own folder or a missing one (T-0405)", () => {
    expect(act(base, "danger", "delete")).toEqual({ enabled: true, note: null });
    expect(act({ ...base, storage: "own" }, "danger", "delete")).toBeUndefined();
    expect(act({ ...base, storage: "own" }, "danger", "remove")).toEqual({ enabled: true, note: null });
    expect(act({ ...base, state: "missing" }, "danger", "delete")).toBeUndefined();
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
    expect(act(i, "maintenance", "repair")).toEqual({ enabled: false, note: "Repair needs a project.yaml that can be read and names this project; the health check says what to fix." });
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
      .toBe("3 checks, 2 failed — Generated CLAUDE.md out of date.");   // the detail as a sentence (REVIEW-0067 #9)
  });
});

describe("the Trash (T-0405)", () => {
  it("the typed confirmation is the exact id: no trimming, no case folding, never empty", () => {
    expect(confirmMatches("alpha", "alpha")).toBe(true);
    expect(confirmMatches("alpha ", "alpha")).toBe(false);
    expect(confirmMatches("Alpha", "alpha")).toBe(false);
    expect(confirmMatches("alph", "alpha")).toBe(false);
    expect(confirmMatches("", "")).toBe(false);
    expect(MARKER_TRASH).toBe("Move to Trash");
  });
  it("restorable until is 30 days after the deletion; an unreadable date has none", () => {
    expect(restorableUntil("2026-11-11T07:00:00Z")).toBe("2026-12-11T07:00:00Z");
    expect(restorableUntil("2026-02-10T23:30:00Z")).toBe("2026-03-12T23:30:00Z");
    expect(restorableUntil(null)).toBeNull();
    expect(restorableUntil("yesterday")).toBeNull();
  });
  it("dates are pinned to en-US and UTC", () => {
    expect(trashDate("2026-11-11T23:59:00Z")).toBe("Nov 11, 2026");
    expect(trashDate(undefined)).toBe("unknown");
  });
  it("rows are newest first, use the server's deadline when it sent one, and a pending entry cannot be acted on", () => {
    const rows = trashRows([
      { entry: "a-20261101T000000Z", id: "a", title: "Alpha", deleted_at: "2026-11-01T00:00:00Z", restorable_until: "2026-12-01T00:00:00Z", pending: false },
      { entry: "b-20261105T000000Z", id: "b", title: "", deleted_at: "2026-11-05T00:00:00Z", restorable_until: null, pending: true },
    ]);
    expect(rows.map((r) => r.id)).toEqual(["b", "a"]);
    expect(rows[0]).toMatchObject({ title: "b", deleted: "Nov 5, 2026", until: "Dec 5, 2026", canAct: false });
    expect(rows[0].note).toContain("Still being moved");
    expect(rows[1]).toMatchObject({ title: "Alpha", until: "Dec 1, 2026", canAct: true, note: null });
    expect(trashRows(undefined)).toEqual([]);
  });
  it("the Trash link is shown only when something is in it", () => {
    expect(trashLinkLabel(0)).toBeNull();
    expect(trashLinkLabel(undefined)).toBeNull();
    expect(trashLinkLabel(1)).toBe("Trash (1)");
  });
  // REVIEW-0063 m6: the server pages the Trash (offset, limit, total, next_offset); the section pages with it
  it("one page needs no pager; a first page offers Older, a middle page both, the last page Newer", () => {
    const rows = (n: number) => Array.from({ length: n }, () => ({}));
    expect(trashPager(undefined)).toEqual({ label: null, newer: null, older: null });
    expect(trashPager({ entries: rows(3), total: 3, offset: 0, limit: 200, next_offset: null })).toEqual({ label: null, newer: null, older: null });
    expect(trashPager({ entries: rows(200), total: 523, offset: 0, limit: 200, next_offset: 200 }))
      .toEqual({ label: "Showing 1–200 of 523", newer: null, older: 200 });
    expect(trashPager({ entries: rows(200), total: 523, offset: 200, limit: 200, next_offset: 400 }))
      .toEqual({ label: "Showing 201–400 of 523", newer: 0, older: 400 });
    expect(trashPager({ entries: rows(123), total: 523, offset: 400, limit: 200, next_offset: null }))
      .toEqual({ label: "Showing 401–523 of 523", newer: 200, older: null });
  });
  it("a page emptied by restores steps back to the newer page; an old server without paging fields is one page", () => {
    expect(trashPager({ entries: [], total: 200, offset: 200, limit: 200, next_offset: null }))
      .toEqual({ label: null, newer: 0, older: null, stepBack: 0 });
    expect(trashPager({ entries: [{}, {}], ttl_days: 30 })).toEqual({ label: null, newer: null, older: null });
  });
});

describe("at a glance", () => {
  const sum = (over: Partial<import("@/lib/api/task-tracking").Summary> = {}) => ({ open: 3, in_progress: 2, blocked: 1, complete: 9, archived: 0, total: 15, overdue: 0,
    approvals_pending: 0, done_7d: 4, initiatives_active: 1, ...over });

  it("draws only the tiles whose source is on, and leads each to a tab that exists", async () => {
    const { glanceTiles } = await import("@/pages/projects/inspector-logic");
    expect(glanceTiles({ active: ["core"], kb: false, builtin: false }).map((t) => t.id)).toEqual(["changes"]);
    expect(glanceTiles({ active: ["core", "kb", "tasks", "graph", "videos"], kb: true, builtin: false }).map((t) => [t.id, t.tab])).toEqual(
      [["tasks", "tasks"], ["knowledge", "knowledge"], ["videos", "videos"], ["changes", null]]);
    // knowledge base on, graph off: the numbers lead to Docs, not to a "Feature off" page
    expect(glanceTiles({ active: ["core", "kb"], kb: true, builtin: false }).find((t) => t.id === "knowledge")?.tab).toBe("docs");
    // tasks need the knowledge base itself, not only the flag
    expect(glanceTiles({ active: ["core", "tasks"], kb: false, builtin: false }).some((t) => t.id === "tasks")).toBe(false);
    // Corvin itself is not a git folder of the user's: no changes tile
    expect(glanceTiles({ active: ["core", "kb", "tasks", "graph"], kb: true, builtin: true }).some((t) => t.id === "changes")).toBe(false);
  });

  it("every tile's tab is one the tab list offers", async () => {
    const { glanceTiles, TABS } = await import("@/pages/projects/inspector-logic");
    for (const t of glanceTiles({ active: ["core", "kb", "tasks", "graph", "videos"], kb: true, builtin: false })) {
      if (t.tab) expect(TABS.some((x) => x.id === t.tab)).toBe(true);
    }
  });

  it("tasks: open first, then in progress and blocked", async () => {
    const { tasksGlance } = await import("@/pages/projects/inspector-logic");
    expect(tasksGlance(sum())).toEqual({ value: "3", detail: "open · 2 in progress · 1 blocked" });
    expect(tasksGlance(sum({ open: 0, in_progress: 0, blocked: 0 })).value).toBe("0");
  });

  it("knowledge: the total, the three biggest kinds, and an honest empty line", async () => {
    const { knowledgeGlance } = await import("@/pages/projects/inspector-logic");
    expect(knowledgeGlance({ tasks: 5, decisions: 2, concepts: 2, notes: 1, empty: 0 })).toEqual({ value: "10", detail: "5 tasks · 2 concepts · 2 decisions" });
    expect(knowledgeGlance({ epics: 1, initiatives: 1, tasks: 1 }).detail).toBe("1 epic · 1 initiative · 1 task");   // singular reads right
    expect(knowledgeGlance({ epics: 1, tasks: 2 }).detail).toBe("2 tasks · 1 epic");
    // the real keys of the live server (a folder name is never shown as such)
    expect(knowledgeGlance({ concepts: 16, "implementation-plans": 1, "adversarial-reviews": 2, decisions: 0 })).toEqual(
      { value: "19", detail: "16 concepts · 2 reviews · 1 plan" });
    expect(knowledgeGlance({ unknown_kind: 2 }).detail).toBe("2 unknown_kind");                                        // an unknown kind is shown as is
    expect(knowledgeGlance({})).toEqual({ value: "0", detail: "nothing written yet" });
    expect(knowledgeGlance(undefined).value).toBe("0");
    expect(knowledgeGlance({ tasks: Number.NaN, notes: 4 }).value).toBe("4");
  });

  it("videos: how many, and how many carry both languages", async () => {
    const { videosGlance } = await import("@/pages/projects/inspector-logic");
    const v = (de: boolean, en: boolean) => ({ name: "x", de, en, folder: "x", state: "ok", folder_exists: true });
    expect(videosGlance([])).toEqual({ value: "0", detail: "no video yet" });
    expect(videosGlance([v(true, true), v(true, false)])).toEqual({ value: "2", detail: "1 with German and English" });
    expect(videosGlance([v(true, true)]).detail).toBe("all with German and English");
  });

  it("changes: nothing to say outside a repository; a cut list says so", async () => {
    const { changesGlance } = await import("@/pages/projects/inspector-logic");
    expect(changesGlance({ files: [], truncated: false, has_repository: false })).toBeNull();
    expect(changesGlance({ files: [], truncated: false, has_repository: true })).toEqual({ value: "0", detail: "everything is committed" });
    const f = { path: "a" } as never;
    expect(changesGlance({ files: [f], truncated: false, has_repository: true })).toEqual({ value: "1", detail: "file not committed yet" });
    expect(changesGlance({ files: [f, f], truncated: true, has_repository: true })).toEqual({ value: "2+", detail: "files not committed yet" });
  });
});
