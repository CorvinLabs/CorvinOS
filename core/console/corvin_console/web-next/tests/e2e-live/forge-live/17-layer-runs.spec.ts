/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 17 — Layer Forge operations are runs (ADR-2253, T-0151), proven LIVE with the real engine and the real console:
 * a keyboard-only PLAN shows its progress WHILE the request is going, Create runs the real gates, and both leave
 * a run (plan, create) with their phases and gate events. Forges a second layer artifact: e2e_test_layer_runs.
 */
import { expect, test } from '@playwright/test';
import { apiSession, get, listLayers, openGenerator, uiLogin } from './live';

const LAYER = 'e2e_test_layer_runs';

test('REAL: a keyboard PLAN shows live progress in the panel, Create runs the gates, both are runs', async ({ context }) => {
  test.setTimeout(15 * 60_000);
  const s = await apiSession();
  test.skip((await listLayers(s)).some((l) => l.id === LAYER) && process.env.FORGE_LIVE_REGEN !== '1', `${LAYER} already exists`);
  const seen = new Set(((await (await get(s, '/forge/runs?kind=layer&limit=200')).json()).runs as any[]).map((r) => r.run_id));

  const page = await uiLogin(context);
  await openGenerator(page, 'layer');
  await page.getByTestId('layer-id-input').fill('L34');
  const ta = page.getByTestId('layer-intent-input');
  await ta.fill(`The manifest id must be exactly "${LAYER}". Guard the data-review boundary: one quality gate that runs `
    + 'tests/layer_forge/test_schema.py, one compile_time enforcement rule for schema validation. Target layer L34. Version 1.0.0.');
  await ta.press('Enter');                                                 // PLAN by keyboard

  // LIVE: the feed appears while the PLAN request is still going (the id only arrives with the response)
  const feed = page.getByTestId('run-feed');
  await expect(feed).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('run-feed-count')).toContainText('live');
  await expect(feed).toContainText('Asking the engine for a manifest', { timeout: 30_000 });

  await expect(page.getByText('Manifest preview')).toBeVisible({ timeout: 8 * 60_000 });
  await expect(page.getByTestId('run-feed-count')).not.toContainText('live', { timeout: 20_000 });

  await page.getByTestId('create-layer-button').click();
  await expect(page).toHaveURL(/tab=layers/, { timeout: 120_000 });       // the Forge page moves to the Layers tab on success

  // the two operations are two runs, each with its own phases
  const rows = ((await (await get(s, '/forge/runs?kind=layer&limit=200')).json()).runs as any[]).filter((r) => !seen.has(r.run_id));
  const plan = rows.find((r) => r.operation === 'plan');
  const create = rows.find((r) => r.operation === 'create');
  expect(plan?.status).toBe('success');
  expect(plan?.phases).toEqual(['plan']);
  expect(create?.status, JSON.stringify(create)).toBe('success');
  expect(create.phases).toEqual(['validate', 'test', 'enforce', 'review', 'create']);
  expect(create.artifact_ref).toEqual({ kind: 'layer', id: `${LAYER}@1.0.0` });
  const ev = (await (await get(s, `/forge/runs/${create.run_id}/events?limit=500`)).json()).events as any[];
  expect(ev.some((e) => e.type === 'gate' && e.data.gate_id === 'schema' && e.data.status === 'PASS'), 'the real quality gate is an event').toBe(true);
  expect(ev.some((e) => e.type === 'gate' && e.phase === 'review' && e.data.verdict), 'the review verdict is an event').toBe(true);
  expect((await listLayers(s)).find((l) => l.id === LAYER)?.status).toMatch(/accepted|deployed/);
});
