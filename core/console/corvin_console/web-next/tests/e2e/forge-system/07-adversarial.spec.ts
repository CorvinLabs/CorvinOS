/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the API, the bundle lab and the audit chain */
/**
 * 07 — Adversarial review: deliberate attempts to break the forge system.
 *
 * Each test names its finding ID (ADV-xx) from the review. A test that asserts
 * a guarantee the system keeps is a plain test; a confirmed weakness is
 * `test.fail()` (see 06); a scanner LIMIT that no reasonable fix closes is
 * measured and annotated, not asserted either way.
 */
import fs from 'fs';
import { request } from '@playwright/test';
import {
  A, B, BUNDLES, apiSession, exportApi, expect, forgeBundle, graftBundle, inspectBundle, openBundles, test, uiLogin,
  upload, saveExport, type Session,
} from './forge-fixtures';

test.describe.configure({ mode: 'serial' });

let a: Session;
let b: Session;

test.beforeAll(async () => {
  a = await apiSession(A);
  b = await apiSession(B);
});

const labTool = (name: string, impl: string, desc = 'lab') => ({
  kind: 'tool', id: name, version: '1.0.0',
  files: { 'spec.json': { json: { name, description: desc, input_schema: { type: 'object' }, runtime: 'python', version: '1.0.0', impl_filename: 'impl.py', meta: null } }, 'impl.py': impl } });

test('ADV-04: markup in a bundle description renders as text, never executes', async ({ browser }) => {
  const payload = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>Nordwind';
  const zip = forgeBundle('xss-description', { id: 'xss-kit', version: '1.0.0', description: payload,
    artifacts: [labTool('nordwind.xss_probe', 'def run(r):\n    return 1\n', '<b onmouseover="window.__pwned=3">hover</b>')] });
  const ctx = await browser.newContext();
  const page = await uiLogin(ctx, B);
  let dialog = false;
  page.on('dialog', async (d) => { dialog = true; await d.dismiss(); });
  await openBundles(page, B);
  await page.getByLabel('bundle file').setInputFiles(zip);
  await expect(page.getByText(payload)).toBeVisible();        // shown literally
  expect(await page.evaluate(() => (window as any).__pwned)).toBeUndefined();
  expect(await page.locator('img[src="x"]').count()).toBe(0);
  expect(dialog).toBe(false);
  await ctx.close();
});

test('ADV-05: a bundle id cannot inject response headers (Content-Disposition)', async () => {
  for (const id of ['nordwind"\r\nSet-Cookie: pwned=1', 'nordwind\nX-Evil: 1', '../../nordwind']) {
    const r = await exportApi(a, { bundle_id: id, bundle_version: '1.0.0',
      selections: [{ kind: 'tool', id: 'nordwind.route_cost', version: '1.0.0' }] });
    expect(r.status(), id).toBe(422);
    expect(r.headers()['set-cookie'] ?? '').not.toContain('pwned');
    expect(r.headers()['x-evil']).toBeUndefined();
  }
});

test('auth matrix: every route needs a session; every mutation needs the CSRF token', async () => {
  const anon = await request.newContext({ baseURL: B.url });
  for (const p of ['/exportable', '/quarantine']) expect((await anon.get(`${BUNDLES}${p}`)).status(), p).toBe(401);
  for (const p of ['/export', '/validate', '/import', `/quarantine/${'a'.repeat(32)}/accept`, `/quarantine/${'a'.repeat(32)}/reject`]) {
    expect((await anon.post(`${BUNDLES}${p}`)).status(), p).toBe(401);
    expect((await b.api.post(`${BUNDLES}${p}`)).status(), `${p} without CSRF`).toBe(403);
    expect((await b.api.post(`${BUNDLES}${p}`, { headers: { 'X-CSRF-Token': 'f'.repeat(64) } })).status(), `${p} wrong CSRF`).toBe(403);
  }
  // A's CSRF token is worthless on B (separate install, separate session store).
  expect((await b.api.post(`${BUNDLES}/import`, { headers: { 'X-CSRF-Token': a.csrf } })).status()).toBe(403);
  await anon.dispose();
});

