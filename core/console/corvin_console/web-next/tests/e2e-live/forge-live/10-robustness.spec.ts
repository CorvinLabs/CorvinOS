/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 10 — Robustness of all four Forge generators. No engine, no LLM spend: this is
 * the fast LDD loop (seconds). Two kinds of evidence:
 *
 *  • REAL API negatives against the live console — auth, CSRF, schema, traversal.
 *  • REAL UI under injected failure — the browser boundary is faked (page.route) so
 *    a refusal, a busy engine, a failed run, a vanished run and a reload mid-run
 *    can be driven deterministically. The panel code under test is the shipped bundle.
 */
import { request } from '@playwright/test';
import { expect, test } from '@playwright/test';
import { apiSession, BASE, get, openGenerator, post, uiLogin, type Api } from './live';

let s: Api;
test.beforeAll(async () => { s = await apiSession(); });

const runStatus = (kind: string, over: Record<string, unknown> = {}) => ({
  run_id: 'run-e2e-fake', kind, status: 'running', phase: kind === 'tool' ? 'sandbox_test' : 'generation',
  phases: kind === 'tool' ? ['planning', 'validation', 'sandbox_test', 'review', 'promotion'] : ['planning', 'generation', 'checks', 'review', 'staging'],
  progress: 40, message: 'working', engine: 'claude_code', error: null, failure_code: null, draft_saved: false,
  tool: null, plugin: null, ...over,
});

// ── the page itself ─────────────────────────────────────────────────────────

test.describe('the four generators are reachable', () => {
  for (const sub of ['skill', 'tool', 'plugin', 'layer'] as const) {
    test(`deep link ?tab=generator&sub=${sub} opens ${sub} forge`, async ({ context }) => {
      const page = await uiLogin(context);
      await openGenerator(page, sub);
      await expect(page.getByRole('tab', { name: new RegExp(`^${sub} forge`, 'i') })).toHaveAttribute('aria-selected', 'true');
    });
  }

  test('all four sub-tabs are on one row and switching keeps ?sub= in sync', async ({ context }) => {
    const page = await uiLogin(context);
    await openGenerator(page, 'skill');
    for (const [label, sub, marker] of [['Tool Forge', 'tool', 'tool-forge'], ['Plugin Forge', 'plugin', 'plugin-forge'],
      ['Layer Forge', 'layer', 'forge-layer-panel'], ['Skill Forge', 'skill', 'submit-generation']] as const) {
      await page.getByRole('tab', { name: label }).click();
      // The default sub-tab (skill) is the bare URL by design; every other one is pinned in ?sub=.
      if (sub === 'skill') await expect(page).not.toHaveURL(/sub=/);
      else await expect(page).toHaveURL(new RegExp(`sub=${sub}`));
      await expect(page.getByTestId(marker)).toBeVisible();
    }
  });

  test('legacy ?tab=creator / skill-forge / tool-forge / plugin-forge still land on the right generator', async ({ context }) => {
    const page = await uiLogin(context);
    for (const [legacy, marker] of [['creator', 'submit-generation'], ['tool-forge', 'tool-forge'], ['plugin-forge', 'plugin-forge']] as const) {
      await page.goto(`${BASE}/console/app/forge?tab=${legacy}`);
      await expect(page.getByTestId(marker)).toBeVisible({ timeout: 30_000 });
    }
  });

  test('an unknown ?sub= falls back to Skill Forge instead of a blank page', async ({ context }) => {
    const page = await uiLogin(context);
    await page.goto(`${BASE}/console/app/forge?tab=generator&sub=nonsense`);
    await expect(page.getByTestId('submit-generation')).toBeVisible({ timeout: 30_000 });
  });
});

// ── Tool Forge / Plugin Forge UI under failure ──────────────────────────────

