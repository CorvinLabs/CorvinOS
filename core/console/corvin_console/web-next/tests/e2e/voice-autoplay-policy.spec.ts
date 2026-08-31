/**
 * Playwright E2E tests — real-browser autoplay-policy coverage for
 * `useVoicePlayback` (src/lib/useVoicePlayback.ts) and its first caller,
 * `WelcomeStep` (src/components/setup/SetupGate.tsx).
 *
 * Blind spot this closes: the ONLY prior test for this hook
 * (tests/unit/useVoicePlayback.test.tsx) runs under vitest+happy-dom, where
 * `HTMLMediaElement.prototype.play` is unconditionally stubbed to reject —
 * it can verify JS control-flow (which branch runs) but cannot exercise a
 * real browser's autoplay-gesture-activation policy, which is the entire
 * reason this hook's SILENT_WAV/priming/"blocked" machinery exists. This
 * spec runs the real WelcomeStep against Chromium AND Firefox (the browser
 * the hook's own comments call out as "strictest") with NO prior page
 * interaction, and asserts:
 *   1. an unmuted `.play()` fired with no user gesture is actually rejected
 *      by the real browser (not a mock) → the "Tap to hear Corvin" fallback
 *      really is reachable in production, not just in the component's
 *      internal state machine.
 *   2. a genuine, trusted Playwright click on that affordance (a real
 *      user gesture, unlike happy-dom's untrusted synthetic events) really
 *      unblocks playback — the second `.play()` call actually resolves.
 *
 * IMPORTANT gotcha discovered while writing this spec: Playwright's own CDP
 * transport marks every `page.evaluate()` call (and therefore every
 * `expect(locator)`/`locator.*()` assertion, which use evaluate() internally)
 * with `userGesture: true`. Chromium's autoplay policy is "sticky" — one
 * gesture-approved play() permanently un-gates the whole frame — so a single
 * `expect(...).toBeVisible()` placed BEFORE the app's own un-gestured
 * autoplay attempt silently defeats the exact browser policy this spec exists
 * to observe (verified empirically: a bare `new Audio().play()` with zero
 * prior Playwright calls is really rejected; the same call after one
 * unrelated `page.evaluate(() => document.title)` unconditionally resolves,
 * on both Chromium and Firefox). The first assertion below therefore uses a
 * plain `page.waitForTimeout()` — never a locator/expect — until AFTER the
 * app's real, un-gestured play() attempt has already resolved on its own.
 *
 * Mock routing follows the LIFO rule used across this suite (see
 * adr-0125-engine-detection.spec.ts): the generic "**\/v1/**" fallback is
 * registered FIRST (checked LAST) so it swallows every unanticipated call —
 * this is a deliberate safety net so the test never depends on, or leaks
 * requests to, a real backend (see setupBaseMocks doc below).
 */
import { expect, test, type Page, type Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Shared test data
// ---------------------------------------------------------------------------

const MOCK_SESSION = {
  username: "test_admin",
  tenant_id: "_default",
  role: "admin",
  csrf_token: "csrf-test-token",
};

const GREETING_TEXT =
  "Hello. Voice, engine and pipeline all check out — you're ready to go.";

/** Build a small, genuinely-decodable 8-bit PCM mono WAV of silence so the
 * browser's real audio pipeline (decode → play → ended) has something to
 * actually do, instead of a zero-length clip. ~0.3s at 8kHz. */
function buildSilentWav(durationSeconds = 0.3, sampleRate = 8000): Buffer {
  const numSamples = Math.max(1, Math.round(durationSeconds * sampleRate));
  const dataSize = numSamples; // 8-bit = 1 byte/sample
  const buf = Buffer.alloc(44 + dataSize);
  buf.write("RIFF", 0, "ascii");
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write("WAVE", 8, "ascii");
  buf.write("fmt ", 12, "ascii");
  buf.writeUInt32LE(16, 16); // fmt chunk size
  buf.writeUInt16LE(1, 20); // PCM
  buf.writeUInt16LE(1, 22); // mono
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate, 28); // byte rate (1 byte/sample * 1 channel)
  buf.writeUInt16LE(1, 32); // block align
  buf.writeUInt16LE(8, 34); // bits per sample
  buf.write("data", 36, "ascii");
  buf.writeUInt32LE(dataSize, 40);
  buf.fill(0x80, 44); // 0x80 = silence for unsigned 8-bit PCM
  return buf;
}

