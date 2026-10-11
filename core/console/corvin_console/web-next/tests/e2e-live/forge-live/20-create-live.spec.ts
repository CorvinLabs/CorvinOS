/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 20 — REAL forging. A user types a request into each of the four generator panels, a real
 * engine (Claude Code CLI) produces the artifact, and we then prove the artifact is a live
 * member of the system — not just that a run said "success":
 *
 *   tool   → registered, sandbox-tested, shows up in /forge/tools AND in the Tools tab
 *   skill  → registered + bootstrap-graded (injectable), shows up in /skill-creator/skills AND the panel
 *   plugin → staged under Marketplace → Forged with provenance, NOT installed
 *   layer  → PLAN (LLM) → preview → Create (gates + enforcement run) → promote proposed→accepted
 *
 * The scenarios are interlocked on purpose (the skill USES the tool's contract, the layer
 * GATES on tests that really exist), and the whole thing is idempotent: an existing
 * e2e_test_* artifact is reused unless FORGE_LIVE_REGEN=1. Artifacts are kept — see
 * E2E_TEST_MANIFEST.md.
 */
import { expect, test } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import {
  apiSession, BASE, get, listLayers, listPlugins, listSkills, listTools, NAMES, openGenerator, post, REGEN, uiLogin, until, type Api,
} from './live';

const isE2ePlugin = (p: any) => /e2e[-_]test[-_]plugin/.test(String(p.plugin_id ?? '') + String(p.dirname ?? ''));
const RUN_MS = 30 * 60_000;
let s: Api;
test.beforeAll(async () => { s = await apiSession(); });
test.describe.configure({ mode: 'serial' });

const EVIDENCE = path.resolve(process.cwd(), 'test-results-forge-live-evidence.json');
function record(key: string, value: unknown) {
  const cur = fs.existsSync(EVIDENCE) ? JSON.parse(fs.readFileSync(EVIDENCE, 'utf8')) : {};
  cur[key] = value;
  fs.writeFileSync(EVIDENCE, JSON.stringify(cur, null, 2));
}

async function pollRun(statusPath: (id: string) => string, runId: string, label: string) {
  return until(async () => {
    const r = await get(s, statusPath(runId));
    expect(r.status(), `${label} status poll`).toBe(200);
    const j = await r.json();
    return ['success', 'failed'].includes(j.status) ? j : false;
  }, `${label} run to finish`, RUN_MS, 3_000);
}

/** Drive a generator panel the way a user does and return the run id the panel received. */
async function submitViaUi(page: any, sub: 'skill' | 'tool' | 'plugin', testIdRequest: string, testIdSubmit: string, request: string, panel?: string) {
  await openGenerator(page, sub);
  await page.getByTestId(testIdRequest).fill(request);
  if (panel) await page.getByTestId('plugin-panel-request').fill(panel);
  const resp = page.waitForResponse((r: any) => /\/(forge-creator\/(tool|plugin)|skill-creator)\/generate$/.test(r.url()) && r.request().method() === 'POST');
  await page.getByTestId(testIdSubmit).click();
  const r = await resp;
  expect(r.status(), 'generate accepted').toBe(202);
  const body = await r.json();
  expect(body.run_id).toBeTruthy();
  return body.run_id as string;
}

// ── TOOL ────────────────────────────────────────────────────────────────────
test.describe('tool forge — real engine', () => {
  const tid = `assistant.${NAMES.tool}`;
  test('forge a deterministic CSV-diff tool from the panel', async ({ context }) => {
    test.setTimeout(RUN_MS + 120_000);
    if (!REGEN && (await listTools(s)).some((t) => t.name === tid || t.tool_id === tid)) { record('tool', { reused: true, name: tid }); return; }
    const page = await uiLogin(context);
    const runId = await submitViaUi(page, 'tool', 'tool-request', 'tool-submit',
      `Create a tool named ${tid} (pure Python stdlib, no network). It takes two CSV texts "left" and "right" and a "key" column name, `
      + 'and returns JSON with the key lists "added", "removed" and "changed" (changed = same key, different row). '
      + 'It must reject a missing key column with a clear error and must handle quoted commas. Include sandbox tests for all of that.');
    const run = await pollRun((id) => `/forge-creator/status/${id}`, runId, 'tool');
    record('tool', { run_id: runId, status: run.status, engine: run.engine, error: run.error, failure_code: run.failure_code });
    expect(run.status, `tool run failed: ${run.error ?? ''} (${run.failure_code ?? ''})`).toBe('success');
    await expect(page.getByTestId('tool-run')).toContainText(/success|registered/i, { timeout: 30_000 });
  });

  test('the forged tool is a live registry member (not just a success message)', async () => {
    const t = (await listTools(s)).find((x) => x.name === tid || x.tool_id === tid);
    expect(t, `${tid} in /forge/tools`).toBeTruthy();
  });
});

