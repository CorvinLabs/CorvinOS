#!/usr/bin/env node
// test_reconnect_backoff.js — the exponential-backoff calculation daemon.js
// uses on reconnect.
//
// This is the fix for a real incident (2026-07-29 14:34): a persistent
// non-logout WhatsApp disconnect (reason 405/unknown) retried on a FIXED 1s
// delay forever — a genuine ban-risk DoS against WhatsApp's own servers,
// only stopped because a human manually killed the daemon. An earlier pass
// documented this fix in a comment but never actually wired it into the
// reconnect call (caught by an adversarial re-review, 2026-07-30) — this
// test file is the missing coverage that would have caught that gap.

const assert = require('assert');
const { computeReconnectDelay, BASE_MS, CAP_MS, FAST_RECONNECT_MS } = require('./reconnect_backoff');

let passed = 0;
let failed = 0;

function ok(label, fn) {
  try {
    fn();
    console.log(`  ok  ${label}`);
    passed += 1;
  } catch (e) {
    console.error(`  FAIL ${label}`);
    console.error(`       ${e.message}`);
    failed += 1;
  }
}

console.log('== reconnect_backoff ==');

const noJitter = () => 0; // deterministic RNG for exact-value assertions

ok('code 515 always gets the fast reconnect, never the backoff ladder', () => {
  const { delayMs, attemptsAfter } = computeReconnectDelay(515, 0, noJitter);
  assert.strictEqual(delayMs, FAST_RECONNECT_MS);
  assert.strictEqual(attemptsAfter, 0, '515 must not increment the attempt counter');
});

ok('code 515 stays fast even after several prior non-515 failures', () => {
  const { delayMs, attemptsAfter } = computeReconnectDelay(515, 5, noJitter);
  assert.strictEqual(delayMs, FAST_RECONNECT_MS);
  assert.strictEqual(attemptsAfter, 5, 'counter must pass through unchanged, not reset');
});

ok('first non-515 failure backs off to base (1s)', () => {
  const { delayMs, attemptsAfter } = computeReconnectDelay(405, 0, noJitter);
  assert.strictEqual(delayMs, BASE_MS);
  assert.strictEqual(attemptsAfter, 1);
});

ok('backoff doubles each attempt: 1s, 2s, 4s, 8s', () => {
  assert.strictEqual(computeReconnectDelay(405, 0, noJitter).delayMs, 1000);
  assert.strictEqual(computeReconnectDelay(405, 1, noJitter).delayMs, 2000);
  assert.strictEqual(computeReconnectDelay(405, 2, noJitter).delayMs, 4000);
  assert.strictEqual(computeReconnectDelay(405, 3, noJitter).delayMs, 8000);
});

ok('backoff is capped at 60s and never exceeds it, however many attempts', () => {
  const { delayMs } = computeReconnectDelay(405, 20, noJitter);
  assert.strictEqual(delayMs, CAP_MS);
});

ok('jitter adds a bounded, non-negative amount on top of the base', () => {
  const alwaysHalf = () => 0.5;
  const { delayMs } = computeReconnectDelay(405, 0, alwaysHalf);
  // base=1000, jitter=floor(0.5 * min(1000, 1000))=500
  assert.strictEqual(delayMs, 1500);
});

ok('jitter never pushes an attempt past roughly double its own base', () => {
  const alwaysMax = () => 0.999999;
  const { delayMs } = computeReconnectDelay(405, 0, alwaysMax);
  assert.ok(delayMs < 2000, `jitter blew past the intended bound: ${delayMs}`);
});

ok('a real-world hammering scenario (many rapid unknown-reason closes) '
   + 'genuinely slows down, not stays fixed at 1s', () => {
  let attempts = 0;
  const seen = [];
  for (let i = 0; i < 6; i += 1) {
    const r = computeReconnectDelay(undefined, attempts, noJitter);
    seen.push(r.delayMs);
    attempts = r.attemptsAfter;
  }
  // Strictly increasing (until the cap) — this is the literal guarantee
  // the incident needed: NOT six identical 1000ms delays in a row.
  for (let i = 1; i < seen.length; i += 1) {
    assert.ok(seen[i] >= seen[i - 1], `delay did not grow: ${seen}`);
  }
  assert.ok(seen[seen.length - 1] > seen[0], `no growth at all across 6 failures: ${seen}`);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
