/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the API, the bundle lab and the audit chain */
/**
 * 06 — Race conditions and timeouts.
 *
 * Concurrency is made with Promise.all against the real server (sync FastAPI
 * handlers run on a thread pool, so requests really overlap). Slowness is the
 * Layer Forge reviewer's latency — in production an Anthropic API call — which
 * the harness stub can stretch (setReviewer delay_s).
 *
 * ADV-01 and ADV-03 were confirmed weaknesses of the first review and are FIXED
 * (2026-10-08); their tests assert the repaired behaviour as regression guards.
 */
import {
  A, B, apiSession, chain, decide, expect, forgeBundle, openBundles, quarantine, setReviewer, test, toolRegistry, uiLogin,
  upload, type Session,
} from './forge-fixtures';

test.describe.configure({ mode: 'serial' });

let a: Session;
let b: Session;
let n = 0;

test.beforeAll(async () => {
  a = await apiSession(A);
  b = await apiSession(B);
});
test.afterAll(() => { setReviewer(A, { verdict: 'PASS' }); setReviewer(B, { verdict: 'PASS' }); });

const toolBundle = (name: string, bundleId = `race-${++n}`, body = `def run(req):\n    return {"tool": "${name}"}\n`) =>
  forgeBundle(bundleId, { id: bundleId, version: '1.0.0', artifacts: [{
    kind: 'tool', id: name, version: '1.0.0',
    files: { 'spec.json': { json: { name, description: 'race', input_schema: { type: 'object' }, runtime: 'python', version: '1.0.0', impl_filename: 'impl.py', meta: null } }, 'impl.py': body } }] });
const layerBundle = (id: string, bundleId = `race-layer-${++n}`) =>
  forgeBundle(bundleId, { id: bundleId, version: '1.0.0', artifacts: [{
    kind: 'layer', id, version: '1.0.0',
    files: { 'manifest.json': { json: { id, version: '1.0.0', targets: [{ layer_id: 'L34', layer_name: 'Data Flow Guard' }], quality_gates: [], enforcement_rules: [] } } } }] });

async function stage(s: Session, zip: string): Promise<string> {
  const body = await (await upload(s, 'import', zip)).json();
  expect(body.outcomes[0].status, JSON.stringify(body)).toBe('quarantined');
  return body.outcomes[0].detail;
}

// ── review-queue races ─────────────────────────────────────────────────────

test('same entry accepted twice at once: exactly one wins, the tool exists once', async () => {
  const qid = await stage(a, toolBundle('nordwind.dock_slots'));
  const codes = (await Promise.all([decide(a, qid, 'accept'), decide(a, qid, 'accept'), decide(a, qid, 'accept')])).map((r) => r.status());
  expect(codes.filter((c) => c === 200)).toHaveLength(1);
  expect(codes.filter((c) => c === 404)).toHaveLength(2);
  expect(Object.keys(toolRegistry(A)).filter((k) => k === 'nordwind.dock_slots')).toHaveLength(1);
});

test('accept and reject of one entry at once: one decision, consistent result', async () => {
  const qid = await stage(a, toolBundle('nordwind.yard_moves'));
  const [acc, rej] = await Promise.all([decide(a, qid, 'accept'), decide(a, qid, 'reject')]);
  expect([acc.status(), rej.status()].sort()).toEqual([200, 404]);
  const exists = 'nordwind.yard_moves' in toolRegistry(A);
  expect(exists).toBe(acc.status() === 200);
});

test('control: two case-variant names accepted one after the other — the second is 409', async () => {
  const q1 = await stage(a, toolBundle('nordwind.berth_plan'));
  const q2 = await stage(a, toolBundle('Nordwind.Berth_Plan'));
  expect((await decide(a, q1, 'accept')).status()).toBe(200);
  expect((await decide(a, q2, 'accept')).status()).toBe(409);
});

