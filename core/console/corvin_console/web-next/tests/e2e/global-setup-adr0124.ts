/**
 * Global setup for ADR-0124 E2E tests.
 * Logs in ONCE via local-login and saves the session cookies to auth-state.json.
 * All subsequent tests reuse those cookies — no more repeated login calls.
 *
 * Rate-limit guard: if auth-state.json already contains a valid session,
 * skip the local-login call entirely to avoid burning the rate-limit budget
 * (10 logins / IP / 60 s).
 */
import { chromium, request } from "@playwright/test";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const _dirname = path.dirname(fileURLToPath(import.meta.url));

export const AUTH_STATE_PATH = path.join(_dirname, "auth-state.json");

/** Check if the existing auth-state is still valid (whoami returns 200). */
async function isSessionValid(): Promise<boolean> {
  if (!fs.existsSync(AUTH_STATE_PATH)) return false;
  try {
    const ctx = await request.newContext({ baseURL: "http://localhost:5173" });
    // Replay cookies from the stored state
    const raw = JSON.parse(fs.readFileSync(AUTH_STATE_PATH, "utf-8"));
    const cookies: Array<{
      name: string; value: string; domain: string; path: string;
      expires?: number; httpOnly?: boolean; secure?: boolean; sameSite?: "Strict"|"Lax"|"None";
    }> = raw.cookies ?? [];
    // Build a cookie header manually and check whoami
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    const r = await ctx.get("http://localhost:5173/v1/console/auth/whoami", {
      headers: { Cookie: cookieHeader },
    });
    await ctx.dispose();
    return r.status() === 200;
  } catch {
    return false;
  }
}

export default async function globalSetup() {
  // Fast path: reuse the existing valid session
  if (await isSessionValid()) {
    console.log("[global-setup] Existing auth-state.json is valid — skipping local-login");
    return;
  }

  const browser = await chromium.launch();
  const context = await browser.newContext({ baseURL: "http://localhost:5173" });
  const page = await context.newPage();

  await page.goto("http://localhost:5173/v1/console/auth/local-login", {
    waitUntil: "load",
    timeout: 20_000,
  }).catch(() => null);
  // After redirect we should land on the SPA — wait for React to boot
  await page.waitForURL(/\/console\//, { timeout: 10_000 }).catch(() => null);

  // Verify the session through the CONTEXT's request API, not page.evaluate().
  //
  // page.evaluate() needs a live JS execution context, and local-login redirects into
  // the SPA which then does its own client-side routing. Whenever that landed mid-flight
  // the evaluate died with "Execution context was destroyed, most likely because of a
  // navigation" — and because this is globalSetup, that one race failed the ENTIRE run,
  // not one test. The old guard was a waitForTimeout(1000) guess, so it held or did not
  // depending on machine speed. context.request shares the context's cookie jar, so it
  // proves the session exists without depending on the page being still or settled.
  let ok = false;
  for (let attempt = 1; attempt <= 3 && !ok; attempt += 1) {
    const r = await context.request
      .get("http://localhost:5173/v1/console/auth/whoami")
      .catch(() => null);
    ok = !!r && r.ok();
    if (!ok) await page.waitForTimeout(500 * attempt);
  }

  if (!ok) {
    await browser.close();
    throw new Error(
      "Global setup: login failed — GET /v1/console/auth/whoami never returned ok. " +
      "Is the console running on :8765 and the vite dev server on :5173?",
    );
  }

  await context.storageState({ path: AUTH_STATE_PATH });
  await browser.close();
  console.log("[global-setup] New auth-state.json saved");
}
