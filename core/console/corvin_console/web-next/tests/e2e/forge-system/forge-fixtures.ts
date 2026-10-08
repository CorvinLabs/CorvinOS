/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the API, the bundle lab and the audit chain */
/**
 * Shared fixtures for the Forge-system suite (playwright.forge-system.config.ts).
 *
 * Two isolated installs (A = seeded "Nordwind HQ", B = empty "Depot Hamburg"),
 * an authenticated API context per install, the bundle lab (ZIP inspection and
 * hostile-bundle forging in Python stdlib), the production audit-chain
 * verifier, and the Downloads directory every export is saved to.
 */
import { test as base, expect, request, type APIRequestContext, type Page, type BrowserContext } from '@playwright/test';
import { execFileSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';
import { HOME_A, HOME_B, URL_A, URL_B } from './forge-env';

const here = path.dirname(fileURLToPath(import.meta.url));
const harness = path.join(here, 'harness');
export { REPO } from './repo-root';
import { REPO } from './repo-root';
export const PYTHON = path.join(REPO, 'core/console/.venv/bin/python');

export type Install = { name: 'A' | 'B'; url: string; home: string };
export const A: Install = { name: 'A', url: URL_A, home: HOME_A };
export const B: Install = { name: 'B', url: URL_B, home: HOME_B };
export const BUNDLES = '/v1/console/forge-bundles';

/** ~/Downloads/corvin-forge-e2e/<run>/ — one folder per run, kept after the run. */
const RUN = process.env.FORGE_E2E_RUN ?? new Date().toLocaleString('sv-SE').slice(0, 16).replace(/[: ]/g, '-');
process.env.FORGE_E2E_RUN = RUN;
export const DOWNLOADS = path.join(
  process.env.FORGE_E2E_DOWNLOADS ?? path.join(os.homedir(), 'Downloads', 'corvin-forge-e2e'), RUN);
fs.mkdirSync(DOWNLOADS, { recursive: true });
/** Keep the newest FORGE_E2E_KEEP (default 10) run folders; older ones were piling up forever (R4-I-7).
 *  Only folders named like a run stamp are ever touched — never anything else in that directory. */
(() => {
  const root = path.dirname(DOWNLOADS);
  const keep = Number(process.env.FORGE_E2E_KEEP ?? 10);
  const runs = fs.readdirSync(root).filter((d) => /^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}$/.test(d)).sort();
  for (const old of runs.slice(0, Math.max(0, runs.length - keep))) {
    if (old !== RUN) fs.rmSync(path.join(root, old), { recursive: true, force: true });
  }
})();
/** Scratch dir for forged/hostile bundles — never mixed into Downloads. */
export const SCRATCH = fs.mkdtempSync(path.join(os.tmpdir(), 'forge-e2e-lab-'));

// ── bundle lab / chain verifier / CLI ──────────────────────────────────────

function py(script: string, args: string[], env: Record<string, string> = {}): any {
  const out = execFileSync(PYTHON, ['-I', path.join(harness, script), ...args], {
    encoding: 'utf-8', env: { ...process.env, ...env }, timeout: 120_000,
  });
  return JSON.parse(out.trim().split('\n').pop()!);
}

export type Inspected = {
  entries: { name: string; size: number; compress_size: number }[];
  envelope: any;
  problems: string[];
  undeclared: string[];
  payloads: Record<string, any>;
};
export const inspectBundle = (zip: string): Inspected => py('bundle_lab.py', ['inspect', zip]);

let recipeN = 0;
/** Build a bundle from a recipe (see bundle_lab.build). Returns the ZIP path. */
export function forgeBundle(name: string, recipe: Record<string, unknown>): string {
  const r = path.join(SCRATCH, `recipe-${++recipeN}.json`);
  fs.writeFileSync(r, JSON.stringify(recipe));
  const out = path.join(SCRATCH, `${name}.zip`);
  py('bundle_lab.py', ['build', r, out]);
  return out;
}
/** Copy a real bundle's payload bytes into a new bundle with envelope overrides / tamper. */
export function graftBundle(name: string, base: string, recipe: Record<string, unknown>): string {
  const r = path.join(SCRATCH, `recipe-${++recipeN}.json`);
  fs.writeFileSync(r, JSON.stringify(recipe));
  const out = path.join(SCRATCH, `${name}.zip`);
  py('bundle_lab.py', ['graft', base, r, out]);
  return out;
}

export type Chain = { ok: boolean; problems: any[]; lines: number; events: { line: number; event_type: string; details: any }[] };
export const chain = (inst: Install, since = 0): Chain => py('chain_check.py', [inst.home, String(since)]);