for (const kind of ['tool', 'plugin'] as const) {
  test.describe(`${kind} forge UI`, () => {
    test('a request under 10 characters is refused inline and nothing is sent', async ({ context }) => {
      const page = await uiLogin(context);
      let sent = 0;
      await page.route('**/forge-creator/*/generate', (r) => { sent++; return r.abort(); });
      await openGenerator(page, kind);
      await page.getByTestId(`${kind}-request`).fill('too short');
      await page.getByTestId(`${kind}-submit`).click();
      await expect(page.getByRole('alert')).toContainText('at least 10 characters');
      expect(sent).toBe(0);
    });

    test('the submit button stays disabled for blank / whitespace-only input', async ({ context }) => {
      const page = await uiLogin(context);
      await openGenerator(page, kind);
      await expect(page.getByTestId(`${kind}-submit`)).toBeDisabled();
      await page.getByTestId(`${kind}-request`).fill('      ');
      await expect(page.getByTestId(`${kind}-submit`)).toBeDisabled();
    });

    test('a gate refusal (403) is shown to the user and the form stays usable', async ({ context }) => {
      const page = await uiLogin(context);
      await page.route('**/forge-creator/*/generate', (r) => r.fulfill({ status: 403, contentType: 'application/json',
        body: JSON.stringify({ detail: 'refused by house rules: not an acceptable use' }) }));
      await openGenerator(page, kind);
      await page.getByTestId(`${kind}-request`).fill('a request the house rules will refuse');
      await page.getByTestId(`${kind}-submit`).click();
      await expect(page.getByRole('alert')).toContainText('house rules');
      await expect(page.getByTestId(`${kind}-submit`)).toBeEnabled();
      await expect(page.getByTestId(`${kind}-request`)).toHaveValue('a request the house rules will refuse');
    });

    test('a busy engine (429) is shown, not swallowed', async ({ context }) => {
      const page = await uiLogin(context);
      await page.route('**/forge-creator/*/generate', (r) => r.fulfill({ status: 429, contentType: 'application/json',
        body: JSON.stringify({ detail: 'a generation is already running for this tenant' }) }));
      await openGenerator(page, kind);
      await page.getByTestId(`${kind}-request`).fill('a perfectly fine request that finds the engine busy');
      await page.getByTestId(`${kind}-submit`).click();
      await expect(page.getByRole('alert')).toContainText('already running');
    });

    test('a FAILED run shows the reason, the engine, and frees the form', async ({ context }) => {
      const page = await uiLogin(context);
      await page.route('**/forge-creator/*/generate', (r) => r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'accepted', run_id: 'run-e2e-fake', kind }) }));
      await page.route('**/forge-creator/status/run-e2e-fake', (r) => r.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify(runStatus(kind, { status: 'failed', progress: 60, message: 'Claude Code CLI not found. Install it or set CORVIN_CLAUDE_BIN',
          error: 'claude binary not found', failure_code: 'engine_unavailable' })) }));
      await openGenerator(page, kind);
      await page.getByTestId(`${kind}-request`).fill('a request whose engine is not installed here');
      await page.getByTestId(`${kind}-submit`).click();
      const run = page.getByTestId(`${kind}-run`);
      await expect(run).toContainText('FAILED');
      await expect(page.getByTestId(`${kind}-run-message`)).toContainText('Claude Code CLI not found');
      await expect(run).toContainText('Claude subscription (Claude Code CLI)');
      await expect(page.getByTestId(`${kind}-submit`)).toBeEnabled();
    });

    test('a run the server no longer knows (404) tells the user instead of spinning forever', async ({ context }) => {
      const page = await uiLogin(context);
      await page.route('**/forge-creator/*/generate', (r) => r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'accepted', run_id: 'run-e2e-gone', kind }) }));
      await page.route('**/forge-creator/status/run-e2e-gone', (r) => r.fulfill({ status: 404, contentType: 'application/json',
        body: JSON.stringify({ detail: 'Run not found: run-e2e-gone' }) }));
      await openGenerator(page, kind);
      await page.getByTestId(`${kind}-request`).fill('a request whose run id vanishes');
      await page.getByTestId(`${kind}-submit`).click();
      await expect(page.getByRole('alert')).toContainText('no longer known');
      await expect(page.getByTestId(`${kind}-submit`)).toBeEnabled();
    });

    test('a running run survives a hard reload and a tab switch (run id is re-attached)', async ({ context }) => {
      const page = await uiLogin(context);
      await page.route('**/forge-creator/*/generate', (r) => r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'accepted', run_id: 'run-e2e-fake', kind }) }));
      await page.route('**/forge-creator/status/run-e2e-fake', (r) => r.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify(runStatus(kind)) }));
      await openGenerator(page, kind);
      await page.getByTestId(`${kind}-request`).fill('a long request that is still running when the page reloads');
      await page.getByTestId(`${kind}-submit`).click();
      await expect(page.getByTestId(`${kind}-run`)).toContainText('RUNNING');
      await expect(page.getByTestId(`${kind}-submit`)).toBeDisabled(); // no second click while running
      await page.reload();
      await expect(page.getByTestId(`${kind}-run`)).toContainText('RUNNING');
      await expect(page.getByTestId(`${kind}-submit`)).toBeDisabled();
      const other = kind === 'tool' ? 'Plugin Forge' : 'Tool Forge';
      await page.getByRole('tab', { name: other }).click();
      await page.getByRole('tab', { name: new RegExp(`^${kind} forge`, 'i') }).click();
      await expect(page.getByTestId(`${kind}-run`)).toContainText('RUNNING');
    });

    test('a transient poll error (503) keeps the run alive; it does not end it', async ({ context }) => {
      const page = await uiLogin(context);
      let n = 0;
      await page.route('**/forge-creator/*/generate', (r) => r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'accepted', run_id: 'run-e2e-fake', kind }) }));
      await page.route('**/forge-creator/status/run-e2e-fake', (r) => (++n < 4
        ? r.fulfill({ status: 503, body: 'console restarting' })
        : r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runStatus(kind, { status: 'success', progress: 100, message: 'done' })) })));
      await openGenerator(page, kind);
      await page.getByTestId(`${kind}-request`).fill('a request that outlives a console restart');
      await page.getByTestId(`${kind}-submit`).click();
      await expect(page.getByTestId(`${kind}-run`)).toContainText('SUCCESS', { timeout: 30_000 });
    });
  });
}

