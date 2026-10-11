// Forge LIVE suite — drives the real console (default :8765) through the four
// Forge generators the way a user does right after a fresh install:
//
//   10-robustness      fast, no engine: validation, refusals, restarts, hostile input
//   20-create-live     REAL engine: forges e2e_test_skill / e2e_test_tool / e2e_test_layer / e2e_test_plugin
//   30-fresh-install   ships those artifacts as one bundle into an EMPTY isolated install
//
//   npx playwright test -c playwright.forge-live.config.ts 10          # seconds, no LLM spend
//   npx playwright test -c playwright.forge-live.config.ts 20          # minutes, spends subscription budget
//   FORGE_LIVE_URL=http://127.0.0.1:8765 FORGE_LIVE_SKIP_B=1 ...      # without the empty install
//
// The e2e_test_* artifacts are KEPT after the run on purpose (the operator wants to see them);
// FORGE_LIVE_REGEN=1 deletes and re-forges them, otherwise an existing one is reused.
import { defineConfig, devices } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';
import { HOME_B, PORT_B } from './tests/e2e/forge-system/forge-env';

const dir = path.dirname(fileURLToPath(import.meta.url));
const harness = path.join(dir, 'tests/e2e/forge-system/harness/isolated_forge_backend.py');
const python = path.resolve(dir, '../../.venv/bin/python');

export default defineConfig({
  testDir: './tests/e2e-live/forge-live',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45 * 60_000,
  expect: { timeout: 20_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report-forge-live', open: 'never' }]],
  use: { trace: 'retain-on-failure', screenshot: 'only-on-failure', acceptDownloads: true, ...devices['Desktop Chrome'] },
  projects: [{ name: 'chromium' }],
  webServer: process.env.FORGE_LIVE_SKIP_B === '1' ? undefined : {
    command: `${python} ${harness}`,
    url: `http://127.0.0.1:${PORT_B}/healthz`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
    env: { FORGE_E2E_HOME: HOME_B, FORGE_E2E_PORT: String(PORT_B), FORGE_E2E_SEED: '0' },
  },
});
