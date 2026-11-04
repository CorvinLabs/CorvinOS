/**
 * Render-failure recovery for the route error boundary (PLAN-0969 WP-3, CONCEPT-0125).
 *
 * A page that crashes while rendering (the reported case: React error 185, "maximum update
 * depth", on Windows, gone after a manual reload) reloads itself ONCE per route inside
 * RELOAD_GUARD_MS; a second crash in that window shows the error card. Each crash is reported to
 * the backend as a content-free signature: an error code, the route pattern, the build hash, the
 * OS family. Never the message, a chat id or the user agent: the backend refuses those shapes.
 *
 * A reload is safe for a running chat turn because the live-turn hub re-attaches the new socket
 * to it (ADR-2244); the persisted turns file stays the truth.
 */
import { getCurrentCsrf } from "@/lib/csrf-fetch";

/** One automatic reload per route pattern inside this window. */
export const RELOAD_GUARD_MS = 60_000;

const GUARD_PREFIX = "corvin.render-reload:";
const REPORT_URL = "/v1/console/aco/client-error";

/** "react-185" for a minified React invariant, else the error class name. Never the message. */
export function errorCode(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err ?? "");
  const m = /Minified React error #(\d{1,4})/.exec(message);
  if (m) return `react-${m[1]}`;
  const name = err instanceof Error ? err.name : "Error";
  return /^[A-Za-z][A-Za-z0-9_-]{0,39}$/.test(name) ? name : "Error";
}

/** First two segments of the SPA route ("/app/chat"), lower-cased; ids and the /console base dropped. */
export function routePattern(pathname: string): string {
  const parts = pathname
    .split("/")
    .filter(Boolean)
    .filter((p, i) => !(i === 0 && p === "console"))
    .slice(0, 2)
    .map((p) => p.toLowerCase())
    .filter((p) => /^[a-z0-9_-]{1,40}$/.test(p));
  return parts.length ? `/${parts.join("/")}` : "/root";
}

/** Closed OS family from the platform string (and the UA only to tell Android from Linux). */
export function osFamily(platform: string, userAgent: string): string {
  const p = platform.toLowerCase();
  if (p.startsWith("win")) return "windows";
  if (p.startsWith("mac")) return "macos";
  if (/iphone|ipad|ipod/.test(p)) return "ios";
  if (/android/i.test(userAgent)) return "android";
  if (p.includes("linux")) return "linux";
  return "other";
}

/** Entry bundle hash from the served script tag ("index-<hash>.js"), or "dev". */
export function buildHash(doc: Document = document): string {
  for (const s of Array.from(doc.querySelectorAll("script[src]"))) {
    const m = /assets\/index-([A-Za-z0-9_-]{1,32})\.js/.exec(s.getAttribute("src") ?? "");
    if (m) return m[1];
  }
  return "dev";
}

/** True at most once per route inside RELOAD_GUARD_MS; records the reload when it says yes. */
export function shouldAutoReload(route: string, now: number = Date.now()): boolean {
  const key = GUARD_PREFIX + route;
  try {
    const last = Number(sessionStorage.getItem(key) ?? "0");
    if (last && now - last < RELOAD_GUARD_MS) return false;
    sessionStorage.setItem(key, String(now));
    return true;
  } catch {
    return false; // no storage: never risk a reload loop
  }
}

export interface RenderErrorReport {
  code: string;
  route: string;
  build: string;
  os_family: string;
  auto_reloaded: boolean;
}

/** Fire-and-forget; `keepalive` lets it survive the reload that follows. Never throws. */
export function reportRenderError(report: RenderErrorReport): void {
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const csrf = getCurrentCsrf();
    if (csrf) headers["X-CSRF-Token"] = csrf;
    void fetch(REPORT_URL, {
      method: "POST",
      headers,
      body: JSON.stringify(report),
      credentials: "same-origin",
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    /* reporting must never block recovery */
  }
}

/** Decide, report, and return whether the caller should reload now. */
export function recoverFromRenderError(err: unknown, pathname: string): boolean {
  const route = routePattern(pathname);
  const reload = shouldAutoReload(route);
  const nav = typeof navigator !== "undefined" ? navigator : undefined;
  const platform =
    (nav as Navigator & { userAgentData?: { platform?: string } } | undefined)?.userAgentData?.platform ??
    nav?.platform ??
    "";
  reportRenderError({
    code: errorCode(err),
    route,
    build: buildHash(),
    os_family: osFamily(platform, nav?.userAgent ?? ""),
    auto_reloaded: !reload,
  });
  return reload;
}
