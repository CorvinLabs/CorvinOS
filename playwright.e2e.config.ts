/**
 * Playwright config for the root-level console E2E specs (tests/e2e/*.spec.ts).
 *
 * The root playwright.config.ts targets ./scripts; this one runs the browser
 * suites under tests/e2e against an ALREADY RUNNING console (the systemd
 * corvin-webui.service on :8765, or CONSOLE_BASE_URL). It never starts a
 * server: a stale-bundle/backend mismatch must be visible, not papered over
 * by a fresh dev server.
 *
 *   BASE_URL=http://127.0.0.1:8765 npx playwright test -c playwright.e2e.config.ts
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 2,
  retries: 0,
  timeout: 60_000,
  reporter: [['line'], ['json', { outputFile: 'test-results/root-e2e.json' }]],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:8765',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
