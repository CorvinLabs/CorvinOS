// Fresh-install plugin-lifecycle E2E (Video Producer, marketplace -> install -> use -> uninstall).
//
// Runs against a backend that has NOTHING from this dev machine: a `git archive HEAD`
// export, an empty CORVIN_HOME, no sibling ../Corvin-Marketplace checkout. The plugin
// index and source therefore come from GitHub (CorvinLabs/Corvin-Marketplace), which
// is the whole point. Needs network access and a built SPA (scripts/console-deploy.sh).
//
//   npx playwright test -c playwright.fresh-install.config.ts
//
// One worker, no storageState, no retries: the specs mutate one shared backend and a
// retry would hide the very state-dependent bugs they exist to find.
import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.CORVIN_FRESH_PORT ?? "8841";
const ROOT = process.env.CORVIN_FRESH_ROOT ?? "/tmp/corvin-fresh-install";

export default defineConfig({
  testDir: "./tests/e2e-fresh",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report-fresh-install" }]],
  timeout: 120_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    storageState: { cookies: [], origins: [] },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "bash scripts/start-fresh-install-backend.sh",
    url: `http://127.0.0.1:${PORT}/healthz`,
    // Never reuse: a reused server is not a fresh install.
    reuseExistingServer: false,
    timeout: 180_000,
    env: { CORVIN_FRESH_PORT: PORT, CORVIN_FRESH_ROOT: ROOT },
  },
});
