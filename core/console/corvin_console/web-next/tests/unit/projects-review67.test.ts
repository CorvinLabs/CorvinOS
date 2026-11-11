/**
 * REVIEW-0067 (console usability of the Projects stack): the pure decisions behind the fixes. Each test names the finding it pins.
 */
import { describe, expect, it } from "vitest";
import type { BrowsePlace, FeatureRow, ProjectSummary } from "@/lib/api/projects";
import { FEATURE_LABEL, featureLabel } from "@/pages/projects/encodings";
import {
  checkDetail, docsRows, doctorSummary, glanceAriaLabel, rowState, sizeLabel, tabLabel, taskStatusLabel,
} from "@/pages/projects/inspector-logic";
import { chatAccessSentence } from "@/pages/projects/roles";
import { boundRepoLabel, listedProjects, resolveScope, scopeLabel, scopeOptions, selectableProjects, graphScopes } from "@/pages/projects/scope";
import { offersKbPart } from "@/pages/projects/share-logic";
import { initialPath, pickerStart } from "@/pages/projects/picker-logic";

const row = (over: Partial<FeatureRow>): FeatureRow => ({
  id: "kb", title: "Knowledge base", description: "", active: false, requires: [], suggests: [], dependants: [], plugins: [], ...over,
});
const p = (over: Partial<ProjectSummary>): ProjectSummary => ({ id: "alpha", path: "/p", template: "t@1", state: "ok", kb: true, ...over });

describe("M3: the 'Use in a chat' sentence follows the project's assistant access", () => {
  it("read and write say different things", () => {
    expect(chatAccessSentence({ assistant_access: "read" })).toBe("The assistant can read the project but not change its files.");
    expect(chatAccessSentence({ assistant_access: "write" })).toBe("The assistant may create and change files in the project.");
    expect(chatAccessSentence({})).toBe("The assistant can read the project but not change its files.");
  });
});

describe("M4: the graph's own repository is named by its folder when it is not Corvin's knowledge base", () => {
  const corvin = "/home/u/projects/Corvin-Knowledge";
  it("same repository (or unknown) = no separate label", () => {
    expect(boundRepoLabel(corvin, corvin)).toBeNull();
    expect(boundRepoLabel(`${corvin}/`, corvin)).toBeNull();
    expect(boundRepoLabel("~/projects/Corvin-Knowledge", corvin)).toBeNull();
    expect(boundRepoLabel("", corvin)).toBeNull();
    expect(boundRepoLabel(corvin, undefined)).toBeNull();
  });
  it("another repository is named by its folder; a generic folder name keeps its parent", () => {
    expect(boundRepoLabel("/home/u/projects/LLM_lie_detector/docs", corvin)).toBe("LLM_lie_detector/docs");
    expect(boundRepoLabel("/srv/kb-main", corvin)).toBe("kb-main");
  });
  const list = [p({ id: "corvin", builtin: true, title: "Corvin", path: corvin }), p({ id: "alpha", title: "Plumbline" })];
  it("with a separate label the switcher offers both: the graph's repository and Corvin itself", () => {
    const opts = { boundLabel: "LLM_lie_detector/docs" };
    expect(scopeLabel({ kind: "bound" }, {}, opts.boundLabel)).toBe("LLM_lie_detector/docs");
    expect(scopeOptions(list, opts).map((o) => [o.value, o.label])).toEqual([
      ["all", "All knowledge bases"], ["bound", "LLM_lie_detector/docs"], ["project:corvin", "Corvin"], ["project:alpha", "Plumbline"]]);
    expect(listedProjects(list, opts).map((x) => x.id)).toEqual(["corvin", "alpha"]);
    expect(selectableProjects(list, opts).map((x) => x.id)).toEqual(["corvin", "alpha"]);
    expect(resolveScope("project:corvin", list, "graph", opts).scope).toEqual({ kind: "project", id: "corvin" });
    expect(graphScopes({ kind: "all" }, selectableProjects(list, opts).map((x) => x.id))).toEqual([null, "corvin", "alpha"]);
  });
  it("while the graph's setting is still loading, the default scope is not called Corvin yet", () => {
    expect(scopeOptions(list, { pendingBound: true }).map((o) => o.label)).toEqual(["All knowledge bases", "…", "Plumbline"]);
  });
  it("without one, Corvin is the default scope as before", () => {
    expect(scopeOptions(list).map((o) => o.label)).toEqual(["All knowledge bases", "Corvin", "Plumbline"]);
    expect(resolveScope("project:corvin", list, "graph").scope).toEqual({ kind: "bound" });
  });
});

describe("minor 6: the core feature is always on", () => {
  it("core reads as on and offers no switch, even when the manifest lists no features", () => {
    const s = rowState(row({ id: "core", title: "Project", active: false }));
    expect(s.on).toBe(true);
    expect(s.canToggle).toBe(false);
    expect(s.status).toBe("Always on");
    expect(rowState(row({ id: "kb", active: false })).status).toBe("Feature off");
    expect(rowState(row({ id: "kb", active: true })).status).toBe("On");
  });
});

describe("minors 7 + 18: feature labels are words, and no feature is called 'Overview'", () => {
  it("role features have names; the catalogue's title is the fallback", () => {
    expect(featureLabel("datasci")).toBe("Data science");
    expect(featureLabel("legal")).toBe("Legal and compliance");
    expect(featureLabel("software")).toBe("Software engineering");
    expect(featureLabel("brand_new", "Brand new thing")).toBe("Brand new thing");
    expect(featureLabel("brand_new")).toBe("brand new");
    expect(featureLabel("dashboard")).not.toBe("Overview");
    expect(Object.values(FEATURE_LABEL)).not.toContain("Overview");
  });
});

