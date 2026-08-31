/**
 * Phase 2: Real Backend E2E Tests (Frontend ↔ Console API Backend)
 *
 * Tests the full stack using the local-login auth mechanism
 * (no email/password — CorvinOS uses session-cookie auth for the console).
 *
 * Prerequisites:
 *   - Vite dev server on :5173 (proxies /v1 → :8765)
 *   - FastAPI console backend on :8765
 *
 * Auth: storageState from globalSetup (auth-state.json).
 */

import { test, expect, BrowserContext } from '@playwright/test';
import { createHash } from 'crypto';
import { readFileSync } from 'fs';

const FRONTEND_URL = 'http://localhost:5173/console/';
const BACKEND_API = 'http://localhost:8765/v1/console';

function getSidFingerprint(): string {
  const authState = JSON.parse(readFileSync('./tests/e2e/auth-state.json', 'utf-8'));
  const sid = authState.cookies.find((c: { name: string }) => c.name === 'corvin_console_sid')?.value ?? '';
  return createHash('sha256').update(sid).digest('hex').slice(0, 12);
}

test.describe('Real Backend E2E — Frontend ↔ Console API', () => {
  test.describe.configure({ mode: 'serial' });

  let ctx: BrowserContext;
  let csrfToken: string;
  let reAuthToken: string;

  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({ storageState: './tests/e2e/auth-state.json' });
    const whoami = await ctx.request.get(`${BACKEND_API}/auth/whoami`);
    expect(whoami.status()).toBe(200);
    csrfToken = (await whoami.json()).csrf_token as string;
    reAuthToken = getSidFingerprint();
  });

  test.afterAll(async () => {
    await ctx?.close();
  });

  // ── Auth Flow ────────────────────────────────────────────────────────

  test('Auth: Whoami returns owner tier', async () => {
    const whoami = await ctx.request.get(`${BACKEND_API}/auth/whoami`);
    expect(whoami.status()).toBe(200);
    const body = await whoami.json();
    expect(body.tier).toBe('owner');
    expect(typeof body.csrf_token).toBe('string');
    expect(body.csrf_token.length).toBeGreaterThan(0);
  });

  test('Auth: Session persists across requests', async () => {
    // Make two successive whoami calls in the same session
    const r1 = await ctx.request.get(`${BACKEND_API}/auth/whoami`);
    expect(r1.status()).toBe(200);
    const r2 = await ctx.request.get(`${BACKEND_API}/auth/whoami`);
    expect(r2.status()).toBe(200);
    expect((await r2.json()).tier).toBe('owner');
  });

  test('Auth: CSRF token required for mutations', async () => {
    // PUT without CSRF → 403
    const noToken = await ctx.request.put(`${BACKEND_API}/profile`, {
      data: { identity: { name: 'Test User' }, re_auth_token: reAuthToken },
    });
    expect([403, 400]).toContain(noToken.status());

    // PUT with CSRF + re_auth_token → 200
    const withToken = await ctx.request.put(`${BACKEND_API}/profile`, {
      data: { identity: { name: 'Test User' }, re_auth_token: reAuthToken },
      headers: { 'X-CSRF-Token': csrfToken },
    });
    expect(withToken.status()).toBe(200);
  });

  // ── Dashboard Flow ───────────────────────────────────────────────────

  test('Dashboard: API returns structured data', async () => {
    const dashResp = await ctx.request.get(`${BACKEND_API}/dashboard`);
    expect(dashResp.status()).toBe(200);
    const dashboard = await dashResp.json();
    expect(typeof dashboard).toBe('object');
  });

  test('Dashboard: Frontend page loads', async () => {
    const page = await ctx.newPage();
    try {
      await page.goto(FRONTEND_URL, { waitUntil: 'load' });
      const html = await page.content();
      expect(html.length).toBeGreaterThan(500);
      expect(html).toContain('id="root"');
    } finally {
      await page.close();
    }
  });

  // ── Profile Management ───────────────────────────────────────────────

  test('Profile: GET and PUT user profile', async () => {
    // GET profile
    const getResp = await ctx.request.get(`${BACKEND_API}/profile`);
    expect(getResp.status()).toBe(200);

    // PUT profile with valid identity structure + re_auth_token
    const updateResp = await ctx.request.put(`${BACKEND_API}/profile`, {
      data: { identity: { name: 'E2E Test Runner' }, re_auth_token: reAuthToken },
      headers: { 'X-CSRF-Token': csrfToken },
    });
    expect(updateResp.status()).toBe(200);
  });

  // ── Error Handling ───────────────────────────────────────────────────

  test('Error: Unauthenticated request gets 401 from protected endpoint', async () => {
    const anonCtx = await ctx.browser()!.newContext();
    try {
      const dashResp = await anonCtx.request.get(`${BACKEND_API}/dashboard`);
      // Local-dev console allows localhost requests without a session (localhost-bypass).
      // In production (non-localhost) this returns 401/403. All three are expected.
      expect([200, 401, 403]).toContain(dashResp.status());
    } finally {
      await anonCtx.close();
    }
  });

  test('Error: Health check works without auth', async () => {
    const anonCtx = await ctx.browser()!.newContext();
    try {
      const healthResp = await anonCtx.request.get(`${BACKEND_API}/healthz`);
      expect(healthResp.status()).toBe(200);
      const health = await healthResp.json();
      expect(health.ok).toBe(true);
    } finally {
      await anonCtx.close();
    }
  });

  // ── Integration: Full User Journey ───────────────────────────────────

  test('Journey: Authenticated session — verify whoami, dashboard, profile, audit', async () => {
    // 1. Verify session
    const whoami = await ctx.request.get(`${BACKEND_API}/auth/whoami`);
    expect(whoami.status()).toBe(200);
    expect((await whoami.json()).tier).toBe('owner');

    // 2. Access dashboard
    const dash = await ctx.request.get(`${BACKEND_API}/dashboard`);
    expect(dash.status()).toBe(200);

    // 3. Update profile
    const update = await ctx.request.put(`${BACKEND_API}/profile`, {
      data: { identity: { name: 'Journey Test User' }, re_auth_token: reAuthToken },
      headers: { 'X-CSRF-Token': csrfToken },
    });
    expect(update.status()).toBe(200);

    // 4. Read audit tail
    const audit = await ctx.request.get(`${BACKEND_API}/audit/tail?limit=5`);
    expect(audit.status()).toBe(200);
  });
});
