// @vitest-environment node
/**
 * W-0: test defaults must never hit the live console (:8765).
 *
 * Proofs:
 *  1. the default Playwright config (no env) targets the sandbox, not :8765;
 *  2. the origin policy refuses :8765 without CORVIN_E2E_ALLOW_LIVE=1 and allows it with;
 *  3. the ADR-0124 global setup refuses :8765 at load, before any login or browser launch;
 *  4. a real `playwright test --list` subprocess pointed at :8765 refuses without the
 *     opt-in, and loads with it (config load is where the refusal lives).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  SANDBOX_CONSOLE_ORIGIN,
  assertNotLiveConsole,
  isLiveConsoleUrl,
  mayReuseDevServer,
  resolveConsoleBaseUrl,
  resolveConsoleOrigin,
  resolveOptionalOrigin,
} from '../e2e/sandbox-origin';

const WEB_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const LIVE_URL = 'http://127.0.0.1:8765/console';
// A spec that is not in the keep-live list, so the subprocess checks load exactly one file.
const ONE_SPEC = 'tests/e2e/chat-composer-autosize.spec.ts';

function cleanEnv(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.CONSOLE_BASE_URL;
  delete env.CORVIN_E2E_ALLOW_LIVE;
  delete env.BASE_URL;
  return { ...env, ...extra };
}

describe('origin policy (tests/e2e/sandbox-origin.ts)', () => {
  it('the sandbox origin is not the live console', () => {
    expect(new URL(SANDBOX_CONSOLE_ORIGIN).port).not.toBe('8765');
  });

  it('detects the live port on any host, fail-closed', () => {
    expect(isLiveConsoleUrl('http://127.0.0.1:8765/console')).toBe(true);
    expect(isLiveConsoleUrl('http://localhost:8765')).toBe(true);
    expect(isLiveConsoleUrl('http://127.0.0.1:8799/console')).toBe(false);
    expect(isLiveConsoleUrl('not a url')).toBe(false);
  });

  it('resolves the sandbox when no env is set', () => {
    expect(resolveConsoleBaseUrl({})).toBe(`${SANDBOX_CONSOLE_ORIGIN}/console`);
    expect(resolveConsoleOrigin({})).toBe(SANDBOX_CONSOLE_ORIGIN);
  });

  it('refuses CONSOLE_BASE_URL on :8765 without the explicit opt-in', () => {
    expect(() => resolveConsoleBaseUrl({ CONSOLE_BASE_URL: LIVE_URL })).toThrow(/CORVIN_E2E_ALLOW_LIVE/);
    expect(() => resolveConsoleBaseUrl({ CONSOLE_BASE_URL: LIVE_URL, CORVIN_E2E_ALLOW_LIVE: '0' })).toThrow(
      /CORVIN_E2E_ALLOW_LIVE/,
    );
  });

  it('allows :8765 only with CORVIN_E2E_ALLOW_LIVE=1', () => {
    expect(resolveConsoleBaseUrl({ CONSOLE_BASE_URL: LIVE_URL, CORVIN_E2E_ALLOW_LIVE: '1' })).toBe(LIVE_URL);
  });

  it('refuses a spec-level URL variable on :8765 without the opt-in', () => {
    expect(() => resolveOptionalOrigin('BASE_URL', { BASE_URL: 'http://127.0.0.1:8765' })).toThrow(
      /CORVIN_E2E_ALLOW_LIVE/,
    );
    expect(resolveOptionalOrigin('BASE_URL', {})).toBe(SANDBOX_CONSOLE_ORIGIN);
  });

  it('assertNotLiveConsole is a no-op for the sandbox', () => {
    expect(() => assertNotLiveConsole(`${SANDBOX_CONSOLE_ORIGIN}/console`, 'x', {})).not.toThrow();
  });
});

describe('default Playwright config', () => {
  const saved = { ...process.env };

  beforeEach(() => {
    delete process.env.CONSOLE_BASE_URL;
    delete process.env.CORVIN_E2E_ALLOW_LIVE;
    vi.resetModules();
  });

  afterEach(() => {
    process.env = { ...saved };
  });

  it('baseURL with no env is the sandbox, not the live console', async () => {
    const mod = await import('../../playwright.config');
    const baseURL = mod.default.use?.baseURL;
    expect(baseURL).toBeDefined();
    expect(new URL(baseURL as string).port).not.toBe('8765');
    expect(new URL(baseURL as string).port).toBe(new URL(SANDBOX_CONSOLE_ORIGIN).port);
  });

  it('the dev server gateway is pinned to the run origin, never the live console', async () => {
    const mod = await import('../../playwright.config');
    const env = (mod.default.webServer as { env?: Record<string, string> }).env;
    expect(env?.CORVIN_GATEWAY_URL).toBe(SANDBOX_CONSOLE_ORIGIN);
  });
});

describe('existing dev server reuse (the CORVIN_GATEWAY_URL pin must apply)', () => {
  const saved = { ...process.env };

  beforeEach(() => {
    delete process.env.CONSOLE_BASE_URL;
    delete process.env.CORVIN_E2E_ALLOW_LIVE;
    delete process.env.CI;
    vi.resetModules();
  });

  afterEach(() => {
    process.env = { ...saved };
  });

  it('mayReuseDevServer: only an explicit live run outside CI may reuse', () => {
    expect(mayReuseDevServer({})).toBe(false);
    expect(mayReuseDevServer({ CORVIN_E2E_ALLOW_LIVE: '1' })).toBe(false); // opt-in, but the origin is still the sandbox
    expect(mayReuseDevServer({ CONSOLE_BASE_URL: `${SANDBOX_CONSOLE_ORIGIN}/console`, CORVIN_E2E_ALLOW_LIVE: '1' })).toBe(false);
    expect(mayReuseDevServer({ CONSOLE_BASE_URL: LIVE_URL, CORVIN_E2E_ALLOW_LIVE: '1', CI: 'true' })).toBe(false);
    expect(mayReuseDevServer({ CONSOLE_BASE_URL: LIVE_URL, CORVIN_E2E_ALLOW_LIVE: '1' })).toBe(true);
  });

  it('default config: a sandbox run never reuses a running :5173 dev server', async () => {
    const mod = await import('../../playwright.config');
    expect((mod.default.webServer as { reuseExistingServer?: boolean }).reuseExistingServer).toBe(false);
  });

  it('ADR-0124 config: a sandbox run never reuses a running :5173 dev server', async () => {
    const mod = await import('../../playwright.adr0124.config');
    const ws = mod.default.webServer as { reuseExistingServer?: boolean; env?: Record<string, string> };
    expect(ws.reuseExistingServer).toBe(false);
    expect(ws.env?.CORVIN_GATEWAY_URL).toBe(SANDBOX_CONSOLE_ORIGIN);
  });

  it('isolated config: the pinned dev server entry is never reused', async () => {
    const mod = await import('../../playwright.isolated.config');
    const servers = mod.default.webServer as Array<{ command: string; reuseExistingServer?: boolean }>;
    const dev = servers.find((s) => s.command === 'npm run dev');
    expect(dev?.reuseExistingServer).toBe(false);
  });

  it('default config: an explicit live run may reuse the operator dev server', async () => {
    process.env.CONSOLE_BASE_URL = LIVE_URL;
    process.env.CORVIN_E2E_ALLOW_LIVE = '1';
    const mod = await import('../../playwright.config');
    expect((mod.default.webServer as { reuseExistingServer?: boolean }).reuseExistingServer).toBe(true);
  });
});

describe('ADR-0124 global setup', () => {
  const saved = { ...process.env };

  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    process.env = { ...saved };
  });

  it('refuses :8765 at load, before any login or browser launch', async () => {
    process.env.CONSOLE_BASE_URL = LIVE_URL;
    delete process.env.CORVIN_E2E_ALLOW_LIVE;
    await expect(import('../e2e/global-setup-adr0124')).rejects.toThrow(/CORVIN_E2E_ALLOW_LIVE/);
  });
});

describe('playwright run and the live console', () => {
  const runList = (env: NodeJS.ProcessEnv) =>
    spawnSync(
      process.execPath,
      [path.join(WEB_ROOT, 'node_modules/@playwright/test/cli.js'), 'test', '--list', '-c', 'playwright.config.ts', ONE_SPEC],
      { cwd: WEB_ROOT, env, encoding: 'utf8', timeout: 120_000 },
    );

  it('refuses to run against :8765 without the opt-in', () => {
    const r = runList(cleanEnv({ CONSOLE_BASE_URL: LIVE_URL }));
    expect(`${r.stdout}\n${r.stderr}`).toContain('CORVIN_E2E_ALLOW_LIVE');
    expect(r.status).not.toBe(0);
  });

  it('loads against :8765 with the explicit opt-in', () => {
    const r = runList(cleanEnv({ CONSOLE_BASE_URL: LIVE_URL, CORVIN_E2E_ALLOW_LIVE: '1' }));
    expect(`${r.stdout}\n${r.stderr}`).not.toContain('refusing to run');
    expect(r.status).toBe(0);
  });

  it('a run with no env at all loads against the sandbox', () => {
    const r = runList(cleanEnv());
    expect(`${r.stdout}\n${r.stderr}`).not.toContain('refusing to run');
    expect(r.status).toBe(0);
  });
});