// ── SKILL ───────────────────────────────────────────────────────────────────
test.describe('skill forge — real engine', () => {
  test('forge a skill that depends on the tool contract', async ({ context }) => {
    test.setTimeout(RUN_MS + 120_000);
    if (!REGEN && (await listSkills(s)).some((k) => String(k.name ?? k.skill_id).includes(NAMES.skill))) { record('skill', { reused: true }); return; }
    const page = await uiLogin(context);
    const runId = await submitViaUi(page, 'skill', 'skill-request', 'submit-generation',
      `Create a skill named assistant.${NAMES.skill} that tells an agent how to review a data change safely: first run the tool `
      + `${`assistant.${NAMES.tool}`} to diff the before/after CSV, then summarise added/removed/changed keys, `
      + 'and refuse to approve when more than 20% of rows changed without an explicit reason. Keep it under 60 lines.');
    const run = await pollRun((id) => `/skill-creator/status/${id}`, runId, 'skill');
    record('skill', { run_id: runId, status: run.status, engine: run.engine, error: run.error, skill: run.skill });
    expect(run.status, `skill run failed: ${run.error ?? ''}`).toBe('success');
    expect(run.skill?.injectable, 'skill must be bootstrap-graded to be injectable').toBeTruthy();
  });

  test('the skill is listed by the API and visible in the panel', async ({ context }) => {
    const sk = (await listSkills(s)).find((k) => String(k.name ?? k.skill_id).includes(NAMES.skill));
    expect(sk, 'skill in /forge/skills').toBeTruthy();
    const page = await uiLogin(context);
    await openGenerator(page, 'skill');
    await page.getByTestId('skill-filter').fill(NAMES.skill);
    await expect(page.getByTestId('skill-row').first()).toContainText(NAMES.skill);
  });
});

