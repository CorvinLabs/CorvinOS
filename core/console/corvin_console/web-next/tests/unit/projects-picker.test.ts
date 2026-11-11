/**
 * The folder picker's decisions (pages/projects/picker-logic.ts): navigation, selection, the keyboard model, the new-folder name
 * rule, the verdict sentence and the places. The dialog only draws these; a wrong key binding or a verdict that enables "Choose"
 * on a refusal is invisible in a screenshot.
 */
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { BrowseEntry, BrowseListing, BrowsePlace } from "@/lib/api/projects";
import {
  INITIAL_PICKER, NAME_RULE_MESSAGE, OK_NEW, OK_PLAIN, SLOW_MESSAGE, THROTTLED_MESSAGE, TYPEAHEAD_MS, allowsNewFolder, canChoose,
  cannotOpen, crumbsView, enterable, entryNote, fieldAfter, initialPath, joinPath, keyHandler, listingError, newFolderNameRule, parentOf, placeGroups,
  reducer, target, toggleFavourite, underRoot, verdictText, verdictWarnings, type KeyInput, type PickerState,
} from "@/pages/projects/picker-logic";

const row = (name: string, extra: Partial<BrowseEntry> = {}): BrowseEntry => ({
  name, path: `/home/u/code/${name}`, kind: "dir", hidden: false, symlink: false, readable: true, project: { state: "none" }, ...extra,
});
const listing = (path: string, entries: BrowseEntry[], extra: Partial<BrowseListing> = {}): BrowseListing => ({
  path, display: path.replace("/home/u", "~"), parent: path === "/" ? null : parentOf(path),
  breadcrumbs: [{ label: "/", path: "/" }, ...path.split("/").filter(Boolean).map((p, i, a) => ({ label: p, path: `/${a.slice(0, i + 1).join("/")}` }))],
  readable: true, entries, verdict: { ok: true, message: OK_NEW }, truncated: false, ...extra,
});
const CODE = listing("/home/u/code", [row("alpha"), row("beta-tools", { refusal: "other_tenant", project: { state: "other" } }), row("bravo"),
  row("corvin-link", { symlink: true, refusal: "symlink" }), row("notes"), row("scratch")]);
const key = (k: string, extra: Partial<KeyInput> = {}): KeyInput => ({ key: k, ctrlKey: false, metaKey: false, altKey: false, inField: false, ...extra });

/** Applies a key; "cancel" is returned to the caller, everything else goes through the reducer. */
function press(s: PickerState, k: KeyInput, now = 0): { s: PickerState; cancelled: boolean } {
  const ev = keyHandler(s, k, now);
  if (!ev) return { s, cancelled: false };
  if (ev.type === "cancel") return { s, cancelled: true };
  return { s: reducer(s, ev), cancelled: false };
}

