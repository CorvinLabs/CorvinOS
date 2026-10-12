/* eslint-disable @typescript-eslint/no-explicit-any -- browser-side stubs */
/**
 * 19 — Forge intake (ADR-2254, slice 1): attach a table to the Tool Forge. The upload and the analysis are REAL (live
 * console, real Layer 24 projection); only the generate call is intercepted, so no engine minutes are spent here.
 */
import { expect, test } from '@playwright/test';
import { openGenerator, uiLogin } from './live';

const CSV = 'email,product,qty\n' + Array.from({ length: 30 }, (_, i) => `user${i}@example.org,widget${i},${i}`).join('\n') + '\n';

const attach = (page: any, name: string, body: string, mimeType = 'text/csv') =>
  page.getByTestId('tool-request-attach-input').setInputFiles({ name, mimeType, buffer: Buffer.from(body) });

test('a table with an e-mail column is admitted, shown as redacted, and its id rides the generate request', async ({ context }) => {
  const page = await uiLogin(context);
  const sent: any[] = [];
  await page.route('**/forge-creator/tool/generate', (r) => {
    sent.push(r.request().postDataJSON());
    return r.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: 'stop here' }) });
  });
  await openGenerator(page, 'tool');
  await attach(page, 'e2e_test_customers.csv', CSV);
  const chip = page.getByTestId('attachment-chip');
  await expect(chip).toHaveAttribute('data-verdict', 'admitted_redacted', { timeout: 30_000 });
  await expect(page.getByTestId('attachment-verdict')).toContainText('email hidden');
  const ta = page.getByTestId('tool-request');
  await ta.fill('dedupe the rows of the attached table');
  await ta.press('Enter');
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].attachment_ids).toHaveLength(1);
  expect(JSON.stringify(sent[0])).not.toContain('user1@example.org');
});

test('a file with a secret is refused, blocks Send until removed, and Enter sends nothing', async ({ context }) => {
  const page = await uiLogin(context);
  let sends = 0;
  await page.route('**/forge-creator/tool/generate', (r) => { sends++; return r.abort(); });
  await openGenerator(page, 'tool');
  await attach(page, 'e2e_test_keys.csv', 'a,b\n1,AKIAIOSFODNN7EXAMPLE\n');
  await expect(page.getByTestId('attachment-chip')).toHaveAttribute('data-verdict', 'refused', { timeout: 30_000 });
  await expect(page.getByTestId('attachment-verdict')).toContainText('secret');
  const ta = page.getByTestId('tool-request');
  await ta.fill('use the attached file for the tool');
  await expect(page.getByTestId('tool-submit')).toBeDisabled();
  await ta.press('Enter');
  await page.waitForTimeout(400);
  expect(sends).toBe(0);
  await page.getByRole('button', { name: /^Remove / }).click();
  await expect(page.getByTestId('tool-submit')).toBeEnabled();
});

test('the Skill and Plugin Forge show no attach button (no engine path consumes it yet)', async ({ context }) => {
  const page = await uiLogin(context);
  for (const sub of ['skill', 'plugin'] as const) {
    await openGenerator(page, sub);
    await expect(page.getByTestId(`${sub}-request-attach`)).toHaveCount(0);
  }
  await openGenerator(page, 'tool');
  await expect(page.getByTestId('tool-request-attach')).toBeVisible();
});