/**
 * Mock every API call the authenticated app shell + SetupGate + WelcomeStep
 * make, so the test never touches a real backend. Registered in the LIFO
 * order documented in adr-0125-engine-detection.spec.ts: generic fallback
 * first (checked last), specific routes after (checked first).
 */
async function setupMocks(page: Page) {
  // 1. Generic fallback — anything unanticipated gets a harmless empty 200
  //    instead of ever reaching a real server.
  await page.route("**/v1/**", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );

  // 2. Session auth — the app must believe it's authenticated to render
  //    SetupGate at all.
  await page.route("**/v1/console/auth/whoami", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_SESSION),
    }),
  );

  // 3. Setup status — setup_complete: false is what makes SetupGate render
  //    (see SetupGate() in SetupGate.tsx).
  await page.route("**/v1/console/setup/status", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        setup_complete: false,
        engine_connected: false,
        bridges_configured: [],
      }),
    }),
  );

  // 4. Welcome self-check kickoff (POST) — WelcomeStep just needs SOME
  //    response; the actual result comes from the status poll below.
  await page.route("**/v1/console/setup/welcome-check", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ state: "running" }),
    }),
  );

  // 5. Welcome self-check status poll — resolve "done" immediately with a
  //    greeting so WelcomeStep calls playTts() right away instead of
  //    polling for up to 100s.
  await page.route("**/v1/console/setup/welcome-check/status", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ state: "done", greeting: GREETING_TEXT, lang: "en" }),
    }),
  );

  // 6. TTS — real, decodable audio bytes so play()/ended are genuine.
  await page.route("**/v1/console/voice/tts", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "audio/wav",
      body: buildSilentWav(),
    }),
  );
}

/**
 * How long to let the mocked async chain (whoami → setup/status →
 * welcome-check → welcome-check/status → tts fetch → audio.play()) settle
 * before inspecting the result. Deliberately a plain timer, NOT a Playwright
 * locator/expect poll — see the big comment on SETTLE_MS's only caller below
 * for why that distinction is load-bearing.
 */
const SETTLE_MS = 3_000;

/**
 * Instrument HTMLMediaElement.prototype.play BEFORE any app script runs, so
 * every real play() attempt (autoplay OR gesture-triggered) is recorded with
 * its actual, real-browser outcome (resolved/rejected) — the original
 * implementation still runs, so the browser's real autoplay policy is what
 * decides the outcome, not a mock.
 */
