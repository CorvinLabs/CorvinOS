/**
 * Attach the session's CSRF token to every same-origin, mutating API request.
 *
 * The typed `api()` client passes `csrf` explicitly, but a number of panels
 * call `fetch()` directly. Once every mutating console route required CSRF
 * (router-level `require_session_csrf_on_mutation`), those calls would have
 * started failing with 403 one by one. Wrapping `window.fetch` once covers
 * them all — and any added later.
 *
 * Only same-origin requests to `/v1/` are touched, and an explicit
 * X-CSRF-Token header always wins. The token is readable by this page anyway
 * (it comes from /auth/whoami), so this adds nothing a cross-site attacker
 * could use: the protection comes from the browser never sending it
 * cross-origin.
 */

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);
let currentCsrf: string | null = null;
let installed = false;

export function setCurrentCsrf(token: string | null | undefined): void {
  currentCsrf = token || null;
}

export function shouldAttachCsrf(url: string, method: string, origin: string): boolean {
  if (!MUTATING.has(method.toUpperCase())) return false;
  let parsed: URL;
  try {
    parsed = new URL(url, origin);
  } catch {
    return false;
  }
  return parsed.origin === origin && parsed.pathname.startsWith("/v1/");
}

export function installCsrfFetch(): void {
  if (installed || typeof window === "undefined" || typeof window.fetch !== "function") return;
  const original = window.fetch.bind(window);
  const wrapped = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    if (currentCsrf && shouldAttachCsrf(url, method, window.location.origin)) {
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      if (!headers.has("X-CSRF-Token")) {
        headers.set("X-CSRF-Token", currentCsrf);
        return original(input, { ...init, headers });
      }
    }
    return original(input, init);
  };
  // defineProperty, not assignment: some environments expose fetch as a
  // read-only property. Failing to wrap must never break the console boot.
  try {
    Object.defineProperty(window, "fetch", { value: wrapped, writable: true, configurable: true });
    installed = true;
  } catch {
    /* explicit csrf in api() calls still works */
  }
}
