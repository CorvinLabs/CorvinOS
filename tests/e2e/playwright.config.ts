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

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',

  /* Run local dev server before tests */
  webServer: {
    command: 'python -m corvin_console.standalone --port 8765',
    url: 'http://127.0.0.1:8765/console',
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
    baseURL: 'http://127.0.0.1:8765/console',
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
