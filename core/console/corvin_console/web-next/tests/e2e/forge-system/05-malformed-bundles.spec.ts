/**
 * 05 — Malformed and hostile bundles, stage by stage.
 *
 * Every case is a bundle forged OUTSIDE the code under test (bundle_lab.py,
 * stdlib only) and sent through the real upload route. Each row names the
 * validator stage that must refuse it. A refused bundle must write nothing:
 * the last test checks B's review queue and registries are unchanged.
 */
import {
  B, apiSession, expect, forgeBundle, openBundles, quarantine, test, toolRegistry, uiLogin, upload, type Session,
} from './forge-fixtures';

test.describe.configure({ mode: 'serial' });

let b: Session;
let toolsBefore: string[] = [];
let queueBefore = 0;

test.beforeAll(async () => {
  b = await apiSession(B);
  toolsBefore = Object.keys(toolRegistry(B)).sort();
  queueBefore = (await quarantine(b)).length;
});

const T = 'artifacts/tool/nordwind.lab@1.0.0/';
const labTool = (impl: unknown = 'def run(r):\n    return 1\n', specExtra: object = {}) => ({
  kind: 'tool', id: 'nordwind.lab', version: '1.0.0',
  files: { 'spec.json': { json: { name: 'nordwind.lab', description: 'lab', input_schema: { type: 'object' }, runtime: 'python', version: '1.0.0', impl_filename: 'impl.py', meta: null, ...specExtra } }, 'impl.py': impl },
});
const plugin = (files: Record<string, unknown>) => ({ kind: 'plugin', id: 'nordwind-lab-plugin', version: '1.0.0', files });
const base = (artifacts: unknown[], extra: object = {}) => ({ id: 'lab-malformed', version: '1.0.0', artifacts, ...extra });

// Credential shapes assembled at runtime so this file itself carries none.
const AWS = 'AKIA' + 'Q3EXAMPLE7NORDWN';                 // 4 + 16
// Fixture guard: a key of the wrong length silently tests nothing (it happened).
if (!/^AKIA[0-9A-Z]{16}$/.test(AWS)) throw new Error('AWS fixture does not have the AKIA+16 shape');
const PEM = '-----BEGIN ' + 'RSA PRIVATE KEY-----\nMIIEow' + 'IBAAKCAQEA7nordwind\n-----END ' + 'RSA PRIVATE KEY-----\n';
const GHP = 'ghp' + '_' + 'N0rdw1ndL0g1st1kT0k3nAbCdEf123456';
const JWT = 'eyJ' + 'hbGciOiJIUzI1NiJ9' + '.' + 'eyJzdWIiOiJub3Jkd2luZCJ9' + '.' + 'c2lnbmF0dXJlLW5vcmR3aW5k';

type Row = [name: string, recipe: Record<string, unknown>, stage: string];
const ROWS: Row[] = [
  // container
  ['entry outside artifacts/', base([labTool()], { tamper: [{ op: 'extra_file', path: 'README.md' }] }), 'container'],
  ['path traversal entry', base([labTool()], { tamper: [{ op: 'raw_entry', path: 'artifacts/../../etc/cron.d/x' }] }), 'container'],
  ['backslash entry', base([labTool()], { tamper: [{ op: 'raw_entry', path: 'artifacts\\tool\\x.py' }] }), 'container'],
  ['absolute entry', base([labTool()], { tamper: [{ op: 'raw_entry', path: '/artifacts/x' }] }), 'container'],
  ['symlink entry', base([labTool()], { tamper: [{ op: 'symlink', path: `${T}impl.py` }] }), 'container'],
  ['case-duplicate entries', base([labTool()], { tamper: [{ op: 'raw_entry', path: `${T}IMPL.py` }] }), 'container'],
  ['zip bomb (2 MiB of zeros, >100:1)', base([plugin({ 'big.bin': { repeat: '\u0000', n: 2 * 1024 * 1024 } })]), 'container'],
  ['gzip bomb inside a payload', base([plugin({ 'data.gz': { gzip: { repeat: '0', n: 30 * 1024 * 1024 } } })]), 'container'],
  ['archive nested two deep', base([plugin({ 'outer.zip': { zip: { 'inner.zip': { zip: { 'x.txt': 'x' } } } } })]), 'container'],
  // envelope
  ['no envelope', base([labTool()], { tamper: [{ op: 'drop_envelope' }] }), 'envelope'],
  ['envelope is not JSON', base([labTool()], { tamper: [{ op: 'drop_envelope' }, { op: 'raw_entry', path: 'forge-bundle.json', body: '{"format":' }] }), 'envelope'],
  ['format_version 2', base([labTool()], { tamper: [{ op: 'envelope_patch', fields: { format_version: 2 } }] }), 'envelope'],
  ['unknown artifact kind', base([{ ...labTool(), kind: 'workflow' }]), 'envelope'],
  ['non-SemVer version', base([labTool()], { version: '1.0' }), 'envelope'],
  ['id with path segment', base([labTool()], { id: '../nordwind' }), 'envelope'],
  ['unknown top-level key', base([labTool()], { tamper: [{ op: 'envelope_patch', fields: { install_hook: 'curl x | sh' } }] }), 'envelope'],
  ['description over 2000 chars', base([labTool()], { description: 'x'.repeat(2001) }), 'envelope'],
  ['65 artifacts (cap 64)', base(Array.from({ length: 65 }, (_, i) => ({ ...labTool(), id: `nordwind.t${i}`, files: { 'spec.json': { json: { name: `nordwind.t${i}` } }, 'impl.py': 'x' } }))), 'envelope'],
  // integrity
  ['payload changed after hashing', base([labTool()], { tamper: [{ op: 'flip_byte', path: `${T}impl.py` }] }), 'integrity'],
  ['declared file missing', base([labTool()], { tamper: [{ op: 'drop_file', path: `${T}impl.py` }] }), 'integrity'],
  ['undeclared file inside artifacts/', base([labTool()], { tamper: [{ op: 'extra_file', path: `${T}backdoor.py` }] }), 'integrity'],
  // secrets — the detector class is named, never the value
  ['AWS access key in tool code', base([labTool(`KEY = "${AWS}"\n`)]), 'secrets'],
  ['private key block in a plugin file', base([plugin({ 'deploy/id_rsa': PEM })]), 'secrets'],
  ['GitHub token in tool spec', base([labTool(undefined, { description: `token ${GHP}` })]), 'secrets'],
  ['JWT in the envelope description', base([labTool()], { description: `session ${JWT}` }), 'secrets'],
  ['AWS key inside a gzip stream', base([plugin({ 'cfg.gz': { gzip: `aws=${AWS}\n` } })]), 'secrets'],
  ['AWS key inside a nested ZIP', base([plugin({ 'pkg.zip': { zip: { 'conf/aws.ini': `key=${AWS}\n` } } })]), 'secrets'],
];

