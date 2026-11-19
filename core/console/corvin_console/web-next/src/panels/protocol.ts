/**
 * Panel host↔panel postMessage protocol (ADR-0362 P4) — the SSOT shared by the
 * host (PanelHost.tsx) and the client SDK (@corvin/panel-sdk).
 *
 * An external panel (iframe or web-component, e.g. a FrontendForge-built panel, a
 * community panel) is untrusted code. It never gets direct DOM/cookie access to the
 * Console; it talks to the host ONLY through these typed postMessage envelopes. The
 * host decides what context to hand over and what actions to honour.
 *
 * Handshake: panel loads → sends `panel:ready` → host replies `host:hello` with the
 * context → steady state. The host ignores any message from an origin/frame it did
 * not mount, and every message is a discriminated union so a malformed payload is a
 * type error at the boundary, not a runtime surprise.
 */

/** Bump when the envelope SHAPE changes in a way host or SDK must understand. */
export const PANEL_PROTOCOL_VERSION = "1";

/** Context the host hands an external panel. Deliberately minimal and PII-free:
 *  no cookies, no session token. A panel mounted with the default sandbox
 *  ("allow-scripts" only) has an opaque origin and cannot call the console API
 *  with the operator's credentials; baseUrl is informational for such a panel.
 *  It reads console data with `corvin:panel:fetch` instead (see "Data requests"). */
export interface PanelHostContext {
  /** API base the panel should call, e.g. "/v1/console". Same-origin. */
  baseUrl: string;
  /** Tenant the Console is scoped to (for display only; the API enforces it).
   *  ABSENT for a panel whose frame has an opaque origin: that hello has to be
   *  posted with targetOrigin "*", which reaches whatever document the frame
   *  holds at that moment, so it carries nothing that names the install. */
  tenantId?: string;
  /** Current Console theme, so the panel can match. */
  theme: "light" | "dark";
  /** Capability-manifest contract version (ADR-0357), for forward-compat. */
  contractVersion: string;
  /** The paths this panel may request with `corvin:panel:fetch`: what it declared,
   *  reduced to what the console lets panels read. Empty: no data access. */
  dataPaths?: string[];
}

/** Host → Panel. */
export type HostToPanel =
  | { type: "corvin:host:hello"; protocolVersion: string; ctx: PanelHostContext }
  | { type: "corvin:host:theme"; theme: "light" | "dark" }
  /** Answer to `corvin:panel:fetch`, matched by `id`. */
  | { type: "corvin:host:data"; id: string; ok: true; status: number; data: unknown }
  | { type: "corvin:host:data"; id: string; ok: false; status: number; error: string };

/** Panel → Host. */
export type PanelToHost =
  | { type: "corvin:panel:ready"; protocolVersion: string }
  | { type: "corvin:panel:navigate"; to: string }
  | { type: "corvin:panel:resize"; height: number }
  /** Ask the host to read console data for the panel (see "Data requests"). */
  | { type: "corvin:panel:fetch"; id: string; path: string };

// ─ Data requests ────────────────────────────────────────────────────────────
//
// A sandboxed panel has an opaque origin: it cannot call the console API with
// the operator's session, and it must not be given allow-same-origin to make
// that work. It asks the host instead:
//
//   panel → host   { type: "corvin:panel:fetch", id: "1", path: "/sessions" }
//   host  → panel  { type: "corvin:host:data", id: "1", ok: true, status: 200, data: … }
//
// The host performs a GET against its OWN api base, with its own session (so the
// tenant is the host's - a panel cannot name one), and only when the path is
//   1. well-formed (`normalisePanelDataPath`),
//   2. on PANEL_READABLE_PATHS - the console-wide list of read-only paths a
//      panel may ever see, and
//   3. declared by this panel in its manifest (`<meta name="corvin-panel-data">`).
// Everything else is answered `ok: false, status: 403`. There is no write, no
// other method, no header and no body a panel can supply.
//
// Boundary, stated: whatever a panel may read here, any script that panel loads
// can read too and can send elsewhere. PANEL_READABLE_PATHS is therefore the
// perimeter - keep secrets, credentials and settings off it.

/** Console-wide allowlist: paths (relative to the API base) a panel may read.
 *  Exact matches, GET only, no path parameters. Adding a path here exposes it to
 *  every panel that declares it. */
export const PANEL_READABLE_PATHS: readonly string[] = [
  "/sessions",
  "/vibe-engineering/traces",
  "/vibe-engineering/pipeline",
  "/vibe-engineering/grades",
  "/vibe-engineering/state",
  "/monitor/phase3-performance",
  "/cost/summary",
  "/cost/breakdown",
  "/cost/by-model",
  "/talent/score",
  "/talent/history",
  "/talent/task-types",
];

/** Largest response body the host forwards to a panel. */
export const PANEL_DATA_MAX_BYTES = 1024 * 1024;
/** Most paths one panel may declare. */
export const PANEL_DATA_MAX_DECLARED = 16;
/** Requests a mounted panel may have in flight / make per minute. */
export const PANEL_DATA_MAX_IN_FLIGHT = 4;
export const PANEL_DATA_MAX_PER_MINUTE = 120;

