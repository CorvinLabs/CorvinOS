/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 24 — an EXISTING forged skill attached to the Skill Forge (ADR-2254 slice 3a, T-0175), real engine: the user picks
 * "Attach existing…" in the composer, the skill's source passes the document stages, and a NEW skill is forged from it
 * (assistant.e2e_test_intake_review_skill - kept; reused unless FORGE_LIVE_REGEN=1).
 */
import { expect, test } from '@playwright/test';
import { apiSession, get, listSkills, openGenerator, REGEN, uiLogin, until, type Api } from './live';

const SOURCE = 'assistant.e2e_test_intake_skill';
const NAME = 'assistant.e2e_test_intake_review_skill';
let s: Api;
test.beforeAll(async () => { s = await apiSession(); });

test('pick an existing skill in the composer and forge a new skill from its source', async ({ context }) => {
  test.setTimeout(30 * 60_000);
  expect((await listSkills(s)).some((k) => String(k.name ?? k.skill_id) === SOURCE), 'spec 22 forged the source skill').toBeTruthy();
  if (!REGEN && (await listSkills(s)).some((k) => String(k.name ?? k.skill_id) === NAME)) return;
  const page = await uiLogin(context);
  await openGenerator(page, 'skill');
  await page.getByTestId('skill-request-attach-existing').selectOption(`skill:${SOURCE}`);
  await expect(page.getByTestId('attachment-chip')).toHaveAttribute('data-verdict', /admitted/, { timeout: 30_000 });
  await page.getByTestId('skill-request').fill(
    `Create a skill named ${NAME} that reviews whether an agent followed the procedure in the attached skill. `
    + `The skill's "name" MUST be exactly ${NAME}. Keep it under 40 lines.`);
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
});
