/**
 * Re-auth possession token for the console's re-auth-gated routes.
 *
 * The server's ``verify_reauth`` refuses an absent or empty token on every
 * re-auth route (profile, settings files, feature flags, worker engine, engine
 * keys, promote, chat settings, bridges, compute, licence key, API-key delete).
 * The token is the session fingerprint from ``/auth/whoami``; ``lib/auth.tsx``
 * publishes it here exactly like the CSRF token (``lib/csrf-fetch.ts``). A
 * caller that never loaded the session sends "" and is refused (401).
 */

let current: string | null = null;

export function setCurrentReauthToken(token: string | null | undefined): void {
  current = token || null;
}

export function getReauthToken(): string {
  return current ?? "";
}