describe("a scripted keyboard session", () => {
  it("open, type-ahead jump, Enter in, Backspace up, Space select, Esc cancel keeps the previous value", () => {
    let s = reducer(INITIAL_PICKER, { type: "navigate", path: "/home/u/code" });
    expect(s.status).toBe("loading");
    s = reducer(s, { type: "loaded", listing: CODE, seq: s.seq });
    expect(s.status).toBe("ready");
    expect(s.focus).toBe(0);
    // type-ahead: "n" jumps to notes, "s" within 700 ms extends nothing ("ns" matches no row) and keeps the focus
    ({ s } = press(s, key("n"), 1000));
    expect(CODE.entries[s.focus].name).toBe("notes");
    ({ s } = press(s, key("s"), 1200));
    expect(CODE.entries[s.focus].name).toBe("notes");
    // after the buffer expires, "b" starts again: first "b" after the focus, wrapping (beta-tools, then bravo)
    ({ s } = press(s, key("b"), 1200 + TYPEAHEAD_MS + 1));
    expect(CODE.entries[s.focus].name).toBe("beta-tools");
    ({ s } = press(s, key("b"), 1200 + 2 * TYPEAHEAD_MS + 2));
    expect(CODE.entries[s.focus].name).toBe("bravo");
    // "al" within the window: a prefix match
    ({ s } = press(s, key("a"), 5000));
    ({ s } = press(s, key("l"), 5100));
    expect(CODE.entries[s.focus].name).toBe("alpha");
    // arrows
    ({ s } = press(s, key("ArrowDown")));
    expect(CODE.entries[s.focus].name).toBe("beta-tools");
    // Enter on a foreign row does nothing
    expect(keyHandler(s, key("Enter"), 0)).toBeNull();
    ({ s } = press(s, key("ArrowUp")));
    ({ s } = press(s, key("Enter")));
    expect(s.path).toBe("/home/u/code/alpha");
    expect(s.status).toBe("loading");
    s = reducer(s, { type: "loaded", listing: listing("/home/u/code/alpha", []), seq: s.seq });
    // Backspace goes up
    ({ s } = press(s, key("Backspace")));
    expect(s.path).toBe("/home/u/code");
    s = reducer(s, { type: "loaded", listing: CODE, seq: s.seq });
    // Space marks the current folder as the choice (and clears a typed new-folder name)
    s = reducer(s, { type: "newName", value: "alpha-2" });
    expect(target(s)).toBe("/home/u/code/alpha-2");
    ({ s } = press(s, key(" ")));
    expect(s.marked).toBe(true);
    expect(s.newName).toBe("");
    expect(target(s)).toBe("/home/u/code");
    // Esc cancels: the field keeps what it had
    const r = press(s, key("Escape"));
    expect(r.cancelled).toBe(true);
    expect(fieldAfter("/home/u/old", { kind: "cancel" })).toBe("/home/u/old");
    expect(fieldAfter("/home/u/old", { kind: "choose", path: "/home/u/code" })).toBe("/home/u/code");
  });

  it("Ctrl+L and Cmd+L open the path field, also from inside a text field; other keys in a field are left to the field", () => {
    const s = reducer(reducer(INITIAL_PICKER, { type: "navigate", path: "/home/u/code" }), { type: "loaded", listing: CODE, seq: 1 });
    expect(keyHandler(s, key("l", { ctrlKey: true }), 0)).toEqual({ type: "pathMode", open: true });
    expect(keyHandler(s, key("L", { metaKey: true, inField: true }), 0)).toEqual({ type: "pathMode", open: true });
    expect(keyHandler(s, key("Backspace", { inField: true }), 0)).toBeNull();
    expect(keyHandler(s, key("a", { inField: true }), 0)).toBeNull();
    expect(keyHandler(s, key(" ", { inField: true }), 0)).toBeNull();
    expect(keyHandler(s, key("Escape", { inField: true }), 0)).toEqual({ type: "cancel" });
    expect(keyHandler(s, key("a", { altKey: true }), 0)).toBeNull();
  });

  it("a late answer for a folder the user already left is dropped", () => {
    let s = reducer(INITIAL_PICKER, { type: "navigate", path: "/home/u/code" });
    const first = s.seq;
    s = reducer(s, { type: "navigate", path: "/home/u" });
    s = reducer(s, { type: "loaded", listing: CODE, seq: first });
    expect(s.status).toBe("loading");
    expect(s.listing).toBeNull();
  });

  it("a page is appended; a failure is an error state that a retry (navigate to the same path) leaves", () => {
    let s = reducer(INITIAL_PICKER, { type: "navigate", path: "/home/u/code" });
    s = reducer(s, { type: "loaded", listing: { ...CODE, truncated: true, next_page: "scratch" }, seq: s.seq });
    s = reducer(s, { type: "appended", listing: listing("/home/u/code", [row("zulu")]), seq: s.seq });
    expect(s.listing?.entries.map((e) => e.name).slice(-2)).toEqual(["scratch", "zulu"]);
    expect(s.listing?.truncated).toBe(false);
    s = reducer(s, { type: "navigate", path: "/mnt/nas" });
    s = reducer(s, { type: "failed", message: THROTTLED_MESSAGE, seq: s.seq });
    expect(s.status).toBe("error");
    expect(s.error).toBe(THROTTLED_MESSAGE);
    s = reducer(s, { type: "navigate", path: "/mnt/nas" });
    expect(s.status).toBe("loading");
    expect(s.error).toBeNull();
  });

  it("Show hidden reloads the folder; the path field keeps its draft", () => {
    let s = reducer(reducer(INITIAL_PICKER, { type: "navigate", path: "/home/u/code" }), { type: "loaded", listing: CODE, seq: 1 });
    s = reducer(s, { type: "toggleHidden" });
    expect(s.hidden).toBe(true);
    expect(s.status).toBe("loading");
    s = reducer(s, { type: "pathMode", open: true });
    s = reducer(s, { type: "pathDraft", value: "/srv/x" });
    expect(s.pathDraft).toBe("/srv/x");
    expect(reducer(s, { type: "pathMode", open: false }).pathMode).toBe(false);
  });
});

