// Video Producer Quality tab (T-0091, ADR-2247 R2c) in a REAL browser against an ISOLATED console:
// the real corvin_gateway.app + the built dist/, a throwaway CORVIN_HOME seeded with the production
// report a real job wrote. The live console on :8765 is never touched.
//
//   npm run build   # or scripts/console-deploy.sh — the spec drives the built bundle
//   npx playwright test -c playwright.video-quality.config.ts
import { defineConfig, devices } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const dir = path.dirname(fileURLToPath(import.meta.url));
const harness = path.join(dir, 'tests/e2e/video-quality/harness/isolated_vp_backend.py');
const python = path.resolve(dir, '../../.venv/bin/python');
export const VQ_PORT = Number(process.env.VQ_E2E_PORT ?? 8796);
export const VQ_HOME = process.env.VQ_E2E_HOME ?? '/tmp/corvin-vq-e2e';

export default defineConfig({
  testDir: './tests/e2e/video-quality',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${VQ_PORT}`, trace: 'retain-on-failure', ...devices['Desktop Chrome'] },
  projects: [{ name: 'chromium' }],
  webServer: {
    command: `${python} ${harness}`,
    url: `http://127.0.0.1:${VQ_PORT}/healthz`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
    env: { VQ_E2E_HOME: VQ_HOME, VQ_E2E_PORT: String(VQ_PORT) },
  },
});
