// TEMPORARY (not committed): runs the E2E suite against an isolated console
// instance on :8799 with a fresh CORVIN_HOME, so production data on :8765 is
// never touched. Playwright owns both server lifecycles for the run's duration.
import base from './playwright.config';
import { defineConfig } from '@playwright/test';

export default defineConfig({
  ...base,
  webServer: [
    {
      // Isolated gateway/console backend with a throwaway CORVIN_HOME.
      command: 'bash scripts/start-isolated-e2e-backend.sh',
      url: 'http://127.0.0.1:8799/healthz',
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      // Vite dev server, proxying /v1 + /healthz to the isolated backend.
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: true,
      timeout: 60_000,
      env: { CORVIN_GATEWAY_URL: 'http://127.0.0.1:8799' },
    },
  ],
});