describe("the new-folder name rule", () => {
  const accept = ["alpha", "alpha-2", "a_b", "v1.2", "A", "x".repeat(80), "2026-11"];
  const reject = ["", ".hidden", "a/b", "a\\b", "a b", "über", "x".repeat(81), "..", "a:b", "a*"];
  it.each(accept)("accepts %j", (n) => expect(newFolderNameRule(n)).toBeNull());
  it.each(reject)("refuses %j with the rule sentence", (n) => expect(newFolderNameRule(n)).toBe(NAME_RULE_MESSAGE));
  it("the sentence is the spec's", () => {
    expect(NAME_RULE_MESSAGE).toBe("Use letters, digits, '-', '_' or '.', no leading dot, at most 80 characters.");
  });
  it("an invalid name keeps Choose off and the target at the current folder", () => {
    let s = reducer(reducer(INITIAL_PICKER, { type: "navigate", path: "/home/u/code" }), { type: "loaded", listing: CODE, seq: 1 });
    s = reducer(s, { type: "newName", value: ".x" });
    expect(target(s)).toBe("/home/u/code");
    expect(canChoose(s, { ok: true, message: OK_NEW })).toBe(false);
    s = reducer(s, { type: "newName", value: "fine" });
    expect(canChoose(s, { ok: true, message: OK_NEW })).toBe(true);
    expect(canChoose(s, { ok: false, reason_class: "not_empty", message: "x" })).toBe(false);
    expect(canChoose(s, undefined)).toBe(false);
  });
});

