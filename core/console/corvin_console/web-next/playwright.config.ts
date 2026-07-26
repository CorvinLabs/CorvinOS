import { defineConfig, devices } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const _dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  // MEASURED, do not "fix" this to false without re-measuring. These are integration
  // tests against one live console, and adr-0124-extensibility does pass 1/1 standalone
  // while contributing 12 failures to a parallel run — so contention is real. But
  // serialising made it far WORSE, not better: fullyParallel:false with 2 workers gave
  // 109 passed / 189 failed, against 299 passed / 75 failed at fullyParallel:true with
  // 4 workers (both runs measured alone on an otherwise idle machine, 2026-07-26).
  // Plausible reason: with file-level scheduling the specs that make real LLM calls
  // (nordtech-command-center) monopolise a worker and everything queued behind them
  // times out against the same busy backend. Whatever the mechanism, the data says
  // test-level parallelism wins here.
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // Cap it: `undefined` means one worker per CPU core, which on this machine meant 8
  // browsers hammering a single backend. 4 is what the 299-passed measurement used.
  workers: process.env.CI ? 1 : 4,
  reporter: 'html',

  // Log in ONCE and share the session with every spec.
  //
  // This wiring existed only in playwright.adr0124.config.ts, so the DEFAULT config
  // — the one `npm run test:e2e` uses, and therefore `npm run test:all` and
  // `npm run test:production` — ran all 509 specs unauthenticated. Measured
  // 2026-07-26: 171 passed, ~109 failed on `whoami failed` / `getCsrf` /
  // "Execution context was destroyed", and the run bailed with 229 never run.
  // Specs say "Session is provided via storageState" in their own comments, which
  // was true of the machinery and false of the config: the project's own release
  // gate could not pass on any machine.
  //
  // A spec that TESTS the login/logout flow itself must start anonymous:
  //   test.use({ storageState: { cookies: [], origins: [] } });
  globalSetup: './tests/e2e/global-setup-adr0124.ts',

  use: {
    storageState: path.join(_dirname, 'tests/e2e/auth-state.json'),
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