async function instrumentPlayback(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { __playAttempts: Array<{ muted: boolean; result?: string; errorName?: string }> };
    w.__playAttempts = [];
    const orig = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (this: HTMLMediaElement) {
      const rec: { muted: boolean; result?: string; errorName?: string } = { muted: this.muted };
      w.__playAttempts.push(rec);
      const result = orig.apply(this);
      return result.then(
        () => {
          rec.result = "resolved";
        },
        (err: unknown) => {
          rec.result = "rejected";
          rec.errorName = err instanceof DOMException ? err.name : String(err);
          throw err;
        },
      );
    };
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Voice autoplay policy — real browser (WelcomeStep / useVoicePlayback)", () => {
  // Fresh, isolated context per test with NO prior storage/session/gesture
  // history — a genuinely "first ever page load" the way a fresh install's
  // first-boot greeting actually happens in production.
  test.use({ storageState: { cookies: [], origins: [] } });

  test("a real browser blocks the un-gestured first-boot greeting and shows the tap-to-hear affordance", async ({ page }) => {
    await instrumentPlayback(page);
    await setupMocks(page);

    await page.goto("/console/app/chat");

    // --- Why this waits on a plain timer instead of expect(...).toBeVisible() ---
    // Chromium (and Firefox, empirically verified the same way) grant a frame
    // "sticky" user-activation the FIRST time it is ever gesture-approved, and
    // that activation then silently authorizes EVERY later programmatic
    // .play() call for the rest of the page's life — this is exactly the
    // "Firefox... auto-play the FIRST turn... allow every turn after it" class
    // of behavior _SILENT_WAV/unlock() exists to work around in the real app.
    // The catch: Playwright's own CDP transport (`Runtime.callFunctionOn`,
    // which underlies EVERY page.evaluate() call and therefore every
    // locator/expect() assertion too) unconditionally passes `userGesture:
    // true` — confirmed by reading node_modules/playwright-core's bundled
    // source and empirically, by instrumenting a bare `new Audio().play()`
    // with zero prior Playwright calls (rejected, real policy) vs. the same
    // page after ONE unrelated `page.evaluate(() => document.title)` (then
    // resolved, unconditionally) on both Chromium and Firefox. Concretely:
    // an `expect(page.getByText(...)).toBeVisible()` call placed here, BEFORE
    // WelcomeStep's own un-gestured playTts()-driven play() attempt has had a
    // chance to run, permanently taints the frame and makes the "was this
    // really blocked" assertion below vacuously true regardless of the app's
    // actual behavior — which is exactly what happened in an earlier draft of
    // this spec (it "passed" in a way that could never fail). So: no
    // locator/expect/evaluate calls are made until AFTER this timer, by which
    // point the mocked async chain (whoami → setup/status → welcome-check →
    // welcome-check/status → tts fetch → the FIRST real play() attempt) has
    // already resolved on its own, using nothing but the app's real JS timers
    // and network responses — untouched by Playwright's gesture leak.
    await page.waitForTimeout(SETTLE_MS);

    const preClickSnapshot = await page.evaluate(() => ({
      attempts: (window as unknown as { __playAttempts: Array<{ muted: boolean; result?: string }> }).__playAttempts,
      hasGreeting: document.body.innerText.includes("Voice, engine and pipeline"),
      hasTapToHear: document.body.innerText.toLowerCase().includes("tap to hear corvin"),
    }));

    // The written greeting rendered regardless of audio outcome.
    expect(preClickSnapshot.hasGreeting).toBe(true);

    // The core, previously-unverified contract: a REAL browser, with no
    // prior user gesture ANYWHERE on the page (not even one leaked in by our
    // own test harness), really did reject the un-gestured play() attempt
    // WelcomeStep fires automatically on mount — surfacing the "Tap to hear
    // Corvin" affordance. happy-dom cannot prove this; only a real
    // Chromium/Firefox engine enforcing its actual autoplay policy can, and
    // only if the harness itself stays out of the way until this point.
    expect(preClickSnapshot.hasTapToHear).toBe(true);

    const unmutedAttempts = preClickSnapshot.attempts.filter((a) => !a.muted);
    expect(unmutedAttempts.length).toBeGreaterThanOrEqual(1);
    expect(unmutedAttempts[0].result).toBe("rejected");

    // From this point on, ordinary Playwright locators are fine to use — the
    // one thing that had to stay untainted (the FIRST, un-gestured play()
    // attempt) has already happened and been captured above. A genuine click
    // on the affordance should still drive the same control-flow real users
    // rely on: playBlocked() resolves and the affordance disappears.
    const tapToHear = page.getByRole("button", { name: /tap to hear corvin/i });
    await tapToHear.click();
    await expect(tapToHear).toBeHidden({ timeout: 15_000 });

    const attemptsAfterClick = await page.evaluate(
      () => (window as unknown as { __playAttempts: Array<{ muted: boolean; result?: string }> }).__playAttempts,
    );
    const unmutedAfterClick = attemptsAfterClick.filter((a) => !a.muted);
    // One rejected (autoplay) + one resolved (post-gesture) real play() call.
    expect(unmutedAfterClick.length).toBeGreaterThanOrEqual(2);
    expect(unmutedAfterClick[unmutedAfterClick.length - 1].result).toBe("resolved");
  });
});
