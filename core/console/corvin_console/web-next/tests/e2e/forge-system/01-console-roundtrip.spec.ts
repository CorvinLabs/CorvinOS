/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the API, the bundle lab and the audit chain */
/**
 * 01 — Console round trip across two installs, driven through the browser.
 *
 * Nordwind HQ (A) exports its Q4 operations kit — two skills (one a SemVer
 * pre-release), three tools (python with a pinned requirement, python with a
 * budget, bash requesting a secret) and two layers (one of which exists in two
 * versions; only the ticked version may travel) — through Forge → Bundles.
 * The ZIP is saved to ~/Downloads, inspected byte-for-byte, then imported by
 * the empty Depot Hamburg install (B) through the same UI, where the tools sit
 * in review until an operator accepts or rejects each one.
 */
import fs from 'fs';
import path from 'path';
import {
  A, B, DOWNLOADS, apiSession, chain, expect, inspectBundle, openBundles, test, toolRegistry, uiLogin,
  exportApi, saveExport, type Session,
} from './forge-fixtures';

test.describe.configure({ mode: 'serial' });

const BUNDLE_ID = 'nordwind-q4-ops';
const BUNDLE_VERSION = '2.3.0';
let exported = '';
let chainA0 = 0;
let chainB0 = 0;
let a: Session;
let b: Session;

test.beforeAll(async () => {
  a = await apiSession(A);
  b = await apiSession(B);
  chainA0 = chain(A).lines;
  chainB0 = chain(B).lines;
});

test('A lists exactly what its four forges hold — and B starts empty', async () => {
  const inv = await (await a.api.get('/v1/console/forge-bundles/exportable')).json();
  expect(inv.skills.map((s: any) => `${s.id}@${s.version}`).sort()).toEqual([
    'customs-hs-classifier@2.0.0-rc.1', 'eta-narrator@0.9.0+build.17', 'invoice-extract@1.4.2']);
  expect(inv.tools.map((t: any) => t.id).sort()).toEqual([
    'nordwind.csv_merge', 'nordwind.manifest_lint', 'nordwind.route_cost']);
  expect(inv.layers.map((l: any) => `${l.id}@${l.version}`).sort()).toEqual([
    'nordwind.audit-sink@3.2.1', 'nordwind.dataflow-guard@1.0.0', 'nordwind.dataflow-guard@1.1.0']);
  // Plugins are never listed: the console has no plugin export (CLI only).
  expect(inv.plugins).toEqual([]);

  const empty = await (await b.api.get('/v1/console/forge-bundles/exportable')).json();
  expect([empty.skills, empty.tools, empty.layers]).toEqual([[], [], []]);
});

test('UI export on A downloads one ZIP into ~/Downloads', async ({ browser }) => {
  const ctx = await browser.newContext({ acceptDownloads: true });
  const page = await uiLogin(ctx, A);
  await openBundles(page, A);

  const exportBtn = page.getByRole('button', { name: /^Export/ });
  await expect(exportBtn).toBeDisabled(); // nothing picked, no id yet

  for (const label of [
    'select skill invoice-extract', 'select skill customs-hs-classifier',
    'select tool nordwind.route_cost', 'select tool nordwind.csv_merge', 'select tool nordwind.manifest_lint',
    'select layer nordwind.dataflow-guard@1.0.0', 'select layer nordwind.audit-sink@3.2.1',
  ]) await page.getByLabel(label).check();
  // One version per layer: ticking 1.1.0 must REPLACE the ticked 1.0.0.
  await page.getByLabel('select layer nordwind.dataflow-guard@1.1.0').check();
  await expect(page.getByLabel('select layer nordwind.dataflow-guard@1.0.0')).not.toBeChecked();

  await page.locator('#fb-id').fill('nordwind q4');           // space: not a safe id
  await expect(exportBtn).toBeDisabled();
  await page.locator('#fb-id').fill(BUNDLE_ID);
  await page.locator('#fb-version').fill('2.3');              // not SemVer
  await expect(exportBtn).toBeDisabled();
  await page.locator('#fb-version').fill(BUNDLE_VERSION);
  await page.locator('#fb-desc').fill(
    'Nordwind Logistik Q4 operations kit: invoice extraction, HS customs classification, '
    + 'route costing, CSV consolidation, container-manifest linting and the L34/L35 data-flow guard.');
  await expect(exportBtn).toHaveText(/Export 7 artifacts/);

  const [download] = await Promise.all([page.waitForEvent('download'), exportBtn.click()]);
  expect(download.suggestedFilename()).toBe(`${BUNDLE_ID}-${BUNDLE_VERSION}.zip`);
  exported = path.join(DOWNLOADS, download.suggestedFilename());
  await download.saveAs(exported);
  await expect(page.getByText('Bundle downloaded.')).toBeVisible();
  await ctx.close();

  expect(fs.existsSync(exported)).toBeTruthy();
  expect(fs.statSync(exported).size).toBeGreaterThan(1000);
});

