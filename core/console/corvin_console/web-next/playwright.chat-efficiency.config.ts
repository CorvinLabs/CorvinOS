// Chat cost / efficiency dashboard E2E (ADR-2324). Targets an already running console
// (a sandbox CORVIN_HOME seeded by tests/e2e-fresh/fixtures/chat_efficiency_seed.py); starts nothing.
//
//   CORVIN_EFF_SID=<sid> CORVIN_EFF_BASE=http://127.0.0.1:8799 \
//   npx playwright test -c playwright.chat-efficiency.config.ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e-fresh",
  testMatch: "**/chat-efficiency.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.CORVIN_EFF_BASE ?? "http://127.0.0.1:8799",
    storageState: { cookies: [], origins: [] },
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
