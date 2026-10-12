/* eslint-disable @typescript-eslint/no-explicit-any -- untyped JSON from the live API */
/**
 * 23 — Layer Forge PLAN with an attached document and the real engine (ADR-2254 slice 5, T-0158): the policy text is the
 * input of the plan, its personal data is redacted first, nothing is created (PLAN is a preview).
 */
import { expect, test } from '@playwright/test';
import { openGenerator, uiLogin } from './live';

const POLICY = '# Boundary policy\nOnly the build pipeline may write to dist/. Violations must fail the build.\nOwner: lena.k@example.org\n';

test('Enter plans a layer from an attached policy; the manifest preview shows and no raw address is sent or returned', async ({ context }) => {
  test.setTimeout(10 * 60_000);
  const page = await uiLogin(context);
  const bodies: string[] = [];
  page.on('response', async (r) => { if (/layer-forge\/plan$/.test(r.url())) bodies.push(await r.text().catch(() => '')); });
  const sent: any[] = [];
  page.on('request', (r) => { if (/layer-forge\/plan$/.test(r.url())) sent.push(r.postDataJSON()); });
  await openGenerator(page, 'layer');
  await page.getByTestId('layer-intent-input-attach-input').setInputFiles({ name: 'e2e_test_policy.md', mimeType: 'text/markdown', buffer: Buffer.from(POLICY) });
  await expect(page.getByTestId('attachment-chip')).toHaveAttribute('data-verdict', 'admitted_redacted', { timeout: 30_000 });
  await page.getByTestId('layer-id-input').fill('L34');
  const ta = page.getByTestId('layer-intent-input');
  await ta.fill('Enforce the attached boundary policy at build time');
  await ta.press('Enter');
  await expect(page.getByText('Manifest preview')).toBeVisible({ timeout: 5 * 60_000 });
  expect(sent).toHaveLength(1);
  expect(sent[0].attachment_ids).toHaveLength(1);
  expect(JSON.stringify(sent[0])).not.toContain('lena.k');
  expect(bodies.join('')).not.toContain('lena.k');
});
