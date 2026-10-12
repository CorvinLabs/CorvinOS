/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 21 — Forge intake end to end with the REAL engine (ADR-2254, T-0156): a table with an e-mail column is attached to the
 * Tool Forge, a tool is forged from it, and the engine's prompt-side view is checked through the run's recorded request:
 * the forged tool carries the column names from the projection, and the raw addresses appear nowhere in the run record.
 * Artifact: assistant.e2e_test_intake_tool (kept, like every e2e_test_* artifact; reused unless FORGE_LIVE_REGEN=1).
 */
import { expect, test } from '@playwright/test';
import { apiSession, get, listTools, openGenerator, REGEN, uiLogin, until, type Api } from './live';

const NAME = 'assistant.e2e_test_intake_tool';
const CSV = 'email,product,qty\n' + Array.from({ length: 40 }, (_, i) => `person${i}.secretaddr@example.org,widget${i % 5},${i}`).join('\n') + '\n';
let s: Api;
test.beforeAll(async () => { s = await apiSession(); });

test('forge a tool from an attached, redacted table and prove the raw addresses never reached the run', async ({ context }) => {
  test.setTimeout(30 * 60_000);
  if (!REGEN && (await listTools(s)).some((t) => t.name === NAME || t.tool_id === NAME)) return;
  const page = await uiLogin(context);
  await openGenerator(page, 'tool');
  await page.getByTestId('tool-request-attach-input').setInputFiles({ name: 'e2e_test_orders.csv', mimeType: 'text/csv', buffer: Buffer.from(CSV) });
  await expect(page.getByTestId('attachment-chip')).toHaveAttribute('data-verdict', 'admitted_redacted', { timeout: 30_000 });
  await page.getByTestId('tool-request').fill(
    `Create a tool named ${NAME} (pure Python stdlib, no network). It takes a "rows" list of objects shaped like the attached table `
    + 'and returns {"count": <number of rows>, "total_qty": <sum of the qty field>}. Include test cases. '
    + `The "name" field in your plan MUST be exactly ${NAME}.`);
  const resp = page.waitForResponse((r) => /forge-creator\/tool\/generate$/.test(r.url()) && r.request().method() === 'POST');
  await page.getByTestId('tool-request').press('Enter');
  const r = await resp;
  expect(r.status()).toBe(202);
  const runId = (await r.json()).run_id as string;
  const run = await until(async () => {
    try {
      const j = await (await get(s, `/forge-creator/status/${runId}`)).json();
      return ['success', 'failed'].includes(j.status) ? j : false;
    } catch { return false; } // a reset socket under load is not a verdict on the run
  }, 'tool run to finish', 25 * 60_000, 3_000);
  expect(run.status, `tool run failed: ${run.error ?? ''}`).toBe('success');
  expect(run.tool?.name, 'the tool keeps the requested e2e_test_ name').toBe(NAME);
  expect((await listTools(s)).some((t) => t.name === NAME || t.tool_id === NAME)).toBeTruthy();
  const ev = await get(s, `/forge/runs/${runId}/events?after=0&limit=500`);
  expect(ev.status(), 'positive control: the events route answered').toBe(200);
  const events = await ev.json();
  expect(JSON.stringify(events).length, 'positive control: the run recorded events').toBeGreaterThan(50);
  const everything = JSON.stringify(run) + JSON.stringify(events);
  // The engine wrote its sandbox test cases from the projection: the redacted placeholder, never an address.
  expect(JSON.stringify(run), 'the engine saw the redacted projection').toContain('<email>');
  expect(everything, 'a raw address in the run record').not.toContain('secretaddr');
});
