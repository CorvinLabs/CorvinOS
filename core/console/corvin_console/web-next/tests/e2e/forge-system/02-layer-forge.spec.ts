/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the API, the bundle lab and the audit chain */
/**
 * 02 — Layer Forge, end to end, with REAL quality gates.
 *
 * Nordwind's compliance team forges an L37 retention layer on HQ through the
 * console: two quality gates are real pytest runs of this repo's own Layer
 * Forge tests. It is exported to ~/Downloads and imported on the depot, where
 * Layer Forge runs every gate AGAIN — an import is a proposal, never a copy.
 * Then the unhappy paths: a reviewer that is down, a reviewer that flags, a
 * gate whose test file does not exist on the target, the gate caps, and a
 * manifest that smuggles registry state.
 */
import {
  A, B, apiSession, chain, exportApi, expect, forgeBundle, saveExport, setReviewer, test, upload, inspectBundle,
  type Session,
} from './forge-fixtures';

test.describe.configure({ mode: 'serial' });

let a: Session;
let b: Session;
const RETENTION = {
  id: 'nordwind.customs-retention', version: '1.0.0',
  targets: [{ layer_id: 'L37', layer_name: 'Audit-at-rest Encryption + Retention' }],
  quality_gates: [
    { gate_id: 'lf-primitive', test_path: 'tests/layer_forge/test_primitive.py' },
    { gate_id: 'lf-registry', test_path: 'tests/layer_forge/test_registry.py' },
  ],
  enforcement_rules: [],
};

const layerOnly = (id: string, version: string, manifest: Record<string, unknown>) => ({
  id: `lab-${id.replace(/[^a-z0-9]/gi, '-').toLowerCase()}`, version: '1.0.0',
  artifacts: [{ kind: 'layer', id, version, files: { 'manifest.json': { json: { id, version, ...manifest } } } }],
});
const L34 = [{ layer_id: 'L34', layer_name: 'Data Flow Guard' }];

test.beforeAll(async () => {
  a = await apiSession(A);
  b = await apiSession(B);
  setReviewer(A, { verdict: 'PASS' });
  setReviewer(B, { verdict: 'PASS' });
});
test.afterAll(() => { setReviewer(A, { verdict: 'PASS' }); setReviewer(B, { verdict: 'PASS' }); });

test('forge a layer on A through the console: real pytest gates pass', async () => {
  const r = await a.api.post('/v1/console/layer-forge/definitions', { headers: { 'X-CSRF-Token': a.csrf }, data: RETENTION });
  expect(r.status(), await r.text()).toBe(200);
  const body = await r.json();
  expect(body.status).toBe('SUCCESS');
  expect(body.registry_key).toBe('nordwind.customs-retention@1.0.0');
  expect(body.gate_verdicts.map((g: any) => [g.gate_id, g.status])).toEqual([['lf-primitive', 'PASS'], ['lf-registry', 'PASS']]);
  // The gate really ran pytest: its tail names a pass count.
  expect(body.gate_verdicts[0].detail).toMatch(/\d+ passed/);
});

test('the same id@version cannot be forged twice (409)', async () => {
  const r = await a.api.post('/v1/console/layer-forge/definitions', { headers: { 'X-CSRF-Token': a.csrf }, data: RETENTION });
  expect(r.status()).toBe(409);
});

test('export → ~/Downloads → import on B re-runs every gate on B', async () => {
  const zip = await saveExport(await exportApi(a, {
    bundle_id: 'nordwind-compliance', bundle_version: '1.0.0',
    description: 'L37 retention layer with its quality gates',
    selections: [{ kind: 'layer', id: RETENTION.id, version: RETENTION.version }],
  }), 'nordwind-compliance-1.0.0.zip');
  const m = JSON.parse(inspectBundle(zip).payloads['artifacts/layer/nordwind.customs-retention@1.0.0/manifest.json']);
  expect(m.quality_gates).toEqual(RETENTION.quality_gates);
  expect(m).not.toHaveProperty('status');

  const since = chain(B).lines;
  const r = await upload(b, 'import', zip);
  expect(r.status(), await r.text()).toBe(200);
  expect((await r.json()).outcomes).toEqual([
    expect.objectContaining({ kind: 'layer', id: RETENTION.id, status: 'forged' })]);
  // Gate evidence on B's OWN chain — the gates ran here, not on A.
  const gates = chain(B, since).events.filter((e) => e.event_type === 'layer_forge.quality_gate_evaluated');
  expect(gates, 'both pytest gates evaluated on B').toHaveLength(2);
});

test('reviewer down on B (verdict ERROR): the layer is refused, nothing is stored', async () => {
  setReviewer(B, { verdict: 'ERROR' });
  const zip = forgeBundle('layer-review-error', layerOnly('nordwind.reviewer-down', '1.0.0', { targets: L34, quality_gates: [], enforcement_rules: [] }));
  const r = await upload(b, 'import', zip);
  expect(r.status()).toBe(200);
  const body = await r.json();
  expect(body.failed_count).toBe(1);
  expect(body.outcomes[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('review') });
  const ids = (await (await b.api.get('/v1/console/layer-forge/definitions')).json()).items.map((l: any) => l.id);
  expect(ids).not.toContain('nordwind.reviewer-down');
  setReviewer(B, { verdict: 'PASS' });
});

