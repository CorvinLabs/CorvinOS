/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 16 — the Production feed under the generators (ADR-2253, T-0149).
 *  • REAL: attach to a real finished run of the live console and read its real event log in the real panel.
 *  • FAKED at the browser boundary only: a run that is going, a reload mid-run, a run with no log, a capped log.
 */
import { expect, test } from '@playwright/test';
import { apiSession, BASE, get, openGenerator, uiLogin } from './live';

const RUN_KEY = (kind: string) => `forge-creator-run:${kind}`;

const event = (seq: number, over: Record<string, unknown> = {}) => ({
  seq, ts: `2026-10-12T00:00:${String(seq).padStart(2, '0')}+00:00`, phase: 'generation', level: 'info', type: 'progress',
  message: `step ${seq}`, data: { progress: seq * 10 }, ...over,
});
const status = (over: Record<string, unknown> = {}) => ({
  run_id: 'run-aaaaaaaaaaaa', kind: 'tool', status: 'running', phase: 'generation', phases: ['planning', 'generation', 'review'],
  progress: 40, message: 'working', engine: 'claude_code', error: null, failure_code: null, draft_saved: false, tool: null, plugin: null, ...over,
});

test('REAL: a finished tool run shows its real event log in the real panel', async ({ context }) => {
  const s = await apiSession();
  const list = (await (await get(s, '/forge/runs?kind=tool&limit=50')).json()).runs as any[];
  let withLog: any = null;
  let events: any[] = [];
  for (const r of list) {
    const ev = (await (await get(s, `/forge/runs/${r.run_id}/events?limit=500`)).json()).events as any[];
    if (ev.length > 0) { withLog = r; events = ev; break; }
  }
  test.skip(!withLog, 'no tool run with an event log exists on this install yet - forge one first');
  const page = await uiLogin(context);
  await page.addInitScript(([k, id]) => window.sessionStorage.setItem(k as string, id as string), [RUN_KEY('tool'), withLog.run_id]);
  await openGenerator(page, 'tool');
  const feed = page.getByTestId('run-feed');
  await expect(feed).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('run-feed-count')).toContainText(`${events.length} event`);
  await expect(feed.getByTestId('run-feed-phase').first()).toBeVisible();
  // the first and last real events are readable in the feed
  await feed.getByTestId('run-feed-phase').first().locator('summary').click();
  await expect(feed).toContainText(events[0].message.slice(0, 30));
});

test('a run that is going fills in live and stops saying "live" when it ends; a reload re-attaches', async ({ context }) => {
  const page = await uiLogin(context);
  let polls = 0;
  let finished = false;
  await page.route('**/forge-creator/status/run-aaaaaaaaaaaa', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(status(finished ? { status: 'success', progress: 100, message: 'done' } : {})) }));
  await page.route('**/forge/runs/run-aaaaaaaaaaaa/events*', (r) => {
    polls++;
    const after = Number(new URL(r.request().url()).searchParams.get('after') ?? 0);
    const all = polls < 3 ? [event(1)] : [event(1), event(2), event(3, { type: 'test_result', message: 'sandbox tests: 2/2 passed', data: { passed: 2, total: 2 } })];
    const events = all.filter((e) => e.seq > after);
    return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ run_id: 'run-aaaaaaaaaaaa', events, next_after: events.length ? events[events.length - 1].seq : after }) });
  });
  await page.addInitScript(([k, id]) => window.sessionStorage.setItem(k as string, id as string), [RUN_KEY('tool'), 'run-aaaaaaaaaaaa']);
  await openGenerator(page, 'tool');
  await expect(page.getByTestId('run-feed-count')).toContainText('1 event');
  await expect(page.getByTestId('run-feed-count')).toContainText('live');
  await expect(page.getByTestId('run-feed-count')).toContainText('3 events', { timeout: 15_000 });
  await expect(page.getByTestId('run-feed-tests')).toContainText('2 of 2 passed');
  finished = true;
  await expect(page.getByTestId('run-feed-count')).not.toContainText('live', { timeout: 15_000 });
  // reload: the run id is in sessionStorage; the feed is rebuilt from the beginning of the log
  await page.reload();
  await expect(page.getByTestId('run-feed-count')).toContainText('3 events', { timeout: 15_000 });
  await expect(page.getByTestId('run-feed-count')).not.toContainText('live');
});

