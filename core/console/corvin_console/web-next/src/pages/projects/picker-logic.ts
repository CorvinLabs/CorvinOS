/**
 * The folder picker's decisions, pure so they are unit-tested (tests/unit/projects-picker.test.ts): navigation and selection
 * (`reducer`), the keyboard model (`keyHandler`), the new-folder name rule, the verdict sentence, row notes, breadcrumbs and places.
 * The server judges every folder (the verdict is the location rule itself) and the action judges it again on confirm; nothing here
 * decides whether a folder may be used, it only decides what to draw and when "Choose" is on.
 */
import { ApiError } from "@/lib/api/client";
import type { BrowseEntry, BrowseListing, BrowsePlace, BrowseVerdict, PickerPurpose } from "@/lib/api/projects";

export const TYPEAHEAD_MS = 700;
export const NAME_RULE_MESSAGE = "Use letters, digits, '-', '_' or '.', no leading dot, at most 80 characters.";
export const OK_NEW = "Can be used — a new folder will be created when you continue.";
export const OK_PLAIN = "Can be used";
export const SLOW_MESSAGE = "This folder did not answer in time (network share?). Try again or choose another place.";
export const THROTTLED_MESSAGE = "Too many folder requests at once; wait a moment.";
export const UNREADABLE_MESSAGE = "No permission to read this folder.";
export const PATH_RULE_MESSAGE = "The path must be absolute, without '..'.";
export const NO_ANSWER_MESSAGE = "No answer from the console. Try again.";
export const HINT_FALLBACK = "The assistant may refuse some shell commands in this folder.";

/** Row refusals that mean "cannot be opened" (the server answers no listing for them). */
const BLOCKING = new Set(["symlink", "corvin_home", "system_prefix", "other_tenant"]);

export interface PickerState {
  /** the folder being shown; null before the places told where to start */
  path: string | null;
  listing: BrowseListing | null;
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
  /** request counter: an answer for an older request is dropped */
  seq: number;
  /** index of the focused row, -1 = none */
  focus: number;
  hidden: boolean;
  pathMode: boolean;
  pathDraft: string;
  newName: string;
  /** Space was pressed: the current folder is the choice */
  marked: boolean;
  typed: string;
  typedAt: number;
}

export const INITIAL_PICKER: PickerState = {
  path: null, listing: null, status: "idle", error: null, seq: 0, focus: -1, hidden: false, pathMode: false, pathDraft: "", newName: "",
  marked: false, typed: "", typedAt: -Infinity,
};

export type PickerEvent =
  | { type: "navigate"; path: string }
  | { type: "loaded"; listing: BrowseListing; seq: number }
  | { type: "appended"; listing: BrowseListing; seq: number }
  | { type: "failed"; message: string; seq: number }
  | { type: "focus"; index: number }
  | { type: "move"; delta: number }
  | { type: "first" }
  | { type: "last" }
  | { type: "type"; char: string; now: number }
  | { type: "toggleHidden" }
  | { type: "pathMode"; open: boolean }
  | { type: "pathDraft"; value: string }
  | { type: "newName"; value: string }
  | { type: "mark" };

const rows = (s: PickerState): BrowseEntry[] => s.listing?.entries ?? [];
const clamp = (i: number, n: number) => (n === 0 ? -1 : Math.max(0, Math.min(n - 1, i)));

export function reducer(s: PickerState, e: PickerEvent): PickerState {
  switch (e.type) {
    case "navigate":
      return { ...s, path: e.path, listing: null, status: "loading", error: null, seq: s.seq + 1, focus: -1, newName: "", marked: false,
        typed: "", typedAt: -Infinity };
    case "loaded":
      if (e.seq !== s.seq) return s;
      return { ...s, listing: e.listing, status: "ready", error: null, focus: e.listing.entries.length ? 0 : -1 };
    case "appended":
      if (e.seq !== s.seq || !s.listing) return s;
      return { ...s, listing: { ...s.listing, entries: [...s.listing.entries, ...e.listing.entries], truncated: e.listing.truncated,
        next_page: e.listing.next_page } };
    case "failed":
      if (e.seq !== s.seq) return s;
      return { ...s, status: "error", error: e.message, listing: null, focus: -1 };
    case "focus":
      return { ...s, focus: clamp(e.index, rows(s).length) };
    case "move":
      return { ...s, focus: clamp((s.focus < 0 ? (e.delta > 0 ? -1 : 0) : s.focus) + e.delta, rows(s).length) };
    case "first":
      return { ...s, focus: clamp(0, rows(s).length) };
    case "last":
      return { ...s, focus: clamp(rows(s).length - 1, rows(s).length) };
    case "type": {
      const buf = (e.now - s.typedAt <= TYPEAHEAD_MS ? s.typed : "") + e.char.toLowerCase();
      const list = rows(s);
      const starts = (i: number) => list[i].name.toLowerCase().startsWith(buf);
      let hit = -1;
      if (buf.length === 1) {
        for (let k = 1; k <= list.length && hit < 0; k += 1) {
          const i = (Math.max(s.focus, -1) + k) % list.length;
          if (starts(i)) hit = i;
        }
      } else {
        hit = list.findIndex((_r, i) => starts(i));
      }
      return { ...s, typed: buf, typedAt: e.now, focus: hit >= 0 ? hit : s.focus };
    }
    case "toggleHidden":
      return s.path === null ? { ...s, hidden: !s.hidden } : { ...reducer(s, { type: "navigate", path: s.path }), hidden: !s.hidden };
    case "pathMode":
      return { ...s, pathMode: e.open, pathDraft: e.open && !s.pathMode ? (s.path ?? "") : s.pathDraft };
    case "pathDraft":
      return { ...s, pathDraft: e.value };
    case "newName":
      return { ...s, newName: e.value, marked: false };
    case "mark":
      return { ...s, marked: true, newName: "" };
  }
}