describe("the verdict", () => {
  it("names a new folder for create, move and import; plain otherwise; the server's sentence on a refusal", () => {
    for (const p of ["create", "move", "import"] as const) expect(verdictText({ ok: true, message: "x" }, p)).toBe(OK_NEW);
    for (const p of ["adopt", "projects_root", "relocate"] as const) expect(verdictText({ ok: true, message: "x" }, p)).toBe(OK_PLAIN);
    expect(OK_NEW).toBe("Can be used — a new folder will be created when you continue.");
    expect(verdictText({ ok: false, reason_class: "corvin_home", message: "This is Corvin's own data; projects cannot live here." }, "adopt"))
      .toBe("This is Corvin's own data; projects cannot live here.");
  });
  it("a hint word is a warning sentence and does not turn Choose off", () => {
    const v = { ok: true, message: "Can be used", warnings: ["hint_word"],
      notes: [{ class: "hint_word", message: "The assistant may refuse some shell commands in a folder whose path contains 'forge'." }] };
    expect(verdictWarnings(v)).toEqual(["The assistant may refuse some shell commands in a folder whose path contains 'forge'."]);
    expect(verdictWarnings({ ok: true, message: "", warnings: ["hint_word"] })).toEqual([
      "The assistant may refuse some shell commands in this folder."]);
    expect(verdictWarnings({ ok: true, message: "" })).toEqual([]);
    const s = reducer(reducer(INITIAL_PICKER, { type: "navigate", path: "/home/u/forge-lab" }),
      { type: "loaded", listing: listing("/home/u/forge-lab", [], { verdict: v }), seq: 1 });
    expect(canChoose(s, v)).toBe(true);
  });
  it("Choose is off while loading, on a slow answer and in an error state", () => {
    const s = reducer(INITIAL_PICKER, { type: "navigate", path: "/home/u/code" });
    expect(canChoose(s, { ok: true, message: "" })).toBe(false);
    const slow = reducer(s, { type: "loaded", listing: listing("/home/u/code", [], { verdict: { ok: false, reason_class: "slow", message: SLOW_MESSAGE } }), seq: s.seq });
    expect(canChoose(slow, slow.listing?.verdict)).toBe(false);
  });
  it("turns an API failure into the fixed sentences", () => {
    expect(listingError(new ApiError(429, { detail: { message: "Too many folder requests at once; wait a moment.", reason_class: "throttled" } }))).toBe(THROTTLED_MESSAGE);
    expect(listingError(new ApiError(429, {}))).toBe(THROTTLED_MESSAGE);
    expect(listingError(new ApiError(422, { detail: { message: "The path must be absolute, without '..'.", reason_class: "path_rule" } })))
      .toBe("The path must be absolute, without '..'.");
    expect(listingError(new ApiError(422, {}))).toBe("The path must be absolute, without '..'.");
    expect(listingError(new TypeError("fetch failed"))).toBe("No answer from the console. Try again.");
  });
});

describe("rows", () => {
  it("say why they cannot be entered", () => {
    expect(entryNote(row("alpha", { project: { state: "mine", id: "alpha", title: "Alpha research" }, refusal: "nested" }))).toBe("Already a project (Alpha research)");
    expect(entryNote(row("b", { project: { state: "other" }, refusal: "other_tenant" }))).toBe("belongs to another workspace");
    expect(entryNote(row("l", { symlink: true, refusal: "symlink" }))).toBe("link — cannot be chosen");
    expect(entryNote(row("p", { readable: false }))).toBe("no permission");
    expect(entryNote(row(".corvin", { refusal: "corvin_home" }))).toBe("Corvin's own data");
    expect(entryNote(row("etc", { refusal: "system_prefix" }))).toBe("system folder");
    expect(entryNote(row("plain"))).toBe("");
  });
  it("links, foreign rows, Corvin's data, system folders and unreadable folders cannot be entered", () => {
    expect(enterable(row("plain"))).toBe(true);
    expect(enterable(row("mine", { project: { state: "mine", id: "m", title: "M" }, refusal: "nested" }))).toBe(true);
    expect(enterable(row("hid", { hidden: true, refusal: "hidden" }))).toBe(true);
    for (const r of [row("l", { symlink: true, refusal: "symlink" }), row("o", { project: { state: "other" } }), row("c", { refusal: "corvin_home" }),
      row("s", { refusal: "system_prefix" }), row("t", { refusal: "other_tenant" }), row("u", { readable: false })]) {
      expect(enterable(r)).toBe(false);
    }
  });
});