/** Plugin Forge has no console export (by design) — the CLI is the export path. */
export function cliExport(inst: Install, args: string[]): any {
  const pp = ['', 'core/plugins', 'corvin_operator/bridges/shared', 'corvin_operator/skill-forge',
    'corvin_operator/forge', 'core/compliance', 'core/license', 'core/gateway', 'core/console']
    .map((p) => (p ? path.join(REPO, p) : REPO)).join(':');
  const out = execFileSync(PYTHON, [path.join(REPO, 'scripts/forge_bundle_cli.py'), 'export', ...args], {
    encoding: 'utf-8', cwd: inst.home, timeout: 120_000,
    env: { ...process.env, PYTHONPATH: pp, CORVIN_HOME: inst.home, CORVIN_TENANT_ID: '_default',
      CORVIN_AUDIT_ANCHOR_KEY: path.join(inst.home, 'audit-anchor.key'), XDG_CONFIG_HOME: path.join(inst.home, 'xdg'),
      FORGE_ROOT: '', VOICE_AUDIT_PATH: '' },
  });
  return JSON.parse(out);
}

/** Steer the stubbed Layer Forge reviewer (the one replaced external boundary). */
/** Steer the PLAN-phase model double (harness: <home>/e2e-control/plan.json). */
export function setPlanner(inst: Install, cfg: { mode: 'manifest' | 'error'; manifest?: Record<string, unknown>; message?: string; delay_s?: number }) {
  fs.writeFileSync(path.join(inst.home, 'e2e-control/plan.json'), JSON.stringify({ delay_s: 0, ...cfg }));
}

export function setReviewer(inst: Install, cfg: { verdict: 'PASS' | 'FLAGGED' | 'ERROR'; delay_s?: number }) {
  fs.writeFileSync(path.join(inst.home, 'e2e-control/layer_review.json'), JSON.stringify({ delay_s: 0, ...cfg }));
}

// ── sessions ───────────────────────────────────────────────────────────────

export type Session = { api: APIRequestContext; csrf: string; inst: Install };

export async function apiSession(inst: Install): Promise<Session> {
  const api = await request.newContext({ baseURL: inst.url });
  const login = await api.get('/v1/console/auth/local-login');
  expect(login.ok(), `local-login on ${inst.name}`).toBeTruthy();
  const who = await api.get('/v1/console/auth/whoami');
  expect(who.status()).toBe(200);
  const body = await who.json();
  expect(body.tenant_id).toBe('_default');
  return { api, csrf: body.csrf_token, inst };
}

export async function uiLogin(context: BrowserContext, inst: Install): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${inst.url}/v1/console/auth/local-login`);
  await page.waitForURL(/\/console\//);
  return page;
}

export async function openBundles(page: Page, inst: Install) {
  await page.goto(`${inst.url}/console/app/forge?tab=bundles`);
  // Positive control: the panel's own marker string, not just "something rendered".
  await expect(page.getByText('Share forged artifacts as one bundle')).toBeVisible();
}

export async function upload(s: Session, route: 'validate' | 'import', zip: string | Buffer, opts: { timeout?: number; csrf?: string | null } = {}) {
  const buffer = typeof zip === 'string' ? fs.readFileSync(zip) : zip;
  const headers: Record<string, string> = {};
  if (opts.csrf !== null) headers['X-CSRF-Token'] = opts.csrf ?? s.csrf;
  return s.api.post(`${BUNDLES}/${route}`, {
    headers, timeout: opts.timeout ?? 120_000,
    multipart: { file: { name: 'bundle.zip', mimeType: 'application/zip', buffer } },
  });
}

export async function exportApi(s: Session, body: { bundle_id: string; bundle_version: string; description?: string; selections: { kind: string; id: string; version: string }[] }) {
  return s.api.post(`${BUNDLES}/export`, { headers: { 'X-CSRF-Token': s.csrf }, data: body });
}

/** Save a successful export response into Downloads and return the path. */
export async function saveExport(res: Awaited<ReturnType<typeof exportApi>>, fileName: string): Promise<string> {
  expect(res.status(), await res.text().catch(() => '')).toBe(200);
  const p = path.join(DOWNLOADS, fileName);
  fs.writeFileSync(p, await res.body());
  return p;
}

export async function quarantine(s: Session): Promise<any[]> {
  const r = await s.api.get(`${BUNDLES}/quarantine`);
  expect(r.status()).toBe(200);
  return (await r.json()).items;
}

export async function decide(s: Session, qid: string, action: 'accept' | 'reject') {
  return s.api.post(`${BUNDLES}/quarantine/${qid}/${action}`, { headers: { 'X-CSRF-Token': s.csrf } });
}

export function toolRegistry(inst: Install): Record<string, any> {
  const p = path.join(inst.home, 'tenants/_default/forge/registry.json');
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf-8')) : {};
}

export const test = base;
export { expect };
