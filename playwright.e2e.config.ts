/**
 * Playwright config for the root-level console E2E specs (tests/e2e/*.spec.ts).
 *
 * The root playwright.config.ts targets ./scripts; this one runs the browser
 * suites under tests/e2e against an ALREADY RUNNING console (the systemd
 * corvin-webui.service on :8765, or CONSOLE_BASE_URL). It never starts a
 * server: a stale-bundle/backend mismatch must be visible, not papered over
 * by a fresh dev server.
 *
 *   CORVIN_E2E_ALLOW_LIVE=1 BASE_URL=http://127.0.0.1:8765 npx playwright test -c playwright.e2e.config.ts
 *
 * Without BASE_URL the run targets the sandbox origin; :8765 is refused unless CORVIN_E2E_ALLOW_LIVE=1.
 */
import { defineConfig } from '@playwright/test';
import { resolveOptionalOrigin } from './core/console/corvin_console/web-next/tests/e2e/sandbox-origin';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 2,
  retries: 0,
  timeout: 60_000,
  outputDir: 'playwright-results/root-e2e',
  reporter: [['line'], ['json', { outputFile: 'playwright-results/root-e2e.json' }]],
  use: {
    baseURL: resolveOptionalOrigin('BASE_URL'),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