test('only the plugin request carries panel_request; the tool request does not send it', async ({ context }) => {
  const page = await uiLogin(context);
  const bodies: Record<string, any> = {};
  await page.route('**/forge-creator/*/generate', async (r) => {
    bodies[r.request().url().includes('/plugin/') ? 'plugin' : 'tool'] = r.request().postDataJSON();
    await r.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: 'stop here' }) });
  });
  await openGenerator(page, 'plugin');
  await page.getByTestId('plugin-request').fill('a plugin that shows the freight dashboard');
  await page.getByTestId('plugin-panel-request').fill('a panel with a table of open shipments');
  await page.getByTestId('plugin-submit').click();
  await expect(page.getByRole('alert')).toContainText('stop here');
  expect(bodies.plugin).toMatchObject({ panel_request: 'a panel with a table of open shipments' });
  await openGenerator(page, 'tool');
  await page.getByTestId('tool-request').fill('a tool that sums freight weights');
  await page.getByTestId('tool-submit').click();
  await expect(page.getByRole('alert')).toContainText('stop here');
  expect(bodies.tool.panel_request ?? '').toBe('');
});

// ── Skill Forge UI ──────────────────────────────────────────────────────────

test.describe('skill forge UI', () => {
  test('generate is disabled until there is a request, and sends async:true with the CSRF token', async ({ context }) => {
    const page = await uiLogin(context);
    let seen: { body: any; csrf: string | undefined } | null = null;
    await page.route('**/skill-creator/generate', async (r) => {
      seen = { body: r.request().postDataJSON(), csrf: r.request().headers()['x-csrf-token'] };
      await r.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ detail: 'busy: one skill run at a time' }) });
    });
    await openGenerator(page, 'skill');
    await expect(page.getByTestId('submit-generation')).toBeDisabled();
    await page.getByTestId('skill-request').fill('a skill that checks pallet manifests');
    await page.getByTestId('submit-generation').click();
    await expect(page.getByText('busy: one skill run at a time')).toBeVisible();
    expect(seen!.body).toMatchObject({ user_request: 'a skill that checks pallet manifests', async: true });
    expect(seen!.csrf).toMatch(/^[0-9a-f]{64}$/);
    await expect(page.getByTestId('submit-generation')).toBeEnabled();
  });

  test('a failed skill run shows its error and the phase stepper', async ({ context }) => {
    const page = await uiLogin(context);
    await page.route('**/skill-creator/generate', (r) => r.fulfill({ status: 202, contentType: 'application/json',
      body: JSON.stringify({ status: 'accepted', run_id: 'run-e2e-skill', engine: 'claude_code', base_skill: null, message: 'ok' }) }));
    await page.route('**/skill-creator/status/run-e2e-skill', (r) => r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ run_id: 'run-e2e-skill', status: 'failed', phase: 'review', progress: 80, message: 'review failed',
        engine: 'claude_code', phases: ['planning', 'validation', 'ldd_iteration', 'review', 'promotion'],
        error: 'adversarial review confirmed a security finding', base_skill: null, skill: null }) }));
    await openGenerator(page, 'skill');
    await page.getByTestId('skill-request').fill('a skill the reviewer will reject');
    await page.getByTestId('submit-generation').click();
    await expect(page.getByTestId('phase-stepper')).toBeVisible();
    await expect(page.getByTestId('generation-error')).toContainText('confirmed a security finding');
    await expect(page.getByTestId('submit-generation')).toBeEnabled();
  });
});