describe("paths and breadcrumbs", () => {
  it("joins and splits in the OS form", () => {
    expect(joinPath("/home/u/code", "alpha")).toBe("/home/u/code/alpha");
    expect(joinPath("/", "srv")).toBe("/srv");
    expect(joinPath("C:\\work", "alpha")).toBe("C:\\work\\alpha");
    expect(joinPath("D:\\", "x")).toBe("D:\\x");
    expect(parentOf("/home/u/code/alpha")).toBe("/home/u/code");
    expect(parentOf("/home")).toBe("/");
    expect(parentOf("C:\\work\\alpha")).toBe("C:\\work");
    expect(parentOf("C:\\work")).toBe("C:\\");
  });
  it("collapse the middle on a wide screen and to the parent on a phone", () => {
    const crumbs = listing("/home/u/code/alpha/deep/er", []).breadcrumbs;
    const wide = crumbsView(crumbs, false);
    expect(wide.map((c) => c.label)).toEqual(["/", "…", "alpha", "deep", "er"]);
    expect(wide[1].path).toBeNull();
    expect(crumbsView(crumbs.slice(0, 3), false).map((c) => c.label)).toEqual(["/", "home", "u"]);
    expect(crumbsView(crumbs, true)).toEqual([{ label: "‹ deep", path: "/home/u/code/alpha/deep" }, { label: "er", path: null }]);
    expect(crumbsView(crumbs.slice(0, 1), true)).toEqual([{ label: "/", path: null }]);
  });
  it("New folder here only where a new folder is a valid choice", () => {
    expect(["create", "move", "projects_root", "import"].every((p) => allowsNewFolder(p as never))).toBe(true);
    expect(allowsNewFolder("adopt")).toBe(false);
    expect(allowsNewFolder("relocate")).toBe(false);
  });
});

describe("places", () => {
  const P = (kind: BrowsePlace["kind"], path: string, extra: Partial<BrowsePlace> = {}): BrowsePlace => ({ kind, label: path.split("/").pop() || path, path, available: true, ...extra });
  const places = [P("managed", "/home/u/.local/share/corvin/projects/w-1", { label: "Corvin workspace" }), P("home", "/home/u", { label: "Home" }),
    P("documents", "/home/u/Documents", { available: false }), P("drive", "/media/u/USB", { note: "removable" }), P("recent", "/home/u/code/notes"),
    P("favourite", "/home/u/code")];
  it("group in the spec's order and leave out empty groups", () => {
    expect(placeGroups(places).map((g) => [g.id, g.label, g.places.length])).toEqual([
      ["places", "Places", 3], ["drives", "Drives and network", 1], ["recent", "Recent", 1], ["favourites", "Favourites", 1]]);
    expect(placeGroups(places.slice(0, 2)).map((g) => g.id)).toEqual(["places"]);
  });
  it("start at the given folder, else the first available of workspace, projects folder, home", () => {
    expect(initialPath(places, "/home/u/code")).toBe("/home/u/code");
    expect(initialPath(places)).toBe("/home/u/.local/share/corvin/projects/w-1");
    expect(initialPath([P("managed", "/m", { available: false }), P("home", "/home/u")])).toBe("/home/u");
    expect(initialPath([])).toBeNull();
  });
  it("a star adds or removes the current folder; at most 10", () => {
    expect(toggleFavourite(places, "/home/u/code")).toEqual([]);
    expect(toggleFavourite(places, "/home/u/x")).toEqual(["/home/u/code", "/home/u/x"]);
    const ten = Array.from({ length: 10 }, (_, i) => P("favourite", `/f${i}`));
    expect(toggleFavourite(ten, "/new")).toBeNull();
  });
});

describe("folders that cannot be opened, and the workspace badge", () => {
  it("a blocked answer says 'cannot be opened', an unreadable one is a permission problem", () => {
    const blocked = listing("/etc", [], { readable: false, verdict: { ok: false, reason_class: "system_prefix", message: "x" } });
    expect(cannotOpen(blocked)).toBe(true);
    expect(cannotOpen(listing("/srv/x", [], { readable: false, verdict: { ok: false, reason_class: "missing", message: "x" } }))).toBe(false);
    expect(cannotOpen(null)).toBe(false);
  });
  it("a chosen folder in the managed root is the Corvin workspace, by components", () => {
    expect(underRoot("/m/w-1/alpha", "/m/w-1")).toBe(true);
    expect(underRoot("/m/w-10/alpha", "/m/w-1")).toBe(false);
    expect(underRoot("C:\\Data\\W\\a", "c:\\data\\w")).toBe(true);
    expect(underRoot("/x", undefined)).toBe(false);
  });
});
