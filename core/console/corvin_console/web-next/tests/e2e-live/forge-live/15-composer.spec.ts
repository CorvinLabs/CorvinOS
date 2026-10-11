/* eslint-disable @typescript-eslint/no-explicit-any -- browser-side stubs */
/**
 * 15 — the shared ForgeComposer (CONCEPT-0104, E-027): Enter sends, Shift+Enter does not, hold-Space dictates.
 * No engine: the generate route is intercepted at the browser boundary, the panel code is the shipped bundle.
 * Speech recognition is stubbed in the page (a browser capability, not the code under test).
 */
import { expect, test, type Page } from '@playwright/test';
import { openGenerator, uiLogin } from './live';

const KINDS = [
  { kind: 'skill', textarea: 'skill-request', submit: 'submit-generation', route: '**/skill-creator/generate' },
  { kind: 'tool', textarea: 'tool-request', submit: 'tool-submit', route: '**/forge-creator/tool/generate' },
  { kind: 'plugin', textarea: 'plugin-request', submit: 'plugin-submit', route: '**/forge-creator/plugin/generate' },
] as const;

const FAKE_SPEECH = `
  class FakeRecognition {
    continuous = false; interimResults = false; lang = 'en-US';
    onresult = null; onerror = null; onend = null; _t = null;
    start() {
      window.__speech_started = (window.__speech_started || 0) + 1;
      this._t = setTimeout(() => this.onresult && this.onresult({ resultIndex: 0,
        results: [{ isFinal: true, 0: { transcript: 'dictated words' } }] }), 80);
    }
    stop() { clearTimeout(this._t); setTimeout(() => this.onend && this.onend(), 10); }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
`;

async function countGenerates(page: Page, route: string) {
  const bodies: any[] = [];
  await page.route(route, (r) => {
    bodies.push(r.request().postDataJSON());
    return r.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: 'stop here' }) });
  });
  return bodies;
}

for (const k of KINDS) {
  test.describe(`${k.kind} composer`, () => {
    test('Enter sends a valid request once, with the exact text', async ({ context }) => {
      const page = await uiLogin(context);
      const sent = await countGenerates(page, k.route);
      await openGenerator(page, k.kind);
      const ta = page.getByTestId(k.textarea);
      await ta.fill('a request long enough to be sent');
      await ta.press('Enter');
      await expect.poll(() => sent.length).toBe(1);
      expect(sent[0].user_request).toBe('a request long enough to be sent');
      await expect(page.getByRole('alert')).toContainText('stop here');
    });

    test('Shift+Enter inserts a newline and sends nothing', async ({ context }) => {
      const page = await uiLogin(context);
      const sent = await countGenerates(page, k.route);
      await openGenerator(page, k.kind);
      const ta = page.getByTestId(k.textarea);
      await ta.fill('first line of a request');
      await ta.press('Shift+Enter');
      await ta.pressSequentially('second line');
      await expect(ta).toHaveValue('first line of a request\nsecond line');
      await page.waitForTimeout(300);
      expect(sent.length).toBe(0);
    });

    test('Enter on a too-short request shows the inline refusal and sends nothing', async ({ context }) => {
      const page = await uiLogin(context);
      const sent = await countGenerates(page, k.route);
      await openGenerator(page, k.kind);
      const ta = page.getByTestId(k.textarea);
      await ta.fill('too short');
      await ta.press('Enter');
      await expect(page.getByRole('alert')).toContainText('at least 10 characters');
      expect(sent.length).toBe(0);
    });

    test('Enter on blank input sends nothing (the button is disabled, so is the key)', async ({ context }) => {
      const page = await uiLogin(context);
      const sent = await countGenerates(page, k.route);
      await openGenerator(page, k.kind);
      const ta = page.getByTestId(k.textarea);
      await ta.fill('      ');
      await ta.press('Enter');
      await page.waitForTimeout(300);
      expect(sent.length).toBe(0);
      await expect(page.getByTestId(k.submit)).toBeDisabled();
    });

    test('the counter follows the text and an over-long request cannot be sent', async ({ context }) => {
      const page = await uiLogin(context);
      const sent = await countGenerates(page, k.route);
      await openGenerator(page, k.kind);
      const ta = page.getByTestId(k.textarea);
      await ta.fill('twelve chars');
      await expect(page.getByTestId(`${k.textarea}-count`)).toHaveText('12/4000');
      await ta.fill('x'.repeat(4001));
      await expect(page.getByTestId(`${k.textarea}-count`)).toHaveText('4001/4000');
      await expect(page.getByTestId(k.submit)).toBeDisabled();
      await ta.press('Enter');
      await page.waitForTimeout(300);
      expect(sent.length).toBe(0);
    });

    test('hold Space dictates into the text and does not send; a tap types a space', async ({ context }) => {
      const page = await uiLogin(context);
      await page.addInitScript(FAKE_SPEECH);
      const sent = await countGenerates(page, k.route);
      await openGenerator(page, k.kind);
      // Focus the page body (not the textarea): hold Space ≥ 400 ms starts dictation, release stops it.
      await page.locator('body').click({ position: { x: 5, y: 5 } });
      await page.keyboard.down('Space');
      await page.waitForTimeout(700);
      await expect(page.getByTestId(`${k.textarea}-mic`)).toHaveAttribute('aria-label', 'Stop recording');
      await page.keyboard.up('Space');
      await expect(page.getByTestId(k.textarea)).toHaveValue(/dictated words/);
      expect(sent.length, 'dictation must never send by itself').toBe(0);
      // A short tap inside the textarea types an ordinary space and starts no dictation.
      const ta = page.getByTestId(k.textarea);
      await ta.fill('a');
      await ta.press('Space');
      await page.waitForTimeout(600);
      await expect(ta).toHaveValue('a ');
      await expect(page.getByTestId(`${k.textarea}-mic`)).toHaveAttribute('aria-label', 'Start recording');
    });
  });
}