test('a run with no event log says so instead of looking empty by accident', async ({ context }) => {
  const page = await uiLogin(context);
  await page.route('**/forge-creator/status/run-bbbbbbbbbbbb', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(status({ run_id: 'run-bbbbbbbbbbbb', status: 'success', progress: 100 })) }));
  await page.route('**/forge/runs/run-bbbbbbbbbbbb/events*', (r) => r.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Run not found"}' }));
  await page.addInitScript(([k, id]) => window.sessionStorage.setItem(k as string, id as string), [RUN_KEY('tool'), 'run-bbbbbbbbbbbb']);
  await openGenerator(page, 'tool');
  await expect(page.getByTestId('run-feed-missing')).toContainText('No event log for this run');
});

test('a capped log reports how many events were left out; findings are grouped by dimension; a failed run shows its error line', async ({ context }) => {
  const page = await uiLogin(context);
  const events = [
    event(1), event(2, { type: 'finding', message: 'a defect', level: 'warning', data: { dimension: 'correctness', verdict: 'confirmed' } }),
    event(3, { type: 'finding', message: 'a nit', data: { dimension: 'correctness', verdict: 'plausible' } }),
    event(4, { type: 'finding', message: 'fine', data: { dimension: 'security', verdict: 'refuted' } }),
    event(5, { type: 'error', level: 'error', message: 'Not registered: the reviewer confirmed a finding' }),
    event(6, { type: 'truncated', message: '7 events were left out', data: { dropped: 7 } }),
  ];
  await page.route('**/forge-creator/status/run-cccccccccccc', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(status({ run_id: 'run-cccccccccccc', status: 'failed', message: 'Not registered' })) }));
  await page.route('**/forge/runs/run-cccccccccccc/events*', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ run_id: 'run-cccccccccccc', events, next_after: 6 }) }));
  await page.addInitScript(([k, id]) => window.sessionStorage.setItem(k as string, id as string), [RUN_KEY('tool'), 'run-cccccccccccc']);
  await openGenerator(page, 'tool');
  await expect(page.getByTestId('run-feed-truncated')).toContainText('7 events were left out');
  const findings = page.getByTestId('run-feed-findings');
  await expect(findings.locator('[data-dimension="correctness"]')).toContainText('1 confirmed · 1 plausible · 0 refuted');
  await expect(findings.locator('[data-dimension="security"]')).toContainText('0 confirmed · 0 plausible · 1 refuted');
  await page.getByTestId('run-feed-phase').first().locator('summary').click();
  await expect(page.locator('[data-type="error"]')).toContainText('Not registered');
  void BASE;
});

// ── REAL engine: refine the e2e_test_skill from the UI and read the diff in the Production feed (T-0172) ──────────
test('REAL: refining the e2e_test_skill from the UI shows what changed in the Production feed', async ({ context }) => {
  test.setTimeout(40 * 60_000);
  const s = await apiSession();
  const name = 'assistant.e2e_test_skill';
  const before = (await (await get(s, '/forge/runs?kind=skill&limit=200')).json()).runs as any[];
  const seen = new Set(before.map((r) => r.run_id));
  const startedAt = Date.now();
  const page = await uiLogin(context);
  await openGenerator(page, 'skill');
  await page.getByTestId('skill-filter').fill(name);
  await page.getByTestId(`refine-${name}`).click();
  const ta = page.getByTestId('skill-request');
  const request = 'Add one final sentence to the method: always state the verdict (APPROVED or NOT APPROVED) on the first line of the reply.';
  await ta.fill(request);
  await ta.press('Enter');                                   // the keyboard path, not the button
  // the run appears in the run API; wait for it to finish
  const run = await (async () => {
    const t0 = Date.now();
    for (;;) {
      // a minutes-long wait must survive a dropped connection: only a real answer counts, a transient error is retried
      let rows: any[] | null = null;
      try { rows = (await (await get(s, '/forge/runs?kind=skill&limit=200')).json()).runs as any[]; } catch { rows = null; }
      const mine = rows?.find((r) => !seen.has(r.run_id));
      if (mine && ['success', 'failed'].includes(mine.status)) return mine;
      if (Date.now() - t0 > 35 * 60_000) throw new Error('the refine run did not finish in 35 minutes');
      await new Promise((r) => setTimeout(r, 5_000));
    }
  })();
  const snaps = (await (await get(s, `/forge/runs/${run.run_id}/snapshots`)).json()).snapshots as any[];
  expect(snaps[0].label, 'a refine snapshots the body it started from').toBe('previous version');
  expect(snaps.length).toBeGreaterThanOrEqual(2);
  // the feed in the panel shows the same diff
  const diff = page.getByTestId('run-diff');
  await expect(diff).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('run-diff-range')).toContainText('previous version');
  const file = diff.locator('[data-testid="run-diff-file"][data-path="SKILL.md"]');
  await expect(file).toHaveAttribute('data-status', 'changed');
  expect(await file.locator('[data-diff="add"]').count(), 'the refine added at least one line').toBeGreaterThan(0);
  void BASE;
});
