// gap/chat proof: the chat specs against a SANDBOX console (never :8765).
//   CONSOLE_BASE_URL=http://127.0.0.1:<port>/console  — the sandbox console serving the build under test
//   tests/e2e/auth-state.json                         — that sandbox's session cookie (gitignored)
// chat-gap-release.spec.ts injects its own cookie from GC_COOKIE and needs GC_BASE/GC_ROOT.
// No webServer, no global login.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';
import { resolveConsoleOrigin } from './tests/e2e/sandbox-origin';

const _dirname = path.dirname(fileURLToPath(import.meta.url));
const authState = path.join(_dirname, 'tests/e2e/auth-state.json');

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: ['chat-gap-release.spec.ts', 'chat-audit-graphs.spec.ts', 'chat-slash-palette.spec.ts'],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: resolveConsoleOrigin(),
    storageState: fs.existsSync(authState) ? authState : undefined,
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