// ── Layer Forge UI ──────────────────────────────────────────────────────────

test.describe('layer forge UI', () => {
  const MANIFEST = { id: 'e2e_test_fake', version: '0.1.0', targets: [{ layer_id: 'L34' }], quality_gates: [], enforcement_rules: [] };

  test('Plan needs both an id and an intent', async ({ context }) => {
    const page = await uiLogin(context);
    await openGenerator(page, 'layer');
    await expect(page.getByTestId('plan-layer-button')).toBeDisabled();
    await page.getByTestId('layer-id-input').fill('e2e_test_fake');
    await expect(page.getByTestId('plan-layer-button')).toBeDisabled();
    await page.getByTestId('layer-intent-input').fill('   ');
    await expect(page.getByTestId('plan-layer-button')).toBeDisabled();
    await page.getByTestId('layer-intent-input').fill('Enforce the L34 boundary');
    await expect(page.getByTestId('plan-layer-button')).toBeEnabled();
  });

  test('a failed PLAN (422) and an unavailable audit chain (503) are both surfaced', async ({ context }) => {
    const page = await uiLogin(context);
    await openGenerator(page, 'layer');
    await page.getByTestId('layer-id-input').fill('e2e_test_fake');
    await page.getByTestId('layer-intent-input').fill('Enforce the L34 boundary');
    for (const [status, text] of [[422, 'planner could not produce a valid manifest'], [503, 'audit chain write failed']] as const) {
      await page.route('**/layer-forge/plan', (r) => r.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ detail: text }) }));
      await page.getByTestId('plan-layer-button').click();
      await expect(page.getByTestId('plan-layer-error')).toContainText(text.split(' ')[0]);
      await page.unroute('**/layer-forge/plan');
    }
    await expect(page.getByText('Manifest preview')).toHaveCount(0); // a failed plan never shows a preview
  });

  test('preview → Discard creates nothing; preview → Create that hits a duplicate (409) shows the reason', async ({ context }) => {
    const page = await uiLogin(context);
    let created = 0;
    await page.route('**/layer-forge/plan', (r) => r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 'SUCCESS', manifest: MANIFEST }) }));
    await page.route('**/layer-forge/definitions', async (r) => {
      if (r.request().method() !== 'POST') return r.continue();
      created++;
      return r.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: { error: 'e2e_test_fake@0.1.0 already exists' } }) });
    });
    await openGenerator(page, 'layer');
    await page.getByTestId('layer-id-input').fill('e2e_test_fake');
    await page.getByTestId('layer-intent-input').fill('Enforce the L34 boundary');
    await page.getByTestId('plan-layer-button').click();
    await expect(page.getByText('Nothing is created yet')).toBeVisible();
    await page.getByRole('button', { name: 'Discard' }).click();
    await expect(page.getByText('Manifest preview')).toHaveCount(0);
    expect(created).toBe(0);
    await page.getByTestId('plan-layer-button').click();
    await page.getByTestId('create-layer-button').click();
    await expect(page.getByTestId('create-layer-error')).toBeVisible();
    expect(created).toBe(1);
    await expect(page.getByTestId('create-layer-success')).toHaveCount(0);
  });
});

// ── real API negatives against the live console ─────────────────────────────

