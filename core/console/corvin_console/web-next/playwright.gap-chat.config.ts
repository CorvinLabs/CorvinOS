// gap/chat proof: runs ONLY chat-gap-release.spec.ts against a sandbox console
// (GC_BASE). No webServer, no global login: the spec injects its own session cookie.
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: ['chat-gap-release.spec.ts', 'chat-audit-graphs.spec.ts'],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: { trace: 'off' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
