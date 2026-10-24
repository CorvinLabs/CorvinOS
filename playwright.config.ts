/**
 * Playwright Configuration for CorvinOS E2E Tests
 *
 * Tests the complete UI flow, feature flags, token metrics, and context pipeline
 */

import { defineConfig, devices } from '@playwright/test';
import { SANDBOX_COMMAND_PREFIX, resolveConsoleBaseUrl, sandboxServerEnv } from './core/console/corvin_console/web-next/tests/e2e/sandbox-origin';

/* Sandbox origin by default; :8765 (live) only with CORVIN_E2E_ALLOW_LIVE=1. */
const CONSOLE_URL = resolveConsoleBaseUrl();

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: './scripts',
  /* Run tests in files that end with .spec.ts */
  testMatch: '**/*.spec.ts',

  /* Run your local dev server before starting the tests */
  webServer: {
    // Throwaway CORVIN_HOME / XDG_CONFIG_HOME / HOME / absolute anchor key, FORGE_ROOT unset:
    // the standalone console otherwise defaults to the operator's ~/.corvin.
    command: `${SANDBOX_COMMAND_PREFIX} python -m corvin_console.standalone --port ${new URL(CONSOLE_URL).port}`,
    env: sandboxServerEnv('root-scripts'),
    url: CONSOLE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },

  /* Fail on console errors / warnings */
  fullyParallel: false,

  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,

  /* Opt out of parallel tests on CI */
  workers: process.env.CI ? 1 : 4,

  /* Reporter to use */
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['json', { outputFile: 'test-results.json' }],
    ['junit', { outputFile: 'junit.xml' }],
  ],

  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: CONSOLE_URL,

    /* Collect trace when retrying the failed test */
    trace: 'on-first-retry',

    /* Screenshot on failure */
    screenshot: 'only-on-failure',

    /* Video on failure */
    video: 'retain-on-failure',

    /* Navigation timeout */
    navigationTimeout: 30000,

    /* Action timeout */
    actionTimeout: 10000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },

    /* Test against Firefox */
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },

    /* Test against WebKit */
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],

  /* Run your local dev server before starting the tests */
  outputFolder: 'test-results',
});