test('the downloaded bundle is complete, hashed, and carries no registry state', () => {
  const z = inspectBundle(exported);
  expect(z.problems).toEqual([]);           // independent sha256/size re-check
  expect(z.undeclared).toEqual([]);
  expect(z.envelope).toMatchObject({ format: 'corvin.forge-bundle', format_version: 1, id: BUNDLE_ID, version: BUNDLE_VERSION });
  const arts = z.envelope.artifacts.map((x: any) => `${x.kind}:${x.id}@${x.version}`).sort();
  expect(arts).toEqual([
    'layer:nordwind.audit-sink@3.2.1', 'layer:nordwind.dataflow-guard@1.1.0',
    'skill:customs-hs-classifier@2.0.0-rc.1', 'skill:invoice-extract@1.4.2',
    // Tools have no version of their own: they travel under the bundle version.
    'tool:nordwind.csv_merge@2.3.0', 'tool:nordwind.manifest_lint@2.3.0', 'tool:nordwind.route_cost@2.3.0',
  ]);

  const layer = JSON.parse(z.payloads['artifacts/layer/nordwind.dataflow-guard@1.1.0/manifest.json']);
  expect(layer.targets.map((t: any) => t.layer_id)).toEqual(['L34', 'L35']); // 1.1.0, not 1.0.0
  expect(layer).not.toHaveProperty('status');                               // registry state stays home
  expect(Object.keys(layer).filter((k) => k.startsWith('_'))).toEqual([]);

  const spec = JSON.parse(z.payloads['artifacts/tool/nordwind.csv_merge@2.3.0/spec.json']);
  for (const k of ['scope', 'call_count', 'created_at', 'promoted', 'sha256', 'impl_path']) expect(spec).not.toHaveProperty(k);
  expect(spec.meta.requirements).toEqual(['python-dateutil==2.9.0']);
  const lint = JSON.parse(z.payloads['artifacts/tool/nordwind.manifest_lint@2.3.0/spec.json']);
  expect(lint.runtime).toBe('bash');
  expect(lint.meta.secrets).toEqual(['NORDWIND_TMS_TOKEN']); // the NAME travels, never a value

  // The tool's code is byte-identical to what Tool Forge stores on A.
  const onA = fs.readFileSync(path.join(A.home, 'tenants/_default/forge/tools/nordwind.route_cost.py'), 'utf-8');
  expect(z.payloads['artifacts/tool/nordwind.route_cost@2.3.0/nordwind.route_cost.py']).toBe(onA);

  // Each skill travels as the Skill Forge package (ADR-0674), not a loose folder.
  const skillZip = Object.keys(z.payloads).find((k) => k.startsWith('artifacts/skill/invoice-extract@1.4.2/'))!;
  expect(z.payloads[skillZip].zip_entries).toEqual(expect.arrayContaining([expect.stringMatching(/skill\.json$/)]));
});

test('the export is on A\'s audit chain, and the chain still verifies', () => {
  const c = chain(A, chainA0);
  expect(c.ok, JSON.stringify(c.problems)).toBe(true);
  const ev = c.events.find((e) => e.event_type === 'forge_bundle.exported');
  expect(ev, 'forge_bundle.exported').toBeTruthy();
  expect(JSON.stringify(ev!.details)).toContain(BUNDLE_ID);
});

