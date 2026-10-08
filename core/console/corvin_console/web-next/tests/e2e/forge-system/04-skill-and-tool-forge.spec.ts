/**
 * 04 — Skill Forge and Tool Forge specifics.
 *
 * Skills: SemVer build metadata survives the trip, the skill package's own
 * hash is enforced by the installer, a version the store does not hold cannot
 * be exported. Tools: the review queue's accept/reject/unknown-id contract,
 * a name already taken on the target, an unsupported runtime, non-UTF-8 code,
 * a spec naming another tool, and the dependency (requires) checks.
 */
import fs from 'fs';
import {
  A, B, apiSession, decide, exportApi, expect, forgeBundle, graftBundle, inspectBundle, quarantine, saveExport, test,
  toolRegistry, upload, type Session,
} from './forge-fixtures';

test.describe.configure({ mode: 'serial' });

let a: Session;
let b: Session;
let skillBundle = '';

test.beforeAll(async () => {
  a = await apiSession(A);
  b = await apiSession(B);
});

const tool = (name: string, impl: string | object, extra: Record<string, unknown> = {}) => ({
  kind: 'tool', id: name, version: '1.0.0',
  files: {
    'spec.json': { json: { name, description: 'lab tool', input_schema: { type: 'object' }, runtime: 'python',
      version: '1.0.0', impl_filename: 'impl.py', meta: null, ...extra } },
    'impl.py': impl,
  },
});

// ── Skill Forge ────────────────────────────────────────────────────────────

test('skill with SemVer build metadata (0.9.0+build.17) round-trips into B\'s installed store', async () => {
  skillBundle = await saveExport(await exportApi(a, {
    bundle_id: 'nordwind-narration', bundle_version: '0.9.0',
    selections: [{ kind: 'skill', id: 'eta-narrator', version: '0.9.0+build.17' }],
  }), 'nordwind-narration-0.9.0.zip');
  const body = await (await upload(b, 'import', skillBundle)).json();
  expect(body.outcomes).toEqual([expect.objectContaining({ kind: 'skill', status: 'installed', detail: 'eta-narrator@0.9.0+build.17' })]);
  expect(fs.readdirSync(`${B.home}/skills_installed`).join(' ')).toContain('eta-narrator');
});

test('exporting a skill version the store does not hold is refused (422)', async () => {
  const r = await exportApi(a, { bundle_id: 'x', bundle_version: '1.0.0',
    selections: [{ kind: 'skill', id: 'invoice-extract', version: '9.9.9' }] });
  expect(r.status()).toBe(422);
  expect((await r.json()).detail).toContain("on-disk version");
});

test('a skill package swapped for another one fails integrity (envelope hash)', async () => {
  // Graft A's eta-narrator bundle, flip one byte of the skill ZIP after hashing.
  const z = inspectBundle(skillBundle);
  const zipPath = z.envelope.artifacts[0].files[0].path;
  const bad = graftBundle('skill-tampered', skillBundle, { tamper: [{ op: 'flip_byte', path: zipPath }] });
  const v = await (await upload(b, 'validate', bad)).json();
  expect(v).toMatchObject({ valid: false, stage: expect.stringMatching(/integrity|container/) });
});

// ── Tool Forge ─────────────────────────────────────────────────────────────

test('a tool name already taken on the target is refused at staging', async () => {
  // B accepted nordwind.route_cost in spec 01.
  expect(Object.keys(toolRegistry(B))).toContain('nordwind.route_cost');
  const body = await (await upload(b, 'import', forgeBundle('tool-taken', {
    id: 'lab-taken', version: '1.0.0', artifacts: [tool('nordwind.route_cost', 'def run(req):\n    return {"eur": 0}\n')] }))).json();
  expect(body.outcomes[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('already exists') });
  // ... and case does not get around it.
  const cased = await (await upload(b, 'import', forgeBundle('tool-taken-case', {
    id: 'lab-taken-case', version: '1.0.0', artifacts: [tool('NORDWIND.Route_Cost', 'def run(req):\n    return 1\n')] }))).json();
  expect(cased.outcomes[0].status).toBe('failed');
});

test('tool spec naming another tool than the envelope is refused', async () => {
  const body = await (await upload(b, 'import', forgeBundle('tool-name-swap', {
    id: 'lab-swap', version: '1.0.0',
    artifacts: [tool('nordwind.harmless', 'def run(req):\n    return 1\n', { name: 'nordwind.route_cost' })] }))).json();
  expect(body.outcomes[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('envelope names') });
});

