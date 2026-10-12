/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 18 — the Runs tab: one history across all four Forges (ADR-2253, T-0152).
 *  • REAL: the table is the live console's real runs; filters, details and links are checked against the API.
 *  • FAKED at the browser boundary only: the retry action and an unreachable API.
 */
import { expect, test } from '@playwright/test';
import { apiSession, BASE, get, uiLogin } from './live';

const RUNS = `${BASE}/console/app/forge?tab=runs`;

test('REAL: the Runs tab lists the live console\'s runs of all four kinds, newest first', async ({ context }) => {
  const s = await apiSession();
  const api = (await (await get(s, '/forge/runs?limit=100')).json()) as { runs: any[]; count: number };
  const kinds = new Set(api.runs.map((r) => r.kind));
  test.skip(kinds.size < 4, `this install has runs of ${[...kinds].join(', ')} only - forge all four first (specs 20/16/17)`);
  const page = await uiLogin(context);
  await page.goto(RUNS);
  await expect(page.getByTestId('forge-runs')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('run-row')).toHaveCount(api.count);
  await expect(page.getByTestId('runs-count')).toContainText(`${api.count} run`);
  const firstRow = page.getByTestId('run-row').first();
  await expect(firstRow).toHaveAttribute('data-run-id', api.runs[0].run_id);   // the API's newest first
  for (const k of ['skill', 'tool', 'plugin', 'layer']) await expect(page.locator(`[data-testid="run-row"][data-kind="${k}"]`).first()).toBeVisible();
});

test('REAL: kind and status filters narrow the table to exactly what the API says; a combination with no match says so', async ({ context }) => {
  const s = await apiSession();
  const layer = (await (await get(s, '/forge/runs?kind=layer&limit=100')).json()) as { runs: any[]; count: number };
  test.skip(layer.count === 0, 'no layer runs yet');
  const page = await uiLogin(context);
  await page.goto(RUNS);
  await page.getByTestId('runs-kind').selectOption('layer');
  await expect(page.getByTestId('run-row')).toHaveCount(layer.count);
  await expect(page.locator('[data-testid="run-row"]:not([data-kind="layer"])')).toHaveCount(0);
  const failedTool = (await (await get(s, '/forge/runs?kind=tool&status=failed&limit=100')).json()).count as number;
  await page.getByTestId('runs-kind').selectOption('tool');
  await page.getByTestId('runs-status').selectOption('failed');
  await expect(page.getByTestId('run-row')).toHaveCount(failedTool);
  await page.getByTestId('runs-kind').selectOption('plugin');
  await page.getByTestId('runs-status').selectOption('running');
  await expect(page.getByTestId('runs-empty')).toContainText('No run matches these filters');
});

test('REAL: a row opens the same Production feed with the real event count; an artifact is a link to where it lives', async ({ context }) => {
  const s = await apiSession();
  const rows = (await (await get(s, '/forge/runs?kind=layer&status=success&limit=100')).json()).runs as any[];
  let target: any = null;
  let n = 0;
  for (const r of rows) {
    n = ((await (await get(s, `/forge/runs/${r.run_id}/events?limit=500`)).json()).events as any[]).length;
    if (n > 0 && r.artifact_ref) { target = r; break; }
  }
  test.skip(!target, 'no layer run with events and an artifact yet');
  const page = await uiLogin(context);
  await page.goto(RUNS);
  const row = page.locator(`[data-testid="run-row"][data-run-id="${target.run_id}"]`);
  await row.getByRole('link', { name: target.artifact_ref.id }).waitFor();
  expect(await row.getByRole('link', { name: target.artifact_ref.id }).getAttribute('href')).toBe('/console/app/forge?tab=layers');
  await row.getByTestId('run-open').click();
  await expect(page.getByTestId('run-feed-count')).toContainText(`${n} event`, { timeout: 20_000 });
  await row.getByTestId('run-open').click();                                   // Hide
  await expect(page.getByTestId('run-feed')).toHaveCount(0);
});

test('Retry is offered only where the API says retryable, sends the CSRF token, and lands on the Tool generator', async ({ context }) => {
  const page = await uiLogin(context);
  const base = { phases: ['planning'], phase: 'planning', progress: 40, engine: 'claude_code', model: 'claude-opus-5',
    started_at: '2026-10-12T00:00:00+00:00', ended_at: '2026-10-12T00:01:30+00:00', artifact_ref: null, live: false };
  const rows = [
    { run_id: 'run-aaaaaaaaaaaa', kind: 'tool', status: 'failed', retryable: true, error: 'Not registered', ...base },
    { run_id: 'run-bbbbbbbbbbbb', kind: 'tool', status: 'failed', retryable: false, error: 'x', ...base },
    { run_id: 'run-cccccccccccc', kind: 'skill', status: 'success', retryable: false, ...base },
  ];
  await page.route('**/forge/runs?*', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: rows, count: rows.length }) }));
  let retried: { url: string; csrf: string | undefined } | null = null;
  await page.route('**/forge-creator/tool/retry/*', (r) => { retried = { url: r.request().url(), csrf: r.request().headers()['x-csrf-token'] };
    return r.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ run_id: 'run-dddddddddddd', status: 'accepted' }) }); });
  await page.goto(RUNS);
  await expect(page.getByTestId('run-row')).toHaveCount(3);
  await expect(page.locator('[data-run-id="run-aaaaaaaaaaaa"] [data-testid="run-retry"]')).toBeVisible();
  await expect(page.locator('[data-run-id="run-bbbbbbbbbbbb"] [data-testid="run-retry"]')).toHaveCount(0);
  await expect(page.locator('[data-run-id="run-cccccccccccc"] [data-testid="run-retry"]')).toHaveCount(0);
  await expect(page.locator('[data-run-id="run-aaaaaaaaaaaa"]')).toContainText('1 min 30 s');   // duration from the record
  await page.locator('[data-run-id="run-aaaaaaaaaaaa"] [data-testid="run-retry"]').click();
  await expect(page).toHaveURL(/tab=generator&sub=tool/);
  expect((retried as any)?.url).toContain('/tool/retry/run-aaaaaaaaaaaa');
  expect((retried as any)?.csrf, 'a mutating call carries the CSRF token').toBeTruthy();
  expect(await page.evaluate(() => window.sessionStorage.getItem('forge-creator-run:tool'))).toBe('run-dddddddddddd');
});

test('an unreachable run API says "not available on this build" instead of an empty, fine-looking table; a missing model reads unknown', async ({ context }) => {
  const page = await uiLogin(context);
  await page.route('**/forge/runs?*', (r) => r.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Not Found"}' }));
  await page.goto(RUNS);
  await expect(page.getByTestId('runs-unavailable')).toContainText('not available on this build');
  await page.unroute('**/forge/runs?*');
  await page.route('**/forge/runs?*', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ count: 1, runs: [
    { run_id: 'run-eeeeeeeeeeee', kind: 'plugin', status: 'success', engine: 'unknown', model: 'unknown', started_at: null, ended_at: null, artifact_ref: null, live: false, retryable: false }] }) }));
  await page.reload();
  const row = page.getByTestId('run-row');
  await expect(row).toContainText('unknown · unknown');
  await expect(row).toContainText('—');                                         // no duration, no artifact, no start: dashes, not zeros
});