for (const [name, recipe, stage] of ROWS) {
  test(`refused at "${stage}": ${name}`, async () => {
    const zip = forgeBundle(`malformed-${name.replace(/[^a-z0-9]+/gi, '-')}`, recipe);
    const r = await upload(b, 'validate', zip);
    expect(r.status()).toBe(200);
    const v = await r.json();
    expect(v, JSON.stringify(v)).toMatchObject({ valid: false, stage, origin_verified: false });
    if (stage === 'secrets') {
      for (const secret of [AWS, PEM, GHP, JWT]) expect(v.reason).not.toContain(secret.slice(0, 20));
    }
    // /import refuses the same bundle with the same stage, as a 422.
    const imp = await upload(b, 'import', zip);
    expect(imp.status()).toBe(422);
    expect((await imp.json()).detail.stage).toBe(stage);
  });
}

test('transport-level refusals: not a ZIP (422 container), empty (400), > 50 MiB (413)', async () => {
  const notZip = await upload(b, 'import', Buffer.from('PK\u0003\u0004 truncated and lying about it'));
  expect(notZip.status()).toBe(422);
  expect((await notZip.json()).detail.stage).toBe('container');
  expect((await upload(b, 'import', Buffer.alloc(0))).status()).toBe(400);
  expect((await upload(b, 'import', Buffer.alloc(50 * 1024 * 1024 + 1))).status()).toBe(413);
});

// 400 KB: the quadratic regex needs ~18 s here (x4 input = x16 time; 80 KB took 0.73 s), the
// linear matcher ~0.2 s. The first version used 200 KB / 5 s and failed the old regex by only
// 14 % — a faster CPU would have let it pass (R4-I-6).
test('ReDoS probe: 400 KB of "eyJ-eyJ-…" is judged in well under 5 s', async () => {
  const zip = forgeBundle('redos-jwt', base([labTool(`X = "${'eyJ-'.repeat(100_000)}"\n`)]));
  const t0 = Date.now();
  const r = await upload(b, 'validate', zip, { timeout: 30_000 });
  expect(r.status()).toBe(200);
  expect(Date.now() - t0).toBeLessThan(5_000);
});

test('the UI names the refusing stage and offers no Import button', async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await uiLogin(ctx, B);
  await openBundles(page, B);
  await page.getByLabel('bundle file').setInputFiles(forgeBundle('ui-secret', base([labTool(`KEY = "${AWS}"\n`)])));
  await expect(page.getByText(/Rejected at stage “secrets”/)).toBeVisible();
  await expect(page.getByRole('button', { name: /^Import \d/ })).toHaveCount(0);
  await ctx.close();
});

test('nothing above wrote anything on B', async () => {
  expect(Object.keys(toolRegistry(B)).sort()).toEqual(toolsBefore);
  expect((await quarantine(b)).length).toBe(queueBefore);
});
