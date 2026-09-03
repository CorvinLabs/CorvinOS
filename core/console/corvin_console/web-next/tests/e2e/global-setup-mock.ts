/**
 * Global setup for E2E tests with mocked authentication (Phase 4 k=3)
 *
 * Used when the backend is not available; simulates auth state without
 * making real API calls.
 */

import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const MOCK_AUTH_STATE = {
  cookies: [
    {
      name: 'session',
      value: 'mock-session-token-12345',
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      secure: false,
      sameSite: 'Strict' as const,
      expires: Date.now() / 1000 + 86400, // 1 day
    },
  ],
  origins: [
    {
      origin: 'http://127.0.0.1:8765',
      localStorage: [
        {
          name: 'corvin_user_id',
          value: 'test-user-001',
        },
        {
          name: 'corvin_tenant_id',
          value: '_default',
        },
      ],
    },
  ],
};

async function globalSetup() {
  console.log('[Global Setup] Using mocked authentication (no backend required)');

  // Write mock auth state to file
  const authPath = path.join(__dirname, 'auth-state.json');
  fs.writeFileSync(authPath, JSON.stringify(MOCK_AUTH_STATE, null, 2));

  console.log('[Global Setup] Mock auth state written to', authPath);

  return async () => {
    // Cleanup: optional, could delete auth-state.json here
    console.log('[Global Setup] Teardown complete');
  };
}

export default globalSetup;