// ADV-01 — FIXED 2026-10-08: Registry.create checks case-insensitive uniqueness under its own lock;
// the loser of the race gets 409 and stays in the review queue.
test('ADV-01: two case-variant names accepted AT ONCE never both become tools', async () => {
  // _name_taken() is case-insensitive but runs OUTSIDE Registry.create's lock;
  // Registry.create checks `name in data` (case-sensitive) inside it. On
  // macOS/Windows both names map to ONE impl file: the operator reviewed one
  // tool's code and the other tool's code runs under its name.
  let both = 0;
  for (let i = 0; i < 6; i++) {
    const lower = `nordwind.geo_fence_${i}`;
    const upper = `Nordwind.Geo_Fence_${i}`;
    const [q1, q2] = [await stage(a, toolBundle(lower)), await stage(a, toolBundle(upper))];
    const codes = (await Promise.all([decide(a, q1, 'accept'), decide(a, q2, 'accept')])).map((r) => r.status());
    if (codes.every((c) => c === 200)) both++;
  }
  const reg = Object.keys(toolRegistry(A));
  const folded = new Set(reg.map((k) => k.toLowerCase()));
  test.info().annotations.push({ type: 'ADV-01', description: `${both}/6 attempts produced two case-colliding tools` });
  expect(both).toBe(0);
  expect(folded.size, `case-colliding tools: ${reg.filter((k) => /geo_fence/i.test(k)).join(', ')}`).toBe(reg.length);
});

// ADV-02 — REFUTED (measured 1 entry over repeated runs): the identical-entry
// dedupe in ToolQuarantine.stage held under 4 concurrent imports. Kept as a
// regression test; the tail asserts the safe outcome for ANY entry count.
test('ADV-02 (refuted): the same bundle imported 2× at once', async () => {
  const zip = toolBundle('nordwind.reefer_temp', 'race-dup-import');
  // 2 = the number of heavy slots; a 3rd concurrent import would (correctly) be a 429.
  const res = await Promise.all(Array.from({ length: 2 }, () => upload(b, 'import', zip)));
  expect(res.map((r) => r.status())).toEqual([200, 200]);
  const entries = (await quarantine(b)).filter((q) => q.tool_id === 'nordwind.reefer_temp');
  test.info().annotations.push({ type: 'ADV-02', description: `${entries.length} review entries for one identical tool` });
  expect(entries.length).toBeGreaterThanOrEqual(1);
  // Whatever the count: accepting one makes every other entry a 409, the tool exists once.
  expect((await decide(b, entries[0].quarantine_id, 'accept')).status()).toBe(200);
  for (const e of entries.slice(1)) expect((await decide(b, e.quarantine_id, 'accept')).status()).toBe(409);
  expect(Object.keys(toolRegistry(B)).filter((k) => k === 'nordwind.reefer_temp')).toHaveLength(1);
  for (const e of entries.slice(1)) await decide(b, e.quarantine_id, 'reject');
});

test('the same layer imported 2× at once: forged once, the other refused, chain intact', async () => {
  const zip = layerBundle('nordwind.cold-chain', 'race-layer-dup');
  const bodies = await Promise.all(Array.from({ length: 2 }, async () => (await upload(b, 'import', zip)).json()));
  const statuses = bodies.map((x) => x.outcomes[0].status).sort();
  expect(statuses).toEqual(['failed', 'forged']);
  const defs = (await (await b.api.get('/v1/console/layer-forge/definitions')).json()).items
    .filter((l: any) => l.id === 'nordwind.cold-chain');
  expect(defs).toHaveLength(1);
  expect(chain(B).ok).toBe(true);
});

// ── timeouts ───────────────────────────────────────────────────────────────

test('client gives up mid-import (2 s, reviewer takes 6 s): the server finishes cleanly, a retry sees it', async () => {
  setReviewer(B, { verdict: 'PASS', delay_s: 6 });
  const zip = layerBundle('nordwind.slow-review');
  await expect(upload(b, 'import', zip, { timeout: 2_000 })).rejects.toThrow(/Timeout|timed out/i);
  await new Promise((r) => setTimeout(r, 7_000));
  setReviewer(B, { verdict: 'PASS' });
  const def = await b.api.get('/v1/console/layer-forge/definitions/nordwind.slow-review?version=1.0.0');
  expect(def.status()).toBe(200);                          // landed completely, not half
  const retry = await (await upload(b, 'import', zip)).json();
  expect(retry.outcomes[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('refused') });
  expect(chain(B).ok).toBe(true);
});

