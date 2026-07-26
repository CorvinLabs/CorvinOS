import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    // Dev-server port comes from vite.config.ts (server.port = 5173). This said
    // 3000, which no server here ever listens on — specs that rely on the
    // managed webServer could not run at all, and the ones that do run point at
    // a live console via their own BASE_URL/GATEWAY env instead.
    baseURL: process.env.CONSOLE_BASE_URL || 'http://localhost:5173',
    trace: 'on-first-retry',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    command: 'npm run dev',
    // The app is served under vite's base path (`/console/`, see vite.config.ts).
    // Probing the bare origin only ever gets a 302, so Playwright never saw the
    // server as ready and every run without an externally started dev server
    // died on "Timed out waiting 60000ms from config.webServer".
    url: (process.env.CONSOLE_BASE_URL || 'http://localhost:5173') + '/console/',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