export interface KeyInput { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; /** focus is in a text field */ inField: boolean }

/** A key, as a picker event; `cancel` closes the picker without a choice; null = not the picker's key. */
export function keyHandler(s: PickerState, k: KeyInput, now: number): PickerEvent | { type: "cancel" } | null {
  if ((k.ctrlKey || k.metaKey) && !k.altKey && k.key.toLowerCase() === "l") return { type: "pathMode", open: true };
  if (k.key === "Escape") return { type: "cancel" };
  if (k.inField || k.ctrlKey || k.metaKey || k.altKey) return null;
  const list = rows(s);
  switch (k.key) {
    case "ArrowDown": return { type: "move", delta: 1 };
    case "ArrowUp": return { type: "move", delta: -1 };
    case "Home": return { type: "first" };
    case "End": return { type: "last" };
    case "Enter": {
      const r = list[s.focus];
      return r && enterable(r) ? { type: "navigate", path: r.path } : null;
    }
    case "Backspace":
      return s.listing?.parent ? { type: "navigate", path: s.listing.parent } : null;
    case " ":
      return s.status === "ready" ? { type: "mark" } : null;
  }
  if (k.key.length === 1 && /\S/.test(k.key)) return { type: "type", char: k.key, now };
  return null;
}

/** null = a valid new folder name; else the rule's sentence. */
export function newFolderNameRule(name: string): string | null {
  return /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,79}$/.test(name) ? null : NAME_RULE_MESSAGE;
}

export function allowsNewFolder(purpose: PickerPurpose): boolean {
  return purpose === "create" || purpose === "move" || purpose === "projects_root" || purpose === "import";
}

const isWin = (p: string) => /^[A-Za-z]:\\/.test(p) || (p.includes("\\") && !p.includes("/"));

export function joinPath(dir: string, name: string): string {
  const sep = isWin(dir) ? "\\" : "/";
  return dir.endsWith(sep) ? dir + name : dir + sep + name;
}

export function parentOf(p: string): string {
  const sep = isWin(p) ? "\\" : "/";
  const trimmed = p.length > 1 && p.endsWith(sep) && !/^[A-Za-z]:\\$/.test(p) ? p.slice(0, -1) : p;
  const i = trimmed.lastIndexOf(sep);
  if (i < 0) return trimmed;
  if (sep === "\\" && i === 2 && /^[A-Za-z]:/.test(trimmed)) return trimmed.slice(0, 3);
  return i === 0 ? sep : trimmed.slice(0, i);
}

/** What "Choose" would return: the current folder, or the current folder plus a valid new folder name. */
export function target(s: PickerState): string | null {
  if (s.path === null) return null;
  const shown = s.listing?.path ?? s.path;
  return s.newName && newFolderNameRule(s.newName) === null ? joinPath(shown, s.newName) : shown;
}

export function verdictText(v: BrowseVerdict, purpose: PickerPurpose): string {
  if (!v.ok) return v.message;
  return purpose === "create" || purpose === "move" || purpose === "import" ? OK_NEW : OK_PLAIN;
}

/** The sentences of the notes that do not refuse (a hint word in an own folder's path). */
export function verdictWarnings(v: BrowseVerdict | undefined): string[] {
  if (!v?.ok) return [];
  const notes = (v.notes ?? []).map((n) => n.message).filter(Boolean);
  if (notes.length) return notes;
  return (v.warnings ?? []).includes("hint_word") ? [HINT_FALLBACK] : [];
}

/** "Choose" is on only for a loaded folder with an ok verdict for exactly what would be returned. */
export function canChoose(s: PickerState, v: BrowseVerdict | undefined): boolean {
  if (s.status !== "ready" || !v?.ok || target(s) === null) return false;
  return s.newName === "" || newFolderNameRule(s.newName) === null;
}

/** A folder the server answered without a listing because it may not be opened (Corvin's data, a system folder, a link, another
 * workspace); otherwise an empty unreadable answer means no permission. */