test('reviewer flags on B: forged, flagged, and deploy needs an explicit override', async () => {
  setReviewer(B, { verdict: 'FLAGGED' });
  const zip = forgeBundle('layer-flagged', layerOnly('nordwind.flagged-egress', '2.0.0', {
    targets: [{ layer_id: 'L35', layer_name: 'Network Egress Lockdown' }], quality_gates: [], enforcement_rules: [] }));
  const body = await (await upload(b, 'import', zip)).json();
  setReviewer(B, { verdict: 'PASS' });
  expect(body.outcomes[0].status).toBe('forged');
  expect(body.outcomes[0].detail).toContain('review flagged it');

  const t = (data: object) => b.api.post('/v1/console/layer-forge/definitions/nordwind.flagged-egress/2.0.0/transition',
    { headers: { 'X-CSRF-Token': b.csrf }, data });
  expect((await t({ to_status: 'deployed' })).status()).toBe(409);
  const ok = await t({ to_status: 'deployed', override_review_flags: true, override_reason: 'egress allowlist reviewed by Nordwind SecOps' });
  expect(ok.status(), await ok.text()).toBe(200);
  expect(await ok.json()).toMatchObject({ status: 'deployed', review_flagged: true, review_flags: ['security_gap'] });
});

test('a gate whose test file does not exist on B: refused at the test phase', async () => {
  const zip = forgeBundle('layer-ghost-gate', layerOnly('nordwind.ghost-gate', '1.0.0', {
    targets: L34, enforcement_rules: [],
    quality_gates: [{ gate_id: 'ghost', test_path: 'tests/layer_forge/test_nordwind_only_on_hq.py' }] }));
  const body = await (await upload(b, 'import', zip)).json();
  expect(body.outcomes[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('test') });
});

test('gate paths that escape tests/ are refused before any pytest run', async () => {
  for (const [n, p] of [['dotdot', 'tests/../core/forge_bundle/validate.py'], ['dir', 'tests/layer_forge/'],
    ['abs', '/etc/passwd'], ['nonpy', 'tests/layer_forge/conftest.txt']] as const) {
    const zip = forgeBundle(`layer-escape-${n}`, layerOnly(`nordwind.escape-${n}`, '1.0.0', {
      targets: L34, enforcement_rules: [], quality_gates: [{ gate_id: 'g', test_path: p }] }));
    const r = await upload(b, 'import', zip);
    const body = await r.json();
    const failed = r.status() === 422 || body.outcomes?.[0]?.status === 'failed';
    expect(failed, `${p}: ${JSON.stringify(body)}`).toBe(true);
  }
});

test('gate caps: 17 gates on one layer, and 2×9 gates across one bundle, are refused', async () => {
  const gates = (n: number) => Array.from({ length: n }, (_, i) => ({ gate_id: `g${i}`, test_path: 'tests/layer_forge/test_primitive.py' }));
  const one = await (await upload(b, 'import', forgeBundle('layer-17-gates',
    layerOnly('nordwind.many-gates', '1.0.0', { targets: L34, enforcement_rules: [], quality_gates: gates(17) })))).json();
  expect(JSON.stringify(one)).toMatch(/at most 16 quality gates|16/);
  expect(one.outcomes?.[0]?.status ?? 'refused').not.toBe('forged');

  const t0 = Date.now();
  const two = await upload(b, 'import', forgeBundle('layer-2x9-gates', {
    id: 'lab-two-layers', version: '1.0.0',
    artifacts: ['x', 'y'].map((s) => ({ kind: 'layer', id: `nordwind.split-${s}`, version: '1.0.0',
      files: { 'manifest.json': { json: { id: `nordwind.split-${s}`, version: '1.0.0', targets: L34, enforcement_rules: [], quality_gates: gates(9) } } } })),
  }));
  // Refused as a whole BEFORE the first of the 18 pytest runs (each ~1 s here).
  expect(two.status()).toBe(422);
  expect(Date.now() - t0).toBeLessThan(5_000);
});

test('registry state smuggled in a manifest (status: deployed, _promoted_at) never lands', async () => {
  const zip = forgeBundle('layer-smuggled-state', layerOnly('nordwind.smuggled', '1.0.0', {
    targets: L34, quality_gates: [], enforcement_rules: [],
    status: 'deployed', review_flagged: false, review_flags: [], _promoted_at: 1, _created_at: 1 }));
  const body = await (await upload(b, 'import', zip)).json();
  expect(body.outcomes[0].status).toBe('forged');
  const def = await (await b.api.get('/v1/console/layer-forge/definitions/nordwind.smuggled?version=1.0.0')).json();
  expect(def.status).not.toBe('deployed');
});

test('manifest id/version differing from the envelope is refused', async () => {
  const zip = forgeBundle('layer-id-mismatch', {
    id: 'lab-mismatch', version: '1.0.0',
    artifacts: [{ kind: 'layer', id: 'nordwind.honest', version: '1.0.0',
      files: { 'manifest.json': { json: { id: 'nordwind.audit-sink', version: '3.2.1', targets: L34, quality_gates: [], enforcement_rules: [] } } } }],
  });
  const body = await (await upload(b, 'import', zip)).json();
  expect(body.outcomes[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('differ') });
});
