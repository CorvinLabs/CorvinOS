/**
 * Live-console specs that must NOT go through the default config: no dev
 * server, no shared storageState, one worker (they edit shared live state).
 *
 *   npx playwright test -c playwright.live.config.ts
 *   CONSOLE_BASE_URL=http://127.0.0.1:8765/console npx playwright test -c playwright.live.config.ts
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "initiatives-live.spec.ts",
  workers: 1,
  reporter: "line",
  use: { ...devices["Desktop Chrome"] },
});