export function cannotOpen(l: BrowseListing | null): boolean {
  return !!l && !l.verdict.ok && !!l.verdict.reason_class && BLOCKING.has(l.verdict.reason_class);
}

export function enterable(e: BrowseEntry): boolean {
  return !e.symlink && e.readable && e.project.state !== "other" && !(e.refusal && BLOCKING.has(e.refusal));
}

/** The right-hand note of a row ("" = none). */
export function entryNote(e: BrowseEntry): string {
  if (e.symlink || e.refusal === "symlink") return "link — cannot be chosen";
  if (e.project.state === "other" || e.refusal === "other_tenant") return "belongs to another workspace";
  if (e.refusal === "corvin_home") return "Corvin's own data";
  if (e.refusal === "system_prefix") return "system folder";
  if (!e.readable) return "no permission";
  if (e.project.state === "mine") return `Already a project (${e.project.title || e.project.id || e.name})`;
  if (e.refusal === "protected") return "protected place";
  if (e.refusal === "hidden") return "hidden";
  if (e.refusal === "nested") return "inside a project";
  return "";
}

export interface Crumb { label: string; path: string | null }

/** Wide: every crumb, the middle collapsed to "…" beyond five. Compact (phone): "‹ <parent>" and the current folder's name. */
export function crumbsView(crumbs: { label: string; path: string }[], compact: boolean): Crumb[] {
  if (!crumbs.length) return [];
  const last = crumbs[crumbs.length - 1];
  if (compact) {
    if (crumbs.length === 1) return [{ label: last.label, path: null }];
    const parent = crumbs[crumbs.length - 2];
    return [{ label: `‹ ${parent.label}`, path: parent.path }, { label: last.label, path: null }];
  }
  const all: Crumb[] = crumbs.map((c, i) => ({ label: c.label, path: i === crumbs.length - 1 ? null : c.path }));
  return all.length > 5 ? [all[0], { label: "…", path: null }, ...all.slice(-3)] : all;
}

/** The sentence of a failed listing. */
export function listingError(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.detail as { detail?: { message?: unknown } } | undefined;
    const msg = body && typeof body === "object" && body.detail && typeof body.detail === "object" ? body.detail.message : undefined;
    if (err.status === 429) return THROTTLED_MESSAGE;
    if (typeof msg === "string" && msg) return msg;
    if (err.status === 422) return PATH_RULE_MESSAGE;
    if (err.status === 403) return "Your session expired. Sign in again.";
    return "The folder could not be listed. Try again.";
  }
  return NO_ANSWER_MESSAGE;
}

export type Outcome = { kind: "cancel" } | { kind: "choose"; path: string };

/** The field a picker was opened from: Cancel keeps what it had. */
export function fieldAfter(previous: string, o: Outcome): string {
  return o.kind === "choose" ? o.path : previous;
}

export interface PlaceGroup { id: "places" | "drives" | "recent" | "favourites"; label: string; places: BrowsePlace[] }

export function placeGroups(places: BrowsePlace[]): PlaceGroup[] {
  const of = (kinds: BrowsePlace["kind"][]) => places.filter((p) => kinds.includes(p.kind));
  const groups: PlaceGroup[] = [
    { id: "places", label: "Places", places: of(["managed", "root", "home", "desktop", "documents"]) },
    { id: "drives", label: "Drives and network", places: of(["drive"]) },
    { id: "recent", label: "Recent", places: of(["recent"]) },
    { id: "favourites", label: "Favourites", places: of(["favourite"]) },
  ];
  return groups.filter((g) => g.places.length > 0);
}

/** Where the picker opens: the given folder, else the first available of the workspace, the projects folder, home. */
export function initialPath(places: BrowsePlace[], given?: string | null): string | null {
  if (given) return given;
  for (const kind of ["managed", "root", "home"] as const) {
    const p = places.find((x) => x.kind === kind && x.available);
    if (p) return p.path;
  }
  return places.find((p) => p.available)?.path ?? null;
}

/** The favourites after starring or un-starring *path*; null = already 10. */
export function toggleFavourite(places: BrowsePlace[], path: string): string[] | null {
  const favs = places.filter((p) => p.kind === "favourite").map((p) => p.path);
  if (favs.includes(path)) return favs.filter((p) => p !== path);
  return favs.length >= 10 ? null : [...favs, path];
}

/** *p* lies in (or is) *root*, compared by path components (case-folded on Windows paths). */
export function underRoot(p: string, root: string | null | undefined): boolean {
  if (!root) return false;
  const fold = (x: string) => (isWin(x) ? x.toLowerCase() : x);
  const sep = isWin(root) ? "\\" : "/";
  const r = fold(root.endsWith(sep) ? root.slice(0, -1) : root);
  const q = fold(p);
  return q === r || q.startsWith(r + sep);
}
