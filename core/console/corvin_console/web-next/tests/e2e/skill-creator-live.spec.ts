/**
 * Skill Creator — live wiring proof (ADR-0405).
 *
 * Unlike skill-creator.spec.ts, this file stubs NOTHING. It seeds fictional
 * skills into the real tenant SkillForge registry through the real HTTP API,
 * then drives the real panel against the real endpoints: the library lists
 * them, View loads the real body, Delete really removes them.
 *
 * Why both files exist: the stubbed suite pins the panel's behaviour cheaply
 * and deterministically; this one proves the buttons are wired to endpoints
 * that exist and do what they claim. A stubbed suite alone passes happily
 * against a button with no backend — which is exactly how View and Delete
 * shipped inert.
 *
 * Generation itself is NOT triggered here: a real run spends minutes of
 * engine time on the operator's subscription. The seeding uses the same
 * registry the generator writes to, so what the panel sees is
 * indistinguishable from a generated skill. The full generate→promote path
 * against the live engine is covered by test_live_generation (opt-in) in
 * core/console/tests/test_skill_creator_e2e.py.
 */
import { test, expect, request as pwRequest, type APIRequestContext } from '@playwright/test';
import { execFileSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const _dirname = path.dirname(fileURLToPath(import.meta.url));
const ORIGIN = new URL(process.env.CONSOLE_BASE_URL || 'http://127.0.0.1:8765/console').origin;
const API = `${ORIGIN}/v1/console`;

/** Fictional skills, prefixed so a stray one is obvious and easy to purge. */
const PREFIX = 'assistant.e2efixture_';
const FIXTURES = [
  {
    name: `${PREFIX}alpha`,
    description: 'E2E fixture: pretends to validate JSON files and report line numbers.',
    body: '# E2E Fixture Alpha\n\n1. Read the file\n2. Pretend to parse it\n3. Report a fictional finding',
  },
  {
    name: `${PREFIX}beta`,
    description: 'E2E fixture: pretends to lint YAML for duplicate keys.',
    body: '# E2E Fixture Beta\n\n1. Read the YAML\n2. Pretend to find duplicates\n3. Report them',
  },
];

function cookieHeader(): string {
  const raw = JSON.parse(
    fs.readFileSync(path.join(_dirname, 'auth-state.json'), 'utf-8'),
  ) as { cookies: Array<{ name: string; value: string }> };
  return raw.cookies.map((c) => `${c.name}=${c.value}`).join('; ');
}

async function apiContext(): Promise<{ ctx: APIRequestContext; csrf: string }> {
  // Absolute URLs throughout: a leading-slash path resolves against the
  // ORIGIN, not against a baseURL that carries a path prefix, so
  // `/auth/whoami` against baseURL `.../v1/console` would 404 on `/auth/whoami`.
  const ctx = await pwRequest.newContext({
    extraHTTPHeaders: { Cookie: cookieHeader() },
  });
  const who = await ctx.get(`${API}/auth/whoami`);
  if (!who.ok()) throw new Error(`whoami failed: ${who.status()}`);
  return { ctx, csrf: (await who.json()).csrf_token };
}

const REPO = path.resolve(_dirname, '../../../../../..');
const SEED = path.join(_dirname, 'fixtures', 'seed_skills.py');
const PY = process.env.CORVIN_E2E_PYTHON || path.join(REPO, '.venv', 'bin', 'python');

/**
 * Seed through `registry_bridge` — the same path the generator promotes
 * through — rather than an HTTP fixture endpoint. The Skill-Creator has no
 * create-without-generating route by design (creating a skill means running
 * the phases), and adding one to the production API to make a test
 * convenient is how test-only surface ends up shipped.
 */
function fixtureCmd(action: 'seed' | 'purge', args: string[] = []): unknown {
  const out = execFileSync(PY, [SEED, action, ...args], {
    cwd: REPO,
    env: { ...process.env, CORVIN_HOME: process.env.CORVIN_HOME || path.join(REPO, '.corvin') },
    encoding: 'utf-8',
  });
  return JSON.parse(out);
}

// Purge first as well as last: a previously crashed run must not fail this one.
test.beforeAll(() => fixtureCmd('purge', ['--prefix', PREFIX]));
test.afterAll(() => fixtureCmd('purge', ['--prefix', PREFIX]));

test.describe('Skill Creator — live endpoints', () => {
  test('the library, View and Delete work against the real API', async ({ page }) => {
    const { ctx, csrf } = await apiContext();

    // ── seed ────────────────────────────────────────────────────────────
    fixtureCmd('seed', ['--json', JSON.stringify(FIXTURES)]);

    const listed = await (await ctx.get(`${API}/skill-creator/skills`)).json();
    const names = listed.skills.map((s: { name: string }) => s.name);
    expect(names).toContain(FIXTURES[0].name);
    expect(names).toContain(FIXTURES[1].name);

    // ── the panel shows them ────────────────────────────────────────────
    await page.goto('/console/app/skills', { waitUntil: 'domcontentloaded' });
    await expect(
      page.getByRole('heading', { name: 'Skill Creator', exact: true }),
    ).toBeVisible({ timeout: 20000 });

    const rowA = page.locator(`[data-skill="${FIXTURES[0].name}"]`);
    await expect(rowA).toBeVisible({ timeout: 15000 });
    // Seeded without a grade → below the injection gate → must read "inert".
    await expect(rowA.getByText('inert', { exact: true })).toBeVisible();

    // ── View loads the real body ────────────────────────────────────────
    await rowA.getByRole('button', { name: 'View' }).click();
    const viewer = page.getByTestId('skill-viewer');
    await expect(viewer).toBeVisible();
    await expect(viewer.locator('pre')).toContainText('E2E Fixture Alpha');

    // ── Refine targets it (no run started) ──────────────────────────────
    await page.getByTestId('viewer-refine').click();
    await expect(page.getByTestId('refine-banner')).toContainText(FIXTURES[0].name);
    await page.getByTestId('cancel-refine').click();

    // ── Delete really deletes ───────────────────────────────────────────
    await page.getByTestId(`delete-${FIXTURES[0].name}`).click();
    await page.getByTestId('confirm-delete').click();
    await expect(page.getByTestId('skill-toast')).toContainText('Deleted');
    await expect(rowA).toHaveCount(0, { timeout: 15000 });

    // Gone from the API too, not just from the rendered list.
    const afterDelete = await (await ctx.get(`${API}/skill-creator/skills`)).json();
    const remaining = afterDelete.skills.map((s: { name: string }) => s.name);
    expect(remaining).not.toContain(FIXTURES[0].name);
    expect(remaining).toContain(FIXTURES[1].name);

    await ctx.dispose();
  });
});
