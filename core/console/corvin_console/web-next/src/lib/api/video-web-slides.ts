/**
 * Animated web slides need Playwright's Chromium build. The console only REPORTS the state
 * (`GET /video/settings` → `web_slides`); installing the browser is the operator's explicit action
 * (`POST /video/web-slides/install`, session + CSRF, audited) — nothing is downloaded by a GET.
 * The texts live here, not in the page, so the mapping from state and outcome to words is testable.
 */
import { api, ApiError } from "@/lib/api/client";

export type WebSlidesReason = "package_missing" | "browser_missing" | "install_running";
export interface WebSlidesState { available: boolean; reason: WebSlidesReason | null }
export type WebSlidesInstallStatus = "started" | "running" | "already_available";

/** The state from a settings payload; an older build that only sent the boolean reads as "browser missing". */
export function webSlidesState(settings: { web_slides?: WebSlidesState | null; web_slides_available?: boolean }): WebSlidesState {
  if (settings.web_slides) return settings.web_slides;
  return settings.web_slides_available ? { available: true, reason: null } : { available: false, reason: "browser_missing" };
}

/** The note under the settings, or null when web slides are available. */
export function webSlidesNote(state: WebSlidesState): string | null {
  if (state.available) return null;
  switch (state.reason) {
    case "package_missing":
      return "Animated web slides need Playwright in the console environment (pip install playwright); until it is installed every slide is the classic still.";
    case "install_running":
      return "The browser for animated web slides is being installed; until it lands every slide is the classic still.";
    default:
      return "Animated web slides need Playwright's Chromium build; until it is installed every slide is the classic still.";
  }
}

/** POST /video/web-slides/install (api() prefixes the console base): 202 started | running, 200 already_available;
 * 409 / 503 are thrown as ApiError. */
export async function installWebSlidesBrowser(csrf: string): Promise<{ status: WebSlidesInstallStatus }> {
  return api<{ status: WebSlidesInstallStatus }>("/video/web-slides/install", { method: "POST", csrf });
}

/** What the panel says after the install request, for every outcome the route has. */
export function installOutcomeText(result: { status: WebSlidesInstallStatus } | ApiError | Error): string {
  if (result instanceof ApiError) {
    if (result.status === 503) return "The download host is not reachable; install when online.";
    if (result.status === 409) return "Playwright is not installed in the console environment (pip install playwright); there is no browser to install for it.";
    return `The install was not started (HTTP ${result.status}).`;
  }
  if (result instanceof Error) return "The install was not started.";
  switch (result.status) {
    case "already_available":
      return "The browser is already installed.";
    case "running":
      return "An install is already running.";
    default:
      return "The browser is being installed (about 150 MB); this panel updates when it is done.";
  }
}
