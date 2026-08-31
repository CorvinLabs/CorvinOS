/**
 * Live-server E2E config — drives the REAL console on :8765 (production build,
 * served by the running corvin-webui service), not a Vite dev server.
 *
 * The default `playwright.config.ts` uses a global setup that logs in through
 * :5173 and reuses a storageState; these specs authenticate themselves via the
 * real `/auth/local-login` redirect instead, so they satisfy the e2e-wiring-proof
 * requirement of crossing the actual transport boundary.
 *
 *   systemctl --user restart corvin-webui        # serve the current build
 *   npx playwright test --config=playwright.live.config.ts
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e-live",
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765",
    ...devices["Desktop Chrome"],
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