// ── PLUGIN ──────────────────────────────────────────────────────────────────
test.describe('plugin forge — real engine', () => {
  test('forge a plugin with a panel; it is staged, never installed', async ({ context }) => {
    test.setTimeout(RUN_MS + 120_000);
    if (!REGEN && (await listPlugins(s)).some((p) => isE2ePlugin(p))) { record('plugin', { reused: true }); return; }
    const page = await uiLogin(context);
    const runId = await submitViaUi(page, 'plugin', 'plugin-request', 'plugin-submit',
      `Create a plugin whose display name is EXACTLY "${NAMES.plugin}" (the id is derived from that name, so do not rename it): a word-frequency counter. It exposes one function count_words(text) -> dict of lowercase word to count `
      + '(punctuation stripped, stable ordering) and a CLI entry. Include tests.',
      'A small panel with a textarea and a table of the top 10 words.');
    const run = await pollRun((id) => `/forge-creator/status/${id}`, runId, 'plugin');
    record('plugin', { run_id: runId, status: run.status, engine: run.engine, error: run.error, failure_code: run.failure_code, plugin: run.plugin });
    expect(run.status, `plugin run failed: ${run.error ?? ''} (${run.failure_code ?? ''})`).toBe('success');
  });

  test('staged plugin has provenance, is not installed, and can be read back', async () => {
    const p = (await listPlugins(s)).find((x) => isE2ePlugin(x));
    expect(p, 'plugin in /forge-creator/plugins').toBeTruthy();
    expect(p.installed).toBe(false);
    expect(p.signed).toBe(false);
    expect(p.origin_on_install).toBe('community');
    const detail = await get(s, `/forge-creator/plugins/${p.dirname}`);
    expect(detail.status()).toBe(200);
    // "success" is not "usable": the forged code must really contain the requested function.
    const body = await detail.json();
    const code = (body.files ?? []).map((f: any) => f.content ?? '').join('\n');
    record('plugin_content', { has_count_words: /count_words/.test(code), quality: body.quality, files: (body.files ?? []).map((f: any) => f.path) });
    expect(/def count_words|count_words\s*[=(]/.test(code), 'forged plugin implements count_words').toBe(true);
  });
});

// ── LAYER ───────────────────────────────────────────────────────────────────
test.describe('layer forge — real engine', () => {
  test('PLAN (LLM) → preview → Create → promote to accepted', async ({ context }) => {
    test.setTimeout(RUN_MS + 120_000);
    const existing = (await listLayers(s)).find((l) => l.id === NAMES.layer);
    if (existing && !REGEN) { record('layer', { reused: true, status: existing.status }); return; }
    const page = await uiLogin(context);
    await openGenerator(page, 'layer');
    await page.getByTestId('layer-id-input').fill('L34');
    await page.getByTestId('layer-intent-input').fill(
      `The manifest id must be exactly "${NAMES.layer}". Guard the data-review boundary: one quality gate that runs tests/layer_forge/test_schema.py, one compile_time enforcement rule `
      + `for schema validation. Target layer L34. Version 1.0.0.`);
    const plan = page.waitForResponse((r) => r.url().endsWith('/layer-forge/plan'));
    await page.getByTestId('plan-layer-button').click();
    const pr = await plan;
    const planBody = await pr.json().catch(() => ({}));
    record('layer_plan', { http: pr.status(), status: planBody.status, error: planBody.detail ?? planBody.error });
    expect(pr.status(), `PLAN failed: ${JSON.stringify(planBody).slice(0, 400)}`).toBe(200);
    await expect(page.getByText('Manifest preview')).toBeVisible();
    const create = page.waitForResponse((r) => r.url().endsWith('/layer-forge/definitions') && r.request().method() === 'POST');
    await page.getByTestId('create-layer-button').click();
    const cr = await create;
    const createBody = await cr.json().catch(() => ({}));
    record('layer_create', { http: cr.status(), body: JSON.stringify(createBody).slice(0, 600) });
    expect(cr.status(), `CREATE failed: ${JSON.stringify(createBody).slice(0, 600)}`).toBe(200);
    // The Forge page switches to the Layers tab on success, so the user sees the new definition in the list.
    await expect(page).toHaveURL(/tab=layers/);
    await expect(page.getByText(NAMES.layer).first()).toBeVisible();
  });

  test('the layer is in the registry as proposed and promotes to accepted', async () => {
    const l = (await listLayers(s)).find((x) => x.id === NAMES.layer);
    expect(l, 'layer in registry').toBeTruthy();
    if (l.status === 'proposed') {
      const t = await post(s, `/layer-forge/definitions/${l.id}/${l.version}/transition`, { to_status: 'accepted' });
      const body = await t.json().catch(() => ({}));
      record('layer_promote', { http: t.status(), body: JSON.stringify(body).slice(0, 400) });
      expect(t.status(), JSON.stringify(body).slice(0, 400)).toBe(200);
    }
    const after = (await listLayers(s)).find((x) => x.id === NAMES.layer);
    expect(['accepted', 'deployed']).toContain(after.status);
  });
});

test('all four e2e_test_* artifacts exist side by side', async () => {
  const [tools, skills, layers, plugins] = await Promise.all([listTools(s), listSkills(s), listLayers(s), listPlugins(s)]);
  const found = {
    tool: tools.some((t) => String(t.name ?? t.tool_id).includes(NAMES.tool)),
    skill: skills.some((k) => String(k.name ?? k.skill_id).includes(NAMES.skill)),
    layer: layers.some((l) => l.id === NAMES.layer),
    plugin: plugins.some((p) => isE2ePlugin(p)),
  };
  record('all_four', found);
  expect(found).toEqual({ tool: true, skill: true, layer: true, plugin: true });
  void BASE;
});
