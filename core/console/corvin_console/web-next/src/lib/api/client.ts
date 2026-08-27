/**
 * Core fetch client for the console REST API (extracted from the former
 * monolithic lib/api.ts). Shared infrastructure only: ApiError, the api()
 * wrapper, request timeout, and the 401 / CSRF error handlers.
 *
 * See ./index barrel (../api.ts) for the full public surface.
 */

/**
 * Thin fetch wrapper for the console REST API.
 *
 * Contract from ADR-0015 / ADR-0037:
 *   • Cookie `corvin_console_sid` is set by the backend on /auth/login
 *     and carried automatically with `credentials: "include"`.
 *   • Mutating requests carry `X-CSRF-Token` (value returned by
 *     /auth/login + /auth/whoami).
 */

export const BASE = "/v1/console";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown) {
    const detailStr =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "detail" in detail
          ? formatDetailMessage((detail as { detail: unknown }).detail)
          : `HTTP ${status}`;
    super(detailStr);
    this.status = status;
    this.detail = detail;
  }
}

// Helper to format detail messages without stringifying object arrays
function formatDetailMessage(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((e: unknown) => {
        if (typeof e === "string") return e;
        if (e && typeof e === "object") {
          const obj = e as { msg?: unknown; message?: unknown };
          return String(obj.msg || obj.message || String(e));
        }
        return String(e);
      })
      .filter((msg) => msg !== "[object Object]");
    return messages.length > 0
      ? `Validation error: ${messages.join(", ")}`
      : String(detail);
  }
  return String(detail);
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  body?: unknown;
  csrf?: string;
  signal?: AbortSignal;
  /** Override the default request timeout (ms). Pass 0 to disable. */
  timeoutMs?: number;
}

// A 401 from ANY endpoint means the session is gone — most commonly because
// its ADR-0154 SDLP license-proof no longer matches after a tier change
// (upgrading to Member invalidates outstanding sessions by design, so their
// derived proof stops matching). Previously each page's own react-query call
// just 401'd independently and rendered its own generic "Could not load X"
// error, while the shared auth/whoami poll (every 5 minutes) hadn't yet
// noticed and redirected to /login — several minutes of confusing,
// page-scattered errors before the user was ever told to sign in again.
// AuthProvider registers a handler here so a 401 anywhere immediately
// invalidates the shared session cache instead of waiting on that poll.
let _on401: (() => void) | null = null;
export function setOn401Handler(fn: (() => void) | null): void {
  _on401 = fn;
}

// A 403 "invalid CSRF token" means the frontend's cached csrf_token no longer
// matches the server's session. The token is a pure HMAC of the session id with
// the session's csrf_secret, so it survives a server restart — but it goes
// stale when the session rotates (re-login, tier change) or during the brief
// window a restart is draining/rewriting the session store. Without recovery,
// the stale token leaves EVERY mutation failing 403 — most visibly the
// automatic voice-note TTS ("TTS failed: invalid CSRF token") — until a manual
// page reload. AuthProvider registers a handler here that re-fetches whoami, so
// the shared session cache (and every csrf-dependent callback) picks up the
// current token; the next mutation then succeeds without a reload.
let _onCsrfError: (() => void) | null = null;
export function setOnCsrfErrorHandler(fn: (() => void) | null): void {
  _onCsrfError = fn;
}
export function isCsrfError(status: number, payload: unknown): boolean {
  if (status !== 403) return false;
  const detail =
    payload && typeof payload === "object"
      ? (payload as { detail?: unknown }).detail
      : payload;
  return typeof detail === "string" && detail.toLowerCase().includes("csrf");
}
// Raw fetch helpers that bypass api() (the TTS blob endpoints) call this on a
// 403 CSRF so they trigger the same session refresh.
export function notifyCsrfError(): void {
  _onCsrfError?.();
}

// Default wall-clock budget for a single console API call. Without this a
// hung backend leaves react-query queries pending forever, which the UI
// renders as a perpetual "Loading…" spinner. With it, a stalled request
// rejects and surfaces through the route error boundary instead.
const DEFAULT_TIMEOUT_MS = 30_000;

/**
 * Combine the caller's AbortSignal (react-query cancellation) with a
 * timeout, without relying on AbortSignal.any (not yet universal). Returns
 * the merged signal plus a cleanup() to clear the timer / listener.
 */
function withTimeout(
  signal: AbortSignal | undefined,
  timeoutMs: number,
): { signal: AbortSignal; cleanup: () => void } {
  if (!timeoutMs || timeoutMs <= 0) {
    return { signal: signal ?? new AbortController().signal, cleanup: () => {} };
  }
  const controller = new AbortController();
  const onAbort = () => controller.abort(signal?.reason);
  const timer = setTimeout(
    () => controller.abort(new DOMException("Request timed out", "TimeoutError")),
    timeoutMs,
  );
  if (signal) {
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }
  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    },
  };
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (opts.csrf) {
    headers["X-CSRF-Token"] = opts.csrf;
  }

  const { signal, cleanup } = withTimeout(
    opts.signal,
    opts.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  );

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: opts.method ?? "GET",
      headers,
      credentials: "include",
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal,
    });
  } finally {
    cleanup();
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let payload: unknown = text;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      /* keep as text */
    }
  }

  if (!res.ok) {
    if (res.status === 401) {
      _on401?.();
    } else if (isCsrfError(res.status, payload)) {
      _onCsrfError?.();
    }
    throw new ApiError(res.status, payload);
  }
  return payload as T;
}

