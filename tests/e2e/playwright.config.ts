/**
 * Playwright Configuration for Task Graph Visualization E2E Tests
 *
 * Tests the Task Graph component:
 * - API endpoints for graph data
 * - Graph rendering and visualization
 * - User interactions (pan, zoom, filter, search)
 * - Responsive design (mobile, tablet, desktop)
 * - Accessibility compliance
 * - Performance metrics
 * - Error handling
 */

import { defineConfig, devices } from '@playwright/test';
import { LIVE_ONLY_SPEC_GLOBS, LIVE_OPT_IN_ENV, SANDBOX_COMMAND_PREFIX, resolveConsoleBaseUrl, sandboxServerEnv } from '../../core/console/corvin_console/web-next/tests/e2e/sandbox-origin';

/* Sandbox origin by default; :8765 (live) only with CORVIN_E2E_ALLOW_LIVE=1. */
const CONSOLE_URL = resolveConsoleBaseUrl();

export default defineConfig({
  testDir: '.', // this file lives in tests/e2e; './tests/e2e' pointed at tests/e2e/tests/e2e
  testMatch: '**/*.spec.ts',
  // keep-live specs (header names the live console) need the explicit opt-in.
  testIgnore: process.env[LIVE_OPT_IN_ENV] === '1' ? undefined : LIVE_ONLY_SPEC_GLOBS,

  /* Run local dev server before tests */
  webServer: {
    // Throwaway CORVIN_HOME / XDG_CONFIG_HOME / HOME / absolute anchor key, FORGE_ROOT unset:
    // the standalone console otherwise defaults to the operator's ~/.corvin.
    command: `${SANDBOX_COMMAND_PREFIX} python -m corvin_console.standalone --port ${new URL(CONSOLE_URL).port}`,
    env: sandboxServerEnv('root-tests-e2e'),
    url: CONSOLE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },

  /* Parallel execution settings */
  fullyParallel: true,
  workers: process.env.CI ? 1 : 4,

  /* Retry strategy */
  retries: process.env.CI ? 2 : 0,

  /* Timeouts */
  timeout: 30 * 1000,
  navigationTimeout: 30 * 1000,

  /* Reporters */
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['json', { outputFile: 'test-results-graph.json' }],
    ['junit', { outputFile: 'junit-graph.xml' }],
    ['list'],
  ],

  use: {
    baseURL: CONSOLE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    navigationTimeout: 30000,
    actionTimeout: 10000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 5'] },
    },
    {
      name: 'tablet',
      use: { ...devices['iPad Pro'] },
    },
  ],

  outputFolder: 'test-results',
});