test('switching between the composer sub-tabs never accumulates Space listeners', async ({ context }) => {
  const page = await uiLogin(context);
  await page.addInitScript(`
    (() => {
      let active = 0;
      const add = window.addEventListener.bind(window), rem = window.removeEventListener.bind(window);
      const live = new Map();
      window.addEventListener = (t, f, o) => { if (t === 'keydown') { live.set(f, (live.get(f) || 0) + 1); active++; } return add(t, f, o); };
      window.removeEventListener = (t, f, o) => { if (t === 'keydown' && live.get(f)) { live.set(f, live.get(f) - 1); active--; } return rem(t, f, o); };
      window.__keydown_active = () => active;
    })();
  `);
  await openGenerator(page, 'skill');
  const count = () => page.evaluate(() => (window as any).__keydown_active());
  const base = await count();
  // One page load throughout: switch with the tab buttons so mount/unmount really happens.
  for (let i = 0; i < 4; i++) {
    for (const [label, marker] of [['Tool Forge', 'tool-forge'], ['Plugin Forge', 'plugin-forge'],
      ['Layer Forge', 'forge-layer-panel'], ['Skill Forge', 'submit-generation']] as const) {
      await page.getByRole('tab', { name: label }).click();
      await expect(page.getByTestId(marker)).toBeVisible();
    }
  }
  // Positive control: a listener count of 0 would pass a leak check vacuously.
  expect(base).toBeGreaterThan(0);
  expect(await count()).toBe(base);
});

// ── Layer generator (T-0144): the intent is a composer, Layer ID stays a field, Enter runs PLAN only ──

test.describe('layer composer', () => {
  const MANIFEST = { id: 'e2e_test_fake', version: '1.0.0', targets: [{ layer_id: 'L34' }], quality_gates: [], enforcement_rules: [] };

  test('Enter runs PLAN with the id and the intent, shows the preview, and creates nothing', async ({ context }) => {
    const page = await uiLogin(context);
    const plans: any[] = [];
    let creates = 0;
    await page.route('**/layer-forge/plan', (r) => { plans.push(r.request().postDataJSON());
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'SUCCESS', manifest: MANIFEST }) }); });
    await page.route('**/layer-forge/definitions', (r) => { if (r.request().method() === 'POST') creates++; return r.continue(); });
    await openGenerator(page, 'layer');
    await page.getByTestId('layer-id-input').fill('L34');
    const ta = page.getByTestId('layer-intent-input');
    await ta.fill('Guard the boundary at build time');
    await ta.press('Enter');
    await expect.poll(() => plans.length).toBe(1);
    expect(plans[0]).toEqual({ layer_id: 'L34', intent: 'Guard the boundary at build time' });
    await expect(page.getByText('Manifest preview')).toBeVisible();
    await page.waitForTimeout(400);
    expect(creates, 'Enter must never create').toBe(0);
  });

  test('Enter without a Layer ID sends nothing; Shift+Enter is a newline', async ({ context }) => {
    const page = await uiLogin(context);
    let plans = 0;
    await page.route('**/layer-forge/plan', (r) => { plans++; return r.abort(); });
    await openGenerator(page, 'layer');
    const ta = page.getByTestId('layer-intent-input');
    await ta.fill('an intent without an id');
    await ta.press('Enter');
    await ta.press('Shift+Enter');
    await ta.pressSequentially('second line');
    await expect(ta).toHaveValue('an intent without an id\nsecond line');
    await page.waitForTimeout(300);
    expect(plans).toBe(0);
    await expect(page.getByTestId('plan-layer-button')).toBeDisabled();
  });

  test('hold Space dictates into the intent', async ({ context }) => {
    const page = await uiLogin(context);
    await page.addInitScript(FAKE_SPEECH);
    await openGenerator(page, 'layer');
    await page.locator('body').click({ position: { x: 5, y: 5 } });
    await page.keyboard.down('Space');
    await page.waitForTimeout(700);
    await page.keyboard.up('Space');
    await expect(page.getByTestId('layer-intent-input')).toHaveValue(/dictated words/);
  });
});
