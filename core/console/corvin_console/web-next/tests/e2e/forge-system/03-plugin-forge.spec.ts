/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the API, the bundle lab and the audit chain */
/**
 * 03 — Plugin Forge through a bundle.
 *
 * The console deliberately has no plugin export (a plugin is exported from a
 * BUILT package on disk) — that contract is tested first. The CLI exports
 * Nordwind's TMS bridge and ETA panel together with a tool into one bundle in
 * ~/Downloads. On the depot the plugins do not install: they wait in the
 * existing plugin-upload approval (ADR-0511), and a decided package cannot be
 * re-proposed by importing it again.
 */
import path from 'path';
import fs from 'fs';
import {
  A, B, DOWNLOADS, apiSession, cliExport, exportApi, expect, inspectBundle, openBundles, test, uiLogin, upload, quarantine, decide,
  type Session,
} from './forge-fixtures';

test.describe.configure({ mode: 'serial' });

let a: Session;
let b: Session;
let bundle = '';
const pkg = (id: string, v: string) => path.join(A.home, 'plugin-packages', `${id}-${v}.zip`);

test.beforeAll(async () => {
  a = await apiSession(A);
  b = await apiSession(B);
});

test('console export refuses plugins and names the CLI (400, by design)', async () => {
  const r = await exportApi(a, { bundle_id: 'nordwind-plugins', bundle_version: '1.0.0',
    selections: [{ kind: 'plugin', id: 'nordwind-tms-bridge', version: '0.7.3' }] });
  expect(r.status()).toBe(400);
  expect((await r.json()).detail).toContain('forge_bundle_cli.py');
});

test('CLI export: two plugins + one tool → one bundle in ~/Downloads', () => {
  bundle = path.join(DOWNLOADS, 'nordwind-integrations-1.0.0.zip');
  const out = cliExport(A, [
    '--id', 'nordwind-integrations', '--version', '1.0.0', '--output', bundle,
    '--description', 'TMS bridge + ETA panel, with the route-cost tool they call',
    '--plugin', `nordwind-tms-bridge@0.7.3:${pkg('nordwind-tms-bridge', '0.7.3')}`,
    '--plugin', `nordwind-eta-panel@1.0.0:${pkg('nordwind-eta-panel', '1.0.0')}`,
    '--tool', 'nordwind.route_cost@1.0.0',
  ]);
  expect(out).toMatchObject({ status: 'SUCCESS', artifact_count: 3 });
  const z = inspectBundle(bundle);
  expect(z.problems).toEqual([]);
  expect(z.envelope.artifacts.map((x: any) => `${x.kind}:${x.id}@${x.version}`).sort()).toEqual([
    'plugin:nordwind-eta-panel@1.0.0', 'plugin:nordwind-tms-bridge@0.7.3', 'tool:nordwind.route_cost@1.0.0']);
});

test('CLI refuses a package whose manifest names another plugin/version', () => {
  expect(() => cliExport(A, ['--id', 'x', '--version', '1.0.0', '--output', path.join(DOWNLOADS, 'never.zip'),
    '--plugin', `nordwind-tms-bridge@9.9.9:${pkg('nordwind-tms-bridge', '0.7.3')}`])).toThrow();
  expect(fs.existsSync(path.join(DOWNLOADS, 'never.zip'))).toBe(false);
});

test('UI import on B: plugins wait for approval under Plugins, the tool waits in review', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await uiLogin(ctx, B);
  await openBundles(page, B);
  await page.getByLabel('bundle file').setInputFiles(bundle);
  await page.getByRole('button', { name: 'Import 3 artifacts' }).click();
  await expect(page.getByText('Awaiting approval under Plugins')).toHaveCount(2);
  await ctx.close();

  const uploads = await (await b.api.get('/v1/console/plugin-uploads')).json();
  const list = JSON.stringify(uploads);
  expect(list).toContain('nordwind-tms-bridge');
  expect(list).toContain('nordwind-eta-panel');
});

test('approve one plugin, reject the other — through the plugin-upload routes', async () => {
  const uploads = await (await b.api.get('/v1/console/plugin-uploads')).json();
  const items: any[] = uploads.items ?? uploads.uploads ?? uploads;
  const idOf = (name: string) => items.find((u) => JSON.stringify(u).includes(name))?.upload_id;
  const tms = idOf('nordwind-tms-bridge');
  const eta = idOf('nordwind-eta-panel');
  expect(tms && eta, JSON.stringify(items)).toBeTruthy();
  const approve = await b.api.post(`/v1/console/plugin-uploads/${tms}/approve`, { headers: { 'X-CSRF-Token': b.csrf } });
  expect(approve.status(), await approve.text()).toBe(200);
  const reject = await b.api.post(`/v1/console/plugin-uploads/${eta}/reject`, { headers: { 'X-CSRF-Token': b.csrf } });
  expect(reject.status(), await reject.text()).toBe(200);
});

// ADV-09 — FIXED 2026-10-08: StagingManager now remembers each decision (plugin_staging/decisions/).
// Before, `_intake_plugin` refused "this exact package was staged
// before and has already been decided" — but it learns that from the staging
// .meta file, and BOTH decisions delete it (reject: delete_staged_upload;
// approve: move_to_installed unlinks it, core/plugins/staging.py). The branch
// is unreachable: a REJECTED package comes back as pending_approval every time
// the same bundle is imported again, and an approved one is staged a second time.
test('ADV-09 (fixed): re-importing the same bundle cannot re-propose a decided plugin package', async () => {
  const body = await (await upload(b, 'import', bundle)).json();
  const plugins = body.outcomes.filter((o: any) => o.kind === 'plugin');
  test.info().annotations.push({ type: 'ADV-09', description: `re-import statuses: ${plugins.map((o: any) => `${o.id}=${o.status}`).join(', ')}` });
  expect(plugins.map((o: any) => o.status)).toEqual(['failed', 'failed']);
  const details = plugins.map((o: any) => o.detail).join(' | ');
  expect(details).toContain('already been decided (approved)');   // nordwind-tms-bridge
  expect(details).toContain('already been decided (rejected)');   // nordwind-eta-panel
});

test('cleanup: drop what the re-import staged, leave B\'s queues empty', async () => {
  const uploads = await (await b.api.get('/v1/console/plugin-uploads')).json();
  const items: any[] = uploads.items ?? uploads.uploads ?? uploads;
  for (const u of items) {
    if (/nordwind-(tms-bridge|eta-panel)/.test(JSON.stringify(u))) {
      await b.api.post(`/v1/console/plugin-uploads/${u.upload_id}/reject`, { headers: { 'X-CSRF-Token': b.csrf } });
    }
  }
  for (const q of await quarantine(b)) if (q.bundle_id === 'nordwind-integrations') await decide(b, q.quarantine_id, 'reject');
  expect((await quarantine(b)).filter((q) => q.bundle_id === 'nordwind-integrations')).toEqual([]);
});