const DATA_PATH_RE = /^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/;
const DATA_QUERY_RE = /^[A-Za-z0-9_.~=&,:-]{0,256}$/;
/** Query keys a panel may not send: the tenant is the host session's. */
const FORBIDDEN_QUERY_KEYS = new Set(["tenant", "tenant_id", "tenantid", "tid"]);

export interface PanelDataPath {
  /** e.g. "/vibe-engineering/traces" */
  pathname: string;
  /** "" or "?limit=50" */
  search: string;
}

/** A panel-supplied path as `{pathname, search}`, or null when it is not a plain
 *  relative API path. Refuses a scheme or host, `..`, `//`, a backslash, any
 *  percent-encoding, a fragment, control characters and a tenant parameter. */
export function normalisePanelDataPath(path: unknown): PanelDataPath | null {
  if (typeof path !== "string" || path.length === 0 || path.length > 512) return null;
  const q = path.indexOf("?");
  const pathname = q === -1 ? path : path.slice(0, q);
  const query = q === -1 ? "" : path.slice(q + 1);
  if (!DATA_PATH_RE.test(pathname)) return null;
  if (!DATA_QUERY_RE.test(query)) return null;
  for (const pair of query.split("&")) {
    const key = pair.split("=", 1)[0].toLowerCase();
    if (FORBIDDEN_QUERY_KEYS.has(key)) return null;
  }
  return { pathname, search: query ? `?${query}` : "" };
}

/** The declared paths of a panel, reduced to what may ever be granted: well-formed,
 *  on PANEL_READABLE_PATHS, de-duplicated, at most PANEL_DATA_MAX_DECLARED. */
export function grantablePanelPaths(declared: readonly unknown[] | null | undefined): string[] {
  const out: string[] = [];
  for (const d of declared ?? []) {
    const n = normalisePanelDataPath(d);
    if (!n || n.search) continue; // a declaration names a path, never a query
    if (!PANEL_READABLE_PATHS.includes(n.pathname) || out.includes(n.pathname)) continue;
    out.push(n.pathname);
    if (out.length >= PANEL_DATA_MAX_DECLARED) break;
  }
  return out;
}

export type PanelDataDecision =
  | { ok: true; url: string; pathname: string }
  | { ok: false; reason: string };

/** Decide one data request. `declared` is the panel's manifest declaration. */
export function decidePanelDataRequest(
  path: unknown,
  declared: readonly unknown[] | null | undefined,
  baseUrl: string,
): PanelDataDecision {
  const n = normalisePanelDataPath(path);
  if (!n) return { ok: false, reason: "not a valid data path" };
  if (!PANEL_READABLE_PATHS.includes(n.pathname)) {
    return { ok: false, reason: "path is not readable by panels" };
  }
  if (!grantablePanelPaths(declared).includes(n.pathname)) {
    return { ok: false, reason: "path is not declared by this panel" };
  }
  return { ok: true, url: `${baseUrl}${n.pathname}${n.search}`, pathname: n.pathname };
}

/** The paths a panel document declares:
 *  `<meta name="corvin-panel-data" content="/sessions /cost/summary">`
 *  (space- or comma-separated). Used for an in-editor preview (srcDoc); a stored
 *  panel's declaration comes from its manifest on the server, which reads the
 *  same tag when the panel is installed. */
export function declaredDataPathsFromHtml(html: string): string[] {
  const out: string[] = [];
  const tags = html.match(/<meta\b[^>]*>/gi) ?? [];
  for (const tag of tags) {
    if (!/\bname\s*=\s*(["'])corvin-panel-data\1/i.test(tag)) continue;
    const m = /\bcontent\s*=\s*(["'])([^"']*)\1/i.exec(tag);
    if (m) out.push(...m[2].split(/[\s,]+/).filter(Boolean));
  }
  return out;
}

/** Shape check for a `corvin:panel:fetch` envelope (the id is echoed back). */
export function isPanelFetch(m: unknown): m is { type: "corvin:panel:fetch"; id: string; path: string } {
  if (typeof m !== "object" || m === null) return false;
  const v = m as { type?: unknown; id?: unknown; path?: unknown };
  return (
    v.type === "corvin:panel:fetch" &&
    typeof v.id === "string" && v.id.length > 0 && v.id.length <= 64 &&
    typeof v.path === "string"
  );
}

export function isHostToPanel(m: unknown): m is HostToPanel {
  return (
    typeof m === "object" && m !== null &&
    typeof (m as { type?: unknown }).type === "string" &&
    (m as { type: string }).type.startsWith("corvin:host:")
  );
}

export function isPanelToHost(m: unknown): m is PanelToHost {
  return (
    typeof m === "object" && m !== null &&
    typeof (m as { type?: unknown }).type === "string" &&
    (m as { type: string }).type.startsWith("corvin:panel:")
  );
}
