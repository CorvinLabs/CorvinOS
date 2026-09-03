/**
 * Comprehensive mock API setup for E2E tests (Phase 4 k=3)
 *
 * Intercepts all critical API calls so the Console App can run without
 * a live backend.
 */

import { Page } from '@playwright/test';

export async function setupMockApis(page: Page) {
  // Mock CSRF token endpoint
  await page.route('**/v1/console/auth/csrf', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ token: 'mock-csrf-token' }),
    });
  });

  // Mock whoami endpoint
  await page.route('**/v1/console/auth/whoami', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user_id: 'test-user-001',
        email: 'test@example.com',
        display_name: 'Test User',
        tenant_id: '_default',
      }),
    });
  });

  // Mock console manifest (capabilities)
  await page.route('**/v1/console/capabilities/manifest', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        version: '1.0',
        panels: [],
        nav_groups: [],
        features: {
          vibe_engineering: true,
          console_marketplace_panel: true,
        },
      }),
    });
  });

  // Mock OS engine setting
  await page.route('**/v1/console/os-engine', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        engine: 'claude_code',
        model: 'claude-opus-5',
      }),
    });
  });

  // Mock license info
  await page.route('**/v1/console/license', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tier: 'A',
        status: 'active',
      }),
    });
  });

  // Mock settings stream
  await page.route('**/v1/console/settings/stream', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        spec: {
          web_chat: { worker_engine: 'claude_code' },
        },
      }),
    });
  });

  // Mock Vibe Engineering data
  await page.route('**/v1/console/vibe-engineering/state', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        active_task: {
          task_id: 'test-task-001',
          description: 'Test task',
          status: 'in_progress',
          progress_percent: 65,
        },
        workers: [
          { name: 'Claude Code', status: 'running', latency_ms: 145, error_count: 0 },
        ],
        original_context: {
          task_description: 'Test',
          user_intent: 'Test',
          hash_sha256: 'abc123',
          is_valid: true,
        },
        pipeline_context: {
          entropy_score: 0.32,
          tier_1_count: 3,
          tier_2_count: 5,
          tier_3_count: 2,
        },
        talent: { score: 0.78 },
        learning_events: [],
        sessions: [],
        timestamp: new Date().toISOString(),
      }),
    });
  });

  // Mock landing personas
  await page.route('**/v1/console/landing/personas', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        personas: [
          { id: 'assistant', name: 'Assistant', description: 'General assistant' },
        ],
      }),
    });
  });

  console.log('[MockAPI] All API routes mocked');
}
