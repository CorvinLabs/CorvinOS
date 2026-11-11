/**
 * A deterministic folder tree behind the picker's routes (`/v1/console/projects/-/browse…`) for the stubbed projects specs. The
 * shapes are the server's (core/projects/browse.py, routes/projects.py); the verdict sentences are the location rule's own. The
 * real rule and listing are proven over HTTP by tests/e2e/test_projects_browse_e2e.py and in the browser against the real routes
 * by a separate spec.
 */
import type { Page } from "@playwright/test";

export const HOME = "/home/u";
export const MR = "/home/u/.local/share/corvin/projects/w-0123456789";
export const SENT = {
  top_level: "Top-level folders and home folders cannot be chosen — pick a folder inside your home or data directory.",
  corvin_home: "This is Corvin's own data; projects cannot live here.",
  system_prefix: "This is a system folder; projects cannot live here.",
  other_tenant: "This folder already belongs to another workspace.",
  workspace_root: "Choose a project folder inside the Corvin workspace, not the workspace itself.",
  nested: "This folder is inside another project, or contains one.",
  not_empty: "This folder is not empty — choose an empty one or use New folder.",
  missing: "This folder does not exist.",
  symlink: "This is a link; choose the real folder.",
  slow: "This folder did not answer in time (network share?). Try again or choose another place.",
  path_rule: "The path must be absolute, without '..'.",
} as const;
export const HINT = "The assistant may refuse some shell commands in a folder whose path contains 'forge'.";

interface Dir { children?: string[]; refusal?: string; project?: { state: "mine" | "other" | "none"; id?: string; title?: string }; symlink?: boolean;
  readable?: boolean; hidden?: boolean }

/** path -> what it is; a child name not in the map is a plain empty folder. */
const TREE: Record<string, Dir> = {
  "/": { children: ["etc", "home", "media"] },
  "/etc": { refusal: "system_prefix" },
  "/home": { children: ["u"] },
  "/media": { children: ["u"] },
  "/media/u": { children: ["USB"] },
  [HOME]: { children: [".corvin", ".local", "code", "Documents", "music"] },
  [`${HOME}/.corvin`]: { refusal: "corvin_home", hidden: true },
  [`${HOME}/.local`]: { hidden: true, children: ["share"] },
  [`${HOME}/code`]: { children: ["alpha", "beta-tools", "corvin-link", "forge-lab", "nas-mount", "notes", "private", "scratch"] },
  [`${HOME}/code/alpha`]: { project: { state: "mine", id: "alpha", title: "Alpha research" }, refusal: "nested" },
  [`${HOME}/code/beta-tools`]: { project: { state: "other" }, refusal: "other_tenant" },
  [`${HOME}/code/corvin-link`]: { symlink: true, refusal: "symlink" },
  [`${HOME}/code/private`]: { readable: false },
  [`${HOME}/code/scratch`]: { children: ["draft"] },
  [MR]: { children: ["alpha"] },
  [`${HOME}/projects`]: { children: ["lie-copy"] },
};
const node = (p: string): Dir | undefined => TREE[p];
const parentOf = (p: string) => (p === "/" ? null : p.slice(0, p.lastIndexOf("/")) || "/");
const join = (d: string, n: string) => (d === "/" ? `/${n}` : `${d}/${n}`);
const exists = (p: string): boolean => p === "/" || !!node(p) || (() => { const par = parentOf(p); return !!par && !!node(par)?.children?.includes(p.split("/").pop()!); })();

export interface PickerStub {
  /** every GET /browse as `path|purpose|project_id` */
  calls: string[];
  favourites: string[][];
  clearedRecent: number;
  /** how often a slow path answered (the first answer is slow, later ones list) */
  slowHits: Record<string, number>;
}

function verdict(path: string, purpose: string): Record<string, unknown> {
  const n = node(path);
  const v = (cls: keyof typeof SENT) => ({ ok: false, reason_class: cls, message: SENT[cls] });
  if (path === "/" || path === "/home" || path === HOME || path === "/media" || path === "/media/u") return v("top_level");
  if (path === MR) return v("workspace_root");
  if (n?.refusal && n.refusal !== "nested") return v(n.refusal as keyof typeof SENT);
  if (n?.refusal === "nested" || path === `${HOME}/code`) return v("nested");
  if (!exists(path)) return purpose === "adopt" || purpose === "relocate" ? v("missing")
    : { ok: true, message: "Can be used — a new folder will be created when you continue." };
  if ((purpose === "create" || purpose === "move") && (n?.children?.length ?? 0) > 0) return v("not_empty");
  const ok: Record<string, unknown> = { ok: true, message: purpose === "create" || purpose === "move" || purpose === "import"
    ? "Can be used — a new folder will be created when you continue." : "Can be used" };
  if (path.includes("forge")) { ok.warnings = ["hint_word"]; ok.notes = [{ class: "hint_word", message: HINT }]; }
  return ok;
}