describe("minor 9: health details are sentences", () => {
  it("a Python list is shown as a plain list", () => {
    expect(checkDetail({ id: "generated_files_in_sync", ok: false, detail: "out of sync (repair fixes): ['.corvin/local/kb-commands.md', '.gitignore']", at: "" }))
      .toBe("Generated files are out of date: .corvin/local/kb-commands.md, .gitignore. Repair rewrites them.");
    expect(checkDetail({ id: "import_leftovers", ok: false, detail: "unfinished import folders beside the project: ['a.import', 'b.import']", at: "" }))
      .toBe("Unfinished import folders beside the project: a.import, b.import.");
    expect(checkDetail({ id: "features_closed", ok: false, detail: "missing requirements: ['claude', 'kb']", at: "" }))
      .toBe("Features this project needs are off: Claude Code rules, Knowledge base.");
  });
  it("the knowledge-base check and the path rules are said in words", () => {
    expect(checkDetail({ id: "kb_check", ok: true, detail: "kb check: blocking 0", at: "" })).toBe("The knowledge base check finds no blocking problem.");
    expect(checkDetail({ id: "kb_check", ok: false, detail: "kb check: blocking 2", at: "" })).toBe("The knowledge base check finds 2 blocking problems.");
    expect(checkDetail({ id: "hint_source", ok: true, detail: "the path gate's hint test is loadable", at: "" })).toBe("The rules that guard file paths in a chat are available.");
  });
  it("any other server text is kept, starting with a capital and ending with a period", () => {
    expect(checkDetail({ id: "path_exists", ok: true, detail: "the project directory exists", at: "" })).toBe("The project directory exists.");
    expect(checkDetail({ id: "x", ok: false, detail: "", at: "" })).toBe("");
    expect(checkDetail({ id: "manifest", ok: true, detail: "project.yaml is valid and matches the registry", at: "" })).toBe("project.yaml is valid and matches the registry.");
  });
  it("the Maintenance line uses the same words", () => {
    expect(doctorSummary([{ id: "a", ok: true, detail: "x", at: "" }, { id: "generated_files_in_sync", ok: false, detail: "out of sync (repair fixes): ['CLAUDE.md']", at: "" }]))
      .toBe("2 checks, 1 failed — Generated files are out of date: CLAUDE.md. Repair rewrites them.");
  });
});

describe("minor 10: the Docs tab names kinds in words", () => {
  it("folder names become words, biggest first, singular for one", () => {
    expect(docsRows({ "implementation-plans": 2, "adversarial-reviews": 1, decisions: 0, tasks: 7 })).toEqual([
      { kind: "tasks", label: "Tasks", n: 7 }, { kind: "implementation-plans", label: "Plans", n: 2 },
      { kind: "adversarial-reviews", label: "Reviews", n: 1 }, { kind: "decisions", label: "Decisions", n: 0 }]);
  });
});

describe("minor 11: task status and file sizes in words", () => {
  it("status", () => {
    expect(taskStatusLabel("in_progress")).toBe("In progress");
    expect(taskStatusLabel("done")).toBe("Done");
    expect(taskStatusLabel("open")).toBe("Open");
    expect(taskStatusLabel("weird_state")).toBe("Weird state");
  });
  it("sizes", () => {
    expect(sizeLabel(512)).toBe("512 B");
    expect(sizeLabel(2048)).toBe("2.0 KB");
    expect(sizeLabel(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("minor 12: a glance tile names the tab it leads to", () => {
  it("Knowledge leading to Docs says Docs", () => {
    expect(tabLabel("docs")).toBe("Docs");
    expect(glanceAriaLabel({ id: "knowledge", label: "Knowledge", tab: "docs" }, { value: "17", detail: "16 concepts" }))
      .toBe("Knowledge: 17 16 concepts. Open the Docs tab");
    expect(glanceAriaLabel({ id: "tasks", label: "Tasks", tab: "tasks" }, { value: "3", detail: "open" })).toBe("Tasks: 3 open. Open the Tasks tab");
  });
});

describe("minor 17: export offers the knowledge base only when a project has one", () => {
  it("no project with kb = no option", () => {
    expect(offersKbPart(undefined)).toBe(true);
    expect(offersKbPart({ projects: [{ features: {} }, { features: { kb: false } }] })).toBe(false);
    expect(offersKbPart({ projects: [{ features: {} }, { features: { kb: true } }] })).toBe(true);
  });
});

describe("minor 16: a folder of your own starts at home or the last place, not in the Corvin workspace", () => {
  const P = (kind: BrowsePlace["kind"], path: string, extra: Partial<BrowsePlace> = {}): BrowsePlace => ({ kind, label: path, path, available: true, ...extra });
  const places = [P("managed", "/home/u/.local/share/corvin/projects/w-1"), P("home", "/home/u"),
    P("recent", "/home/u/.local/share/corvin/projects/w-1/alpha"), P("recent", "/home/u/old", { available: false }), P("recent", "/home/u/code")];
  it("own: the first available recent outside the workspace, else home", () => {
    expect(initialPath(places, null, "own")).toBe("/home/u/code");
    expect(initialPath(places.slice(0, 4), null, "own")).toBe("/home/u");
    expect(initialPath(places, "/given", "own")).toBe("/given");
    expect(initialPath(places)).toBe("/home/u/.local/share/corvin/projects/w-1");
  });
  it("which pickers are own-folder picks", () => {
    expect(["create", "adopt", "projects_root"].map((x) => pickerStart(x as never))).toEqual(["own", "own", "own"]);
    expect(["move", "relocate", "import"].map((x) => pickerStart(x as never))).toEqual(["workspace", "workspace", "workspace"]);
  });
});
