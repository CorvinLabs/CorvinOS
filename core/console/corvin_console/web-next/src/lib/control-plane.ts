/**
 * Shared fetch helper for the control-plane pages (plugins, subsystems,
 * snapshots).
 *
 * Their backend routers answer 501 `not_implemented` on every route: the
 * backing modules were simulations and were defused. A page that turned that
 * answer into "undefined data" rendered "No plugins found" / "No snapshots
 * yet" — an empty state that claims the list was read and is empty. This
 * helper keeps the two apart: 501 - and 404 on a read - is "not available on
 * this build", every other failure carries the server's own reason.
 */

/** The route exists but is not implemented (501), or a read found no route (404). */
export class ControlPlaneUnavailable extends Error {
  constructor() {
    super("not available on this build");
    this.name = "ControlPlaneUnavailable";
  }
}

export function isControlPlaneUnavailable(error: unknown): error is ControlPlaneUnavailable {
  return error instanceof ControlPlaneUnavailable;
}

/** Best human-readable reason from a FastAPI error body. */
async function failureReason(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : body;
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object") {
      const d = detail as { message?: unknown; reason?: unknown; error?: unknown };
      for (const v of [d.message, d.reason, d.error]) {
        if (typeof v === "string" && v) return v;
      }
    }
  } catch {
    // not JSON — fall through to the status line
  }
  return `HTTP ${res.status}`;
}

export async function controlPlaneFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { credentials: "include", ...init });
  // 404 means "this route does not exist on this build" only for a READ of a
  // collection. On a mutation it is the server saying THIS plugin / snapshot /
  // subsystem does not exist: reporting that as "not available on this build"
  // hid the reason and (through controlPlanePollInterval) read as a missing
  // feature. A mutation's 404 carries the server's own reason like any error.
  const method = (init?.method ?? "GET").toUpperCase();
  const isRead = method === "GET" || method === "HEAD";
  if (res.status === 501 || (res.status === 404 && isRead)) throw new ControlPlaneUnavailable();
  if (!res.ok) throw new Error(await failureReason(res));
  return (await res.json()) as T;
}

/** Toast/inline text for a failed control-plane call. */
export function controlPlaneErrorText(error: unknown): string {
  if (isControlPlaneUnavailable(error)) return "Not available on this build.";
  return error instanceof Error && error.message ? error.message : "Unknown error";
}

/** Polling interval that stops for good once the route is known to be unavailable. */
export function controlPlanePollInterval(error: unknown, ms: number): number | false {
  return isControlPlaneUnavailable(error) ? false : ms;
}
