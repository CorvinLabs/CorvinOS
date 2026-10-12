/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 22 — Forge intake with a DOCUMENT and the real engine (ADR-2254 slice 2, T-0157): a runbook (markdown with a personal
 * e-mail address) is attached to the Skill Forge, a skill is forged from it. The skill must reflect the document's content
 * (a marker term only the document contains) and the address must appear nowhere in the run record.
 * Artifact: assistant.e2e_test_intake_skill (kept; reused unless FORGE_LIVE_REGEN=1).
 */
import { expect, test } from '@playwright/test';
import { apiSession, get, listSkills, openGenerator, REGEN, uiLogin, until, type Api } from './live';

const NAME = 'assistant.e2e_test_intake_skill';
const RUNBOOK = `# Nightly reindex runbook

1. Start the job NIGHTLY-REINDEX-7 at 02:00 UTC.
2. If it fails twice, page the on-call engineer anna.schmidt@example.org and open an incident.
3. Never restart the cluster while NIGHTLY-REINDEX-7 holds the lock.
`;
let s: Api;
test.beforeAll(async () => { s = await apiSession(); });

test('forge a skill from an attached, redacted runbook; the raw address never reaches the run', async ({ context }) => {
  test.setTimeout(30 * 60_000);
  if (!REGEN && (await listSkills(s)).some((k) => String(k.name ?? k.skill_id) === NAME)) return;
  const page = await uiLogin(context);
  await openGenerator(page, 'skill');
  await page.getByTestId('skill-request-attach-input').setInputFiles({ name: 'e2e_test_runbook.md', mimeType: 'text/markdown', buffer: Buffer.from(RUNBOOK) });
  await expect(page.getByTestId('attachment-chip')).toHaveAttribute('data-verdict', 'admitted_redacted', { timeout: 30_000 });
  await page.getByTestId('skill-request').fill(
    `Create a skill named ${NAME} that tells an agent how to operate the job described in the attached runbook. `
    + `The skill's "name" MUST be exactly ${NAME}. Keep it under 50 lines.`);
  const resp = page.waitForResponse((r) => /skill-creator\/generate$/.test(r.url()) && r.request().method() === 'POST');
  await page.getByTestId('skill-request').press('Enter');
  const r = await resp;
  expect(r.status()).toBe(202);
  const runId = (await r.json()).run_id as string;
  const run = await until(async () => {
    try {
      const j = await (await get(s, `/skill-creator/status/${runId}`)).json();
      return ['success', 'failed'].includes(j.status) ? j : false;
    } catch { return false; } // a reset socket under load is not a verdict on the run
  }, 'skill run to finish', 25 * 60_000, 5_000);
  expect(run.status, `skill run failed: ${run.error ?? ''}`).toBe('success');
  expect(run.skill?.name).toBe(NAME);
  const ev = await get(s, `/forge/runs/${runId}/events?after=0&limit=500`);
  expect(ev.status(), 'positive control: the events route answered').toBe(200);
  const everything = JSON.stringify(run) + JSON.stringify(await ev.json());
  expect(everything.length, 'positive control: the run recorded something').toBeGreaterThan(200);
  expect(everything, 'a raw address in the run record').not.toContain('anna.schmidt');
  const detail = await (await get(s, `/forge/skills`)).json();
  const sk = (detail.skills ?? []).find((k: any) => String(k.name ?? k.skill_id) === NAME);
  expect(sk, 'the skill is registered').toBeTruthy();
  expect(JSON.stringify(sk), 'the skill carries no raw address').not.toContain('anna.schmidt');
});