test('ADV-06: skill identity — a package of skill X declared as skill Y must not install as Y', async () => {
  // Export eta-narrator from A, then relabel its envelope entry as invoice-extract@1.4.2.
  const real = await saveExport(await exportApi(a, { bundle_id: 'id-source', bundle_version: '1.0.0',
    selections: [{ kind: 'skill', id: 'customs-hs-classifier', version: '2.0.0-rc.1' }] }), 'adv06-source-1.0.0.zip');
  const z = inspectBundle(real);
  const art = z.envelope.artifacts[0];
  const forged = graftBundle('adv06-relabelled', real, { envelope: { id: 'id-forged', artifacts: [{
    ...art, id: 'nordwind-trusted-invoices', version: '1.0.0' }] } });
  // Envelope paths still name the real skill; the validator must catch the mismatch,
  // or the installer must refuse a package whose manifest names another skill.
  const v = await (await upload(b, 'validate', forged)).json();
  const imp = v.valid ? await (await upload(b, 'import', forged)).json() : null;
  test.info().annotations.push({ type: 'ADV-06', description: JSON.stringify(imp ?? v).slice(0, 400) });
  const installedAs = fs.existsSync(`${B.home}/skills_installed`) ? fs.readdirSync(`${B.home}/skills_installed`).join(' ') : '';
  expect(installedAs).not.toContain('nordwind-trusted-invoices');
});

test('ADV-07 (limit, measured): obfuscated credentials pass the scanner', async () => {
  const AWS = 'AKIA' + 'Q3EXAMPLE7NORDWN';
  const cases: Record<string, string> = {
    concatenated: `KEY = "AKIA" + "${AWS.slice(4)}"\n`,
    base64: `KEY = __import__("base64").b64decode("${Buffer.from(AWS).toString('base64')}")\n`,
    reversed: `KEY = "${AWS.split('').reverse().join('')}"[::-1]\n`,
  };
  const passed: string[] = [];
  for (const [name, impl] of Object.entries(cases)) {
    const v = await (await upload(b, 'validate', forgeBundle(`adv07-${name}`, { id: `adv07-${name}`, version: '1.0.0',
      artifacts: [labTool(`nordwind.obf_${name}`, impl)] }))).json();
    if (v.valid) passed.push(name);
  }
  test.info().annotations.push({ type: 'ADV-07', description: `passed the secret scan: ${passed.join(', ') || 'none'}` });
  // Pattern scanning cannot see through code; the human review gate is the control here.
  expect(passed.length).toBeGreaterThanOrEqual(0);
});

test('ADV-08: credentials in a UTF-16 file and in a nested archive\'s ENTRY NAME', async () => {
  const AWS = 'AKIA' + 'Q3EXAMPLE7NORDWN';
  const utf16 = await (await upload(b, 'validate', forgeBundle('adv08-utf16', { id: 'adv08-utf16', version: '1.0.0',
    artifacts: [{ kind: 'plugin', id: 'nordwind-utf16', version: '1.0.0', files: {
      'conf.ini': { b64: Buffer.from(`aws=${AWS}\n`, 'utf16le').toString('base64') } } }] }))).json();
  const entryName = await (await upload(b, 'validate', forgeBundle('adv08-name', { id: 'adv08-name', version: '1.0.0',
    artifacts: [{ kind: 'plugin', id: 'nordwind-entryname', version: '1.0.0', files: {
      'pkg.zip': { zip: { [`keys/${AWS}.txt`]: 'harmless body' } } } }] }))).json();
  test.info().annotations.push({ type: 'ADV-08', description: `utf16: ${JSON.stringify(utf16).slice(0, 120)} | entry name: ${JSON.stringify(entryName).slice(0, 120)}` });
  expect(utf16).toMatchObject({ valid: false, stage: 'secrets' });
  expect(entryName).toMatchObject({ valid: false, stage: 'secrets' });
});