test('unsupported runtime and non-UTF-8 code are refused', async () => {
  const node = await (await upload(b, 'import', forgeBundle('tool-node', {
    id: 'lab-node', version: '1.0.0', artifacts: [tool('nordwind.nodejs', 'console.log(1)', { runtime: 'node' })] }))).json();
  expect(node.outcomes[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('runtime') });
  const latin1 = await (await upload(b, 'import', forgeBundle('tool-latin1', {
    id: 'lab-latin1', version: '1.0.0', artifacts: [tool('nordwind.latin1', { b64: Buffer.from([0x23, 0x20, 0xe4, 0xf6, 0xfc, 0x0a]).toString('base64') })] }))).json();
  expect(latin1.outcomes[0]).toMatchObject({ status: 'failed', detail: expect.stringContaining('UTF-8') });
});

test('review queue contract: unknown id 404, malformed id 404, double decision 404', async () => {
  const body = await (await upload(b, 'import', forgeBundle('tool-review-contract', {
    id: 'lab-review', version: '1.0.0', artifacts: [tool('nordwind.pallet_count', 'def run(req):\n    return {"pallets": 33}\n')] }))).json();
  const qid = body.outcomes[0].detail;
  expect(qid).toMatch(/^[0-9a-f]{32}$/);
  expect((await decide(b, '0'.repeat(32), 'accept')).status()).toBe(404);
  expect((await decide(b, '..%2F..%2Fetc', 'accept')).status()).toBe(404);
  expect((await decide(b, qid, 'reject')).status()).toBe(200);
  expect((await decide(b, qid, 'reject')).status()).toBe(404);
  expect((await decide(b, qid, 'accept')).status()).toBe(404);
  expect(Object.keys(toolRegistry(B))).not.toContain('nordwind.pallet_count');
});

test('requires: an in-bundle version mismatch and a cycle are refused at "references"', async () => {
  const mismatch = await (await upload(b, 'validate', forgeBundle('req-mismatch', {
    id: 'lab-req', version: '1.0.0', artifacts: [
      { ...tool('nordwind.a', 'def run(r):\n    return 1\n'), requires: [{ kind: 'tool', id: 'nordwind.b', version: '2.0.0' }] },
      tool('nordwind.b', 'def run(r):\n    return 2\n')] }))).json();
  expect(mismatch).toMatchObject({ valid: false, stage: 'references' });
  const cycle = await (await upload(b, 'validate', forgeBundle('req-cycle', {
    id: 'lab-cycle', version: '1.0.0', artifacts: [
      { ...tool('nordwind.a', 'def run(r):\n    return 1\n'), requires: [{ kind: 'tool', id: 'nordwind.b', version: '1.0.0' }] },
      { ...tool('nordwind.b', 'def run(r):\n    return 2\n'), requires: [{ kind: 'tool', id: 'nordwind.a', version: '1.0.0' }] }] }))).json();
  expect(cycle).toMatchObject({ valid: false, stage: 'references', reason: expect.stringContaining('cycle') });
});

test('requires: an artifact the target does not have is refused at "staleness"', async () => {
  const v = await (await upload(b, 'validate', forgeBundle('req-stale', {
    id: 'lab-stale', version: '1.0.0', artifacts: [
      { ...tool('nordwind.uses_geo', 'def run(r):\n    return 1\n'), requires: [{ kind: 'layer', id: 'nordwind.not-on-depot', version: '1.0.0' }] }] }))).json();
  expect(v).toMatchObject({ valid: false, stage: 'staleness' });
  // Positive control: a requirement B DOES have passes.
  const ok = await (await upload(b, 'validate', forgeBundle('req-present', {
    id: 'lab-present', version: '1.0.0', artifacts: [
      { ...tool('nordwind.uses_guard', 'def run(r):\n    return 1\n'), requires: [{ kind: 'layer', id: 'nordwind.dataflow-guard', version: '1.1.0' }] }] }))).json();
  expect(ok.valid, JSON.stringify(ok)).toBe(true);
});

test('cleanup: nothing from this spec is left in B\'s review queue', async () => {
  for (const q of await quarantine(b)) if (q.bundle_id.startsWith('lab-')) await decide(b, q.quarantine_id, 'reject');
  expect((await quarantine(b)).filter((q) => q.bundle_id.startsWith('lab-'))).toEqual([]);
});