test.describe('live API contract', () => {
  // BASE = resolveOptionalOrigin('FORGE_LIVE_URL') (./live): sandbox unless CORVIN_E2E_ALLOW_LIVE=1.
  const anon = async () => request.newContext({ baseURL: BASE });

  test('every generator route refuses an anonymous caller (401) — nothing is spawned', async () => {
    const a = await anon();
    for (const [method, p] of [['POST', '/forge-creator/tool/generate'], ['POST', '/forge-creator/plugin/generate'],
      ['POST', '/skill-creator/generate'], ['POST', '/layer-forge/plan'], ['POST', '/layer-forge/definitions']] as const) {
      const r = await a.fetch(`/v1/console${p}`, { method, data: { user_request: 'an anonymous request that must not run' } });
      expect(r.status(), `${method} ${p}`).toBe(401);
    }
    for (const p of ['/forge-creator/status/x', '/layer-forge/definitions', '/forge/tools', '/forge/skills', '/forge-creator/plugins']) {
      expect((await a.get(`/v1/console${p}`)).status(), p).toBe(401);
    }
  });

  test('a session without the CSRF token is refused (403) on every mutating route', async () => {
    for (const p of ['/forge-creator/tool/generate', '/forge-creator/plugin/generate', '/skill-creator/generate', '/layer-forge/plan', '/layer-forge/definitions']) {
      const r = await post(s, p, { user_request: 'a request without a csrf token', layer_id: 'x', intent: 'y' }, null);
      expect(r.status(), p).toBe(403);
    }
    const bad = await post(s, '/forge-creator/tool/generate', { user_request: 'a request with a wrong csrf token' }, 'f'.repeat(64));
    expect(bad.status()).toBe(403);
  });

  test('schema: too short, extra field, wrong type, unknown kind, panel_request on a tool', async () => {
    expect((await post(s, '/forge-creator/tool/generate', { user_request: 'short' })).status()).toBe(422);
    expect((await post(s, '/forge-creator/tool/generate', { user_request: 'long enough request', evil: 1 })).status()).toBe(422);
    expect((await post(s, '/forge-creator/tool/generate', { user_request: 12345678901 })).status()).toBe(422);
    expect((await post(s, '/forge-creator/tool/generate', { user_request: 'x'.repeat(4001) })).status()).toBe(422);
    expect((await post(s, '/forge-creator/skill/generate', { user_request: 'long enough request' })).status()).toBe(422);
    expect((await post(s, '/forge-creator/tool/generate', { user_request: 'a request with a panel', panel_request: 'a panel on a tool' })).status()).toBe(400);
  });

  test('status of an unknown / foreign / malformed run id is 404, never a 500 or another tenant\'s run', async () => {
    for (const id of ['nope', '0'.repeat(32), '..%2F..%2Fetc%2Fpasswd', 'a'.repeat(300)]) {
      const r = await get(s, `/forge-creator/status/${id}`);
      expect([404, 422], id).toContain(r.status());
    }
    expect((await post(s, '/forge-creator/tool/retry/nope')).status()).toBe(404);
  });

  test('forged-plugin paths reject traversal and non-forge names', async () => {
    for (const d of ['..', '..%2F..', '.env', 'A_B', 'x'.repeat(200)]) {
      expect([400, 404], d).toContain((await get(s, `/forge-creator/plugins/${d}`)).status());
    }
    expect((await get(s, '/forge-creator/plugins/does_not_exist')).status()).toBe(404);
    const del = await s.ctx.delete('/v1/console/forge-creator/plugins/does_not_exist', { headers: { 'X-CSRF-Token': s.csrf } });
    expect(del.status()).toBe(404);
  });

  test('layer API: bad ids and malformed manifests are 4xx and write nothing', async () => {
    const before = (await (await get(s, '/layer-forge/definitions')).json()).count;
    for (const id of ['../x', 'UPPER', '', 'a b', 'x'.repeat(300)]) {
      const r = await post(s, '/layer-forge/definitions', { id, version: '1.0.0', targets: [{ layer_id: 'L34' }], quality_gates: [], enforcement_rules: [] });
      expect([400, 422], `id=${JSON.stringify(id.slice(0, 20))}`).toContain(r.status());
    }
    for (const body of [{}, { id: 'e2e_test_bad' }, { id: 'e2e_test_bad', version: 'one' }, { id: 'e2e_test_bad', version: '1.0.0', targets: 'L34' },
      { id: 'e2e_test_bad', version: '1.0.0', targets: [], quality_gates: [{ gate_id: 'g', test_path: '../../etc/passwd' }], enforcement_rules: [] }]) {
      const r = await post(s, '/layer-forge/definitions', body);
      expect(r.status(), JSON.stringify(body)).toBeGreaterThanOrEqual(400);
      expect(r.status()).toBeLessThan(500);
    }
    expect((await (await get(s, '/layer-forge/definitions')).json()).count).toBe(before);
    expect((await get(s, '/layer-forge/definitions/..%2F..%2Fetc')).status()).toBeGreaterThanOrEqual(400);
    expect((await get(s, '/layer-forge/definitions/does.not.exist')).status()).toBe(404);
  });

  test('layer transition: unknown definition 404, illegal target status 4xx', async () => {
    expect((await post(s, '/layer-forge/definitions/does.not.exist/1.0.0/transition', { to_status: 'accepted' })).status()).toBe(404);
    const r = await post(s, '/layer-forge/definitions/does.not.exist/1.0.0/transition', { to_status: 'banana' });
    expect([404, 422]).toContain(r.status());
  });
});
