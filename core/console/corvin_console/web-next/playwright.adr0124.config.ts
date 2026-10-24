import { defineConfig, devices } from "@playwright/test";
import path from "path";
import { fileURLToPath } from "url";
import { mayReuseDevServer, resolveConsoleOrigin } from "./tests/e2e/sandbox-origin";

const _dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: ["**/adr-0124-*.spec.ts"],
  fullyParallel: false,
  workers: 1,
  retries: 1,
  reporter: "line",
  timeout: 90_000,

  globalSetup: "./tests/e2e/global-setup-adr0124.ts",

  use: {
    baseURL: "http://localhost:5173",
    storageState: path.join(_dirname, "tests/e2e/auth-state.json"),
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: mayReuseDevServer(),
    // Pin the dev server's /v1 proxy to the run's own origin (never the live :8765 by default).
    env: { CORVIN_GATEWAY_URL: resolveConsoleOrigin() },
  },
});