test('UI import on B: preview first, nothing written until Import', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await uiLogin(ctx, B);
  await openBundles(page, B);
  await expect(page.getByText('None forged.')).toHaveCount(3);

  await page.getByLabel('bundle file').setInputFiles(exported);
  await expect(page.getByText(`${BUNDLE_ID}@${BUNDLE_VERSION}`)).toBeVisible();
  await expect(page.getByText(/Nordwind Logistik Q4 operations kit/)).toBeVisible();
  await expect(page.getByText('nordwind.dataflow-guard@1.1.0')).toBeVisible();
  // Validation alone wrote nothing: B's quarantine and registries are still empty.
  expect((await (await b.api.get('/v1/console/forge-bundles/quarantine')).json()).count).toBe(0);
  expect(Object.keys(toolRegistry(B))).toEqual([]);

  await page.getByRole('button', { name: 'Import 7 artifacts' }).click();
  await expect(page.getByText(`${BUNDLE_ID}@${BUNDLE_VERSION}: 7 of 7 artifacts proposed.`)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText('Installed')).toHaveCount(2);
  await expect(page.getByText('Forged (gates passed)')).toHaveCount(2);
  await expect(page.getByText('Awaiting review below')).toHaveCount(3);

  // Review queue: foreign code, with what it would install and which secrets it asks for.
  const csv = page.locator('div.rounded', { hasText: 'nordwind.csv_merge' });
  await expect(csv.getByText('Installs packages: python-dateutil==2.9.0')).toBeVisible();
  const lint = page.locator('div.rounded', { hasText: 'nordwind.manifest_lint' });
  await expect(lint.getByText('Requests secrets: NORDWIND_TMS_TOKEN')).toBeVisible();
  // Imported tools are NOT callable before review.
  expect(Object.keys(toolRegistry(B))).toEqual([]);

  await page.locator('div.rounded', { hasText: 'nordwind.route_cost' }).getByRole('button', { name: 'Accept' }).click();
  await expect(page.getByText('Accepted nordwind.route_cost — it can be called now.')).toBeVisible();
  await csv.getByRole('button', { name: 'Accept' }).click();
  await expect(page.getByText('Accepted nordwind.csv_merge — it can be called now.')).toBeVisible();
  await lint.getByRole('button', { name: 'Reject' }).click();
  await expect(page.getByText('Rejected nordwind.manifest_lint — it was deleted.')).toBeVisible();
  await ctx.close();
});

test('B now holds each artifact in its own forge, with provenance', async () => {
  const reg = toolRegistry(B);
  expect(Object.keys(reg).sort()).toEqual(['nordwind.csv_merge', 'nordwind.route_cost']);
  expect(reg['nordwind.route_cost'].meta).toMatchObject({
    origin: 'forge_bundle', bundle_id: BUNDLE_ID, bundle_version: BUNDLE_VERSION, origin_verified: false });
  expect(fs.readFileSync(reg['nordwind.route_cost'].impl_path, 'utf-8'))
    .toBe(fs.readFileSync(path.join(A.home, 'tenants/_default/forge/tools/nordwind.route_cost.py'), 'utf-8'));
  expect((await (await b.api.get('/v1/console/forge-bundles/quarantine')).json()).count).toBe(0);

  const layers = (await (await b.api.get('/v1/console/layer-forge/definitions')).json()).items;
  expect(layers.map((l: any) => `${l.id}@${l.version}`).sort())
    .toEqual(['nordwind.audit-sink@3.2.1', 'nordwind.dataflow-guard@1.1.0']);

  for (const s of ['invoice-extract', 'customs-hs-classifier']) {
    expect(fs.readdirSync(path.join(B.home, 'skills_installed')).join(' '), s).toContain(s);
  }

  const c = chain(B, chainB0);
  expect(c.ok, JSON.stringify(c.problems)).toBe(true);
  const types = c.events.map((e) => e.event_type);
  for (const t of ['forge_bundle.import_validated', 'forge_bundle.artifact_staged',
    'forge_bundle.quarantine_accepted', 'forge_bundle.quarantine_rejected', 'forge_bundle.artifact_created']) {
    expect(types, t).toContain(t);
  }
});

test('second generation: B re-exports what it imported — payloads are lossless', async () => {
  const res = await exportApi(b, {
    bundle_id: 'depot-hamburg-relay', bundle_version: BUNDLE_VERSION,
    selections: [
      { kind: 'tool', id: 'nordwind.route_cost', version: BUNDLE_VERSION },
      { kind: 'layer', id: 'nordwind.dataflow-guard', version: '1.1.0' },
    ],
  });
  const relay = await saveExport(res, 'depot-hamburg-relay-2.3.0.zip');
  const first = inspectBundle(exported).payloads;
  const second = inspectBundle(relay).payloads;
  const tool = 'artifacts/tool/nordwind.route_cost@2.3.0/nordwind.route_cost.py';
  expect(second[tool]).toBe(first[tool]);
  const layer = 'artifacts/layer/nordwind.dataflow-guard@1.1.0/manifest.json';
  expect(JSON.parse(second[layer])).toEqual(JSON.parse(first[layer]));
  // Provenance meta (origin, quarantine_id) is B's registry state — it must not leak onward.
  const spec = JSON.parse(second['artifacts/tool/nordwind.route_cost@2.3.0/spec.json']);
  expect(JSON.stringify(spec)).not.toMatch(/quarantine_id|origin_verified|forge_bundle/);
});
