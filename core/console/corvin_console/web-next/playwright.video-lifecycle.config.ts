// Video Producer data lifecycle (T-0092: delete with confirmation, cancel, storage quota) in a REAL browser against an ISOLATED console:
// the real corvin_gateway.app + the built dist/, a throwaway CORVIN_HOME seeded with finished jobs, a job being
// produced by the plugin's real process runner (stand-in job process) and a 1 GB quota. The live console on :8765
// is never touched.
//
//   npm run build   # or scripts/console-deploy.sh — the spec drives the built bundle
//   npx playwright test -c playwright.video-lifecycle.config.ts
import { defineConfig, devices } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const dir = path.dirname(fileURLToPath(import.meta.url));
const harness = path.join(dir, 'tests/e2e/video-lifecycle/harness/isolated_vp_lifecycle_backend.py');
const python = path.resolve(dir, '../../.venv/bin/python');
export const VL_PORT = Number(process.env.VL_E2E_PORT ?? 8797);
export const VL_HOME = process.env.VL_E2E_HOME ?? '/tmp/corvin-vl-e2e';

export default defineConfig({
  testDir: './tests/e2e/video-lifecycle',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${VL_PORT}`, trace: 'retain-on-failure', ...devices['Desktop Chrome'] },
  projects: [{ name: 'chromium' }],
  webServer: {
    command: `${python} ${harness}`,
    url: `http://127.0.0.1:${VL_PORT}/healthz`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
    env: { VL_E2E_HOME: VL_HOME, VL_E2E_PORT: String(VL_PORT) },
  },
});