function listing(st: PickerStub, path: string, purpose: string, hidden: boolean) {
  const crumbs = [{ label: "/", path: "/" }];
  let cur = "";
  for (const part of path.split("/").filter(Boolean)) { cur += `/${part}`; crumbs.push({ label: part, path: cur }); }
  const frame = { path, display: path.startsWith(HOME) ? `~${path.slice(HOME.length)}` : path, parent: parentOf(path), breadcrumbs: crumbs };
  if (path.endsWith("/nas-mount")) {
    st.slowHits[path] = (st.slowHits[path] ?? 0) + 1;
    if (st.slowHits[path] === 1) return { ...frame, readable: false, entries: [], verdict: { ok: false, reason_class: "slow", message: SENT.slow }, truncated: false };
  }
  const n = node(path);
  if (n && (n.symlink || ["corvin_home", "system_prefix", "other_tenant"].includes(n.refusal ?? ""))) {
    return { ...frame, readable: false, entries: [], verdict: verdict(path, purpose), truncated: false };
  }
  const entries = (n?.children ?? []).map((name) => {
    const p = join(path, name);
    const c = node(p) ?? {};
    return { name, path: p, kind: "dir", hidden: !!c.hidden, symlink: !!c.symlink, readable: c.readable !== false && !c.symlink,
      project: c.project ?? { state: "none" }, ...(c.refusal ? { refusal: c.refusal } : {}) };
  }).filter((e) => hidden || !e.hidden);
  return { ...frame, readable: true, entries, verdict: verdict(path, purpose), truncated: false };
}

/** Installs the browse routes. `places` may be replaced per spec. */
export async function stubPicker(page: Page, opts: { favourites?: string[]; recent?: string[] } = {}): Promise<PickerStub> {
  const st: PickerStub = { calls: [], favourites: [], clearedRecent: 0, slowHits: {} };
  let favs = [...(opts.favourites ?? [])];
  let recent = [...(opts.recent ?? [`${HOME}/code/notes`])];
  const places = () => [
    { kind: "managed", label: "Corvin workspace", path: MR, available: true },
    { kind: "home", label: "Home", path: HOME, available: true },
    { kind: "documents", label: "Documents", path: `${HOME}/Documents`, available: false, note: "not found" },
    { kind: "drive", label: "USB", path: "/media/u/USB", available: true, note: "removable" },
    ...recent.map((p) => ({ kind: "recent", label: p.split("/").pop(), path: p, available: true })),
    ...favs.map((p) => ({ kind: "favourite", label: p.split("/").pop(), path: p, available: true })),
  ];
  await page.route(/\/v1\/console\/projects\/-\/browse\/places$/, (r) => r.fulfill({ json: { places: places() } }));
  await page.route(/\/v1\/console\/projects\/-\/browse\/favourites$/, (r) => {
    favs = (r.request().postDataJSON() as { paths: string[] }).paths;
    st.favourites.push(favs);
    return r.fulfill({ json: { places: places().filter((p) => p.kind === "favourite") } });
  });
  await page.route(/\/v1\/console\/projects\/-\/browse\/recent$/, (r) => {
    st.clearedRecent += 1; recent = [];
    return r.fulfill({ json: { places: places() } });
  });
  await page.route(/\/v1\/console\/projects\/-\/browse\?/, (r) => {
    const u = new URL(r.request().url());
    const path = u.searchParams.get("path") ?? "";
    const purpose = u.searchParams.get("purpose") ?? "";
    st.calls.push(`${path}|${purpose}|${u.searchParams.get("project_id") ?? ""}`);
    if (!path.startsWith("/") || path.split("/").includes("..")) {
      return r.fulfill({ status: 422, json: { detail: { message: SENT.path_rule, reason_class: "path_rule" } } });
    }
    return r.fulfill({ json: listing(st, path.length > 1 ? path.replace(/\/+$/, "") : path, purpose, u.searchParams.get("hidden") === "1") });
  });
  return st;
}

/** Opens the advanced path field in an open picker, types *path* and goes there. */
export async function enterPath(page: Page, testid: string, path: string): Promise<void> {
  const picker = page.getByTestId("folder-picker");
  const link = picker.getByTestId("picker-path-link");
  if (await link.isVisible()) await link.click();                 // the field stays open after a first Go
  await picker.getByTestId(testid).fill(path);
  await picker.getByTestId(testid).press("Enter");
}