// ADV-03 — FIXED 2026-10-08 (was: 48 slow imports → a sync route answered after
// 5554 ms). At most 2 validate/import handlers run at once; the rest are
// answered 429 + Retry-After immediately, so they never hold a pool worker.
test('ADV-03: a flood of slow imports is shed with 429 and does not starve other sync routes', async () => {
  setReviewer(B, { verdict: 'PASS', delay_s: 5 });
  const zips = Array.from({ length: 48 }, (_, i) => layerBundle(`nordwind.flood-${i}`));
  const flood = Promise.all(zips.map((z) => upload(b, 'import', z, { timeout: 120_000 })));
  await new Promise((r) => setTimeout(r, 1_000));
  const t0 = Date.now();
  const probe = await b.api.get('/v1/console/forge-bundles/quarantine', { timeout: 60_000 });
  const latency = Date.now() - t0;
  const results = await flood;
  setReviewer(B, { verdict: 'PASS' });
  const codes = results.map((r) => r.status());
  const shed = codes.filter((c) => c === 429);
  test.info().annotations.push({ type: 'ADV-03', description: `queue list ${latency} ms; ${codes.filter((c) => c === 200).length} imported, ${shed.length} shed` });
  expect(probe.status()).toBe(200);
  expect(latency).toBeLessThan(2_000);
  expect(codes.every((c) => c === 200 || c === 429), JSON.stringify(codes)).toBe(true);
  expect(codes.filter((c) => c === 200).length).toBeGreaterThanOrEqual(1);
  expect(shed.length).toBeGreaterThanOrEqual(40);                 // 2 slots: nearly everything else is shed
  expect(results.find((r) => r.status() === 429)!.headers()['retry-after']).toBe('5');
  expect(chain(B).ok).toBe(true);
  // Shed requests wrote nothing: only imported layers exist.
  const flooded = (await (await b.api.get('/v1/console/layer-forge/definitions')).json()).items.filter((l: any) => l.id.startsWith('nordwind.flood-'));
  expect(flooded.length).toBe(codes.filter((c) => c === 200).length);
});

// ── UI under latency ───────────────────────────────────────────────────────

test('UI: a slow import locks the form — no double submit, one result', async ({ browser }) => {
  setReviewer(B, { verdict: 'PASS', delay_s: 4 });
  const ctx = await browser.newContext();
  const page = await uiLogin(ctx, B);
  await openBundles(page, B);
  const since = chain(B).lines;
  await page.getByLabel('bundle file').setInputFiles(layerBundle('nordwind.ui-slow'));
  const btn = page.getByRole('button', { name: 'Import 1 artifact' });
  await btn.click();
  await expect(page.getByRole('button', { name: /Import 1 artifact/ })).toBeDisabled();
  await expect(page.getByLabel('bundle file')).toBeDisabled();
  await btn.click({ force: true }).catch(() => {});        // a second, impatient click
  await expect(page.getByText(/1 of 1 artifacts proposed/)).toBeVisible({ timeout: 30_000 });
  setReviewer(B, { verdict: 'PASS' });
  const created = chain(B, since).events.filter((e) => e.event_type === 'forge_bundle.import_validated');
  expect(created).toHaveLength(1);
  await ctx.close();
});

test('UI: a late validation answer for an older file never overwrites the newer pick', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await uiLogin(ctx, B);
  await openBundles(page, B);
  const first = toolBundle('nordwind.first_pick', 'ui-first-pick');
  const second = toolBundle('nordwind.second_pick', 'ui-second-pick');
  let calls = 0;
  await page.route('**/forge-bundles/validate', async (route) => {
    if (++calls === 1) await new Promise((r) => setTimeout(r, 3_000)); // the FIRST answer arrives last
    await route.continue();
  });
  await page.getByLabel('bundle file').setInputFiles(first);
  await page.getByLabel('bundle file').setInputFiles(second);
  await expect(page.getByText('ui-second-pick@1.0.0')).toBeVisible();
  await page.waitForTimeout(3_500);
  await expect(page.getByText('ui-second-pick@1.0.0')).toBeVisible();
  await expect(page.getByText('ui-first-pick@1.0.0')).toHaveCount(0);
  await ctx.close();
});
