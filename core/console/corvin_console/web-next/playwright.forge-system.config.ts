// Forge-system E2E + adversarial suite (Skill / Tool / Plugin / Layer Forge,
// shared through Forge Bundles). Runs against TWO isolated installs, never the
// live console on :8765:
//
//   A "Nordwind HQ"        http://127.0.0.1:$FORGE_E2E_PORT_A (8799)  seeded with all four forges
//   B "Depot Hamburg"      http://localhost:$FORGE_E2E_PORT_B (8798)  empty — the receiving install
//
// Different HOSTS on purpose: cookies are scoped per host, not per port, so
// 127.0.0.1:8799 and 127.0.0.1:8798 would overwrite each other's session.
//
//   npx playwright test -c playwright.forge-system.config.ts
//
// Exports land in ~/Downloads/corvin-forge-e2e/ (FORGE_E2E_DOWNLOADS overrides).
import { defineConfig, devices } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';
import { HOME_A, HOME_B, PORT_A, PORT_B, URL_A, URL_B } from './tests/e2e/forge-system/forge-env';

const dir = path.dirname(fileURLToPath(import.meta.url));
const harness = path.join(dir, 'tests/e2e/forge-system/harness/isolated_forge_backend.py');
const python = path.resolve(dir, '../../.venv/bin/python');

export const FORGE_E2E = {
  A: { url: URL_A, home: HOME_A },
  B: { url: URL_B, home: HOME_B },
};

export default defineConfig({
  testDir: './tests/e2e/forge-system',
  testMatch: '**/*.spec.ts',
  // Snapshot the LIVE audit chain before any spec runs (the isolation proof needs it).
  globalSetup: './tests/e2e/forge-system/global-setup.ts',
  // The installs are shared state (one quarantine queue, one layer registry
  // each). Specs are ordered and serial; the race tests make their own
  // concurrency with Promise.all, where it is the thing under test.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report-forge-system', open: 'never' }]],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    acceptDownloads: true,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `${python} ${harness}`,
      url: `${FORGE_E2E.A.url}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: { FORGE_E2E_HOME: FORGE_E2E.A.home, FORGE_E2E_PORT: String(PORT_A), FORGE_E2E_SEED: '1' },
    },
    {
      command: `${python} ${harness}`,
      url: `http://127.0.0.1:${PORT_B}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: { FORGE_E2E_HOME: FORGE_E2E.B.home, FORGE_E2E_PORT: String(PORT_B), FORGE_E2E_SEED: '0' },
    },
  ],
});
