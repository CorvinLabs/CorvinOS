/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * Shared helpers for the Forge LIVE suite (playwright.forge-live.config.ts).
 * Everything goes through the console's real transport: a browser session
 * (local-login) and the same /v1/console routes the panels call.
 */
import { expect, request, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
import { resolveOptionalOrigin } from '../../e2e/sandbox-origin';

// Sandbox by default; the live :8765 console only with CORVIN_E2E_ALLOW_LIVE=1 and FORGE_LIVE_URL.
export const BASE = resolveOptionalOrigin('FORGE_LIVE_URL');
export const API = `${BASE}/v1/console`;
export const PREFIX = 'e2e_test_';
export const NAMES = {
  skill: `${PREFIX}skill`,
  tool: `${PREFIX}tool`, // forged as assistant.e2e_test_tool (namespace gate)
  plugin: `${PREFIX}plugin`,
  layer: `${PREFIX}layer`,
} as const;
export const REGEN = process.env.FORGE_LIVE_REGEN === '1';

export type Sub = 'skill' | 'tool' | 'plugin' | 'layer';
export type Api = { ctx: APIRequestContext; csrf: string };

export async function apiSession(): Promise<Api> {
  const ctx = await request.newContext({ baseURL: BASE });
  const login = await ctx.get('/v1/console/auth/local-login');
  expect(login.ok(), 'local-login on the live console').toBeTruthy();
  const who = await (await ctx.get('/v1/console/auth/whoami')).json();
  return { ctx, csrf: who.csrf_token };
}

export const post = (s: Api, p: string, data?: unknown, csrf: string | null = s.csrf) =>
  s.ctx.post(`/v1/console${p}`, { data, headers: csrf === null ? {} : { 'X-CSRF-Token': csrf } });
export const get = (s: Api, p: string) => s.ctx.get(`/v1/console${p}`);

/** A page logged in to the live console. */
export async function uiLogin(context: BrowserContext): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${BASE}/v1/console/auth/local-login`);
  await page.waitForURL(/\/console\//);
  return page;
}

export const SUB_MARKER: Record<Sub, string> = {
  skill: 'submit-generation', tool: 'tool-forge', plugin: 'plugin-forge', layer: 'forge-layer-panel',
};

export async function openGenerator(page: Page, sub: Sub) {
  await page.goto(`${BASE}/console/app/forge?tab=generator&sub=${sub}`);
  // Positive control: the sub-panel's own test id, not just "the page rendered".
  await expect(page.getByTestId(SUB_MARKER[sub])).toBeVisible({ timeout: 30_000 });
}

/** Poll until `fn` returns truthy; returns its value. */
export async function until<T>(fn: () => Promise<T | false | null | undefined>, what: string, ms = 60_000, step = 1_000): Promise<T> {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t0 > ms) throw new Error(`timed out after ${ms} ms waiting for ${what}`);
    await new Promise((r) => setTimeout(r, step));
  }
}

export async function listTools(s: Api): Promise<any[]> { return (await (await get(s, '/forge/tools')).json()).tools ?? []; }
export async function listSkills(s: Api): Promise<any[]> { return (await (await get(s, '/forge/skills')).json()).skills ?? []; }
export async function listLayers(s: Api): Promise<any[]> { return (await (await get(s, '/layer-forge/definitions')).json()).items ?? []; }
export async function listPlugins(s: Api): Promise<any[]> { return (await (await get(s, '/forge-creator/plugins')).json()).plugins ?? []; }
