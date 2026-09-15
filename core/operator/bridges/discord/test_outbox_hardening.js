#!/usr/bin/env node
// test_outbox_hardening.js — regression gate for the delivery-liveness wiring
// added after incident 2026-07-26.
//
// What happened: a sendFn call that never settled left the shared outbox
// poller's `running` flag stuck at true. Every subsequent tick returned at
// `if (running) return`, so the Discord daemon delivered NOTHING for 38
// minutes — no ack, no "⏳ Noch dabei …" heartbeat, no final reply — while
// the process stayed alive, the gateway socket stayed open and /status kept
// answering `paired: true`. Not one log line was written, so neither the
// watchdog nor the operator could see it.
//
// daemon.js builds a real discord.js Client at require-time and cannot be
// required without live credentials (see test_daemon_boot.sh), so the wiring
// is asserted structurally here; the poller behaviour itself has behavioural
// coverage in ../shared/js/test_outbox_poller.js.

const assert = require('assert');
const fs = require('fs');
const path = require('path');

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

console.log('== discord outbox hardening ==');

const src = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

ok('poller handle is captured, not discarded', () => {
  assert.ok(/const\s+outboxPoller\s*=\s*startOutboxPoller\(/.test(src),
    'the return value of startOutboxPoller must be kept — without the handle '
    + 'there is no way to expose stall state to /status');
});

ok('/status exposes poller_stalled_s', () => {
  assert.ok(/poller_stalled_s:\s*outboxPoller\.stats\(\)\.stalled_s/.test(src),
    'a live process with an open gateway socket is NOT proof that anything is '
    + 'being delivered — /status must publish the stall duration so an '
    + 'external watchdog can restart a wedged daemon');
});

// The snowflake guard. Extract the literal from daemon.js and exercise it, so
// this fails if the pattern is loosened (e.g. to \d+ which accepts "1") or
// tightened past real snowflake lengths.
const guardMatch = src.match(/if \(!\/\^(\\d\{\d+,\d+\})\$\/\.test\(String\(chId\)\)\)/);

ok('a non-snowflake chat_id is rejected before any REST call', () => {
  assert.ok(guardMatch,
    'sendDiscord must reject a malformed chat_id locally — 724 dead-lettered '
    + '"owner-chat" envelopes each burned a round-trip against Discord\'s '
    + 'invalid-request budget just to come back as 50035');
  const re = new RegExp(`^${guardMatch[1]}$`);
  assert.ok(!re.test('owner-chat'), 'the test placeholder must not pass');
  assert.ok(!re.test(''), 'empty must not pass');
  assert.ok(!re.test('123'), 'a short numeric id must not pass');
  assert.ok(!re.test('1515819896993222867x'), 'trailing garbage must not pass');
  assert.ok(re.test('1515819896993222867'), 'a real channel snowflake must pass');
  assert.ok(re.test('1299270034849533983'), 'a real user snowflake must pass');
});

ok('the local rejection reuses the Discord error code so it is retired at once', () => {
  const idx = src.indexOf('is not a snowflake');
  assert.ok(idx > 0, 'snowflake rejection message missing');
  const window = src.slice(idx, idx + 200);
  assert.ok(/err\.code\s*=\s*50035/.test(window),
    'the synthetic error must carry code 50035 so PERMANENT_DISCORD_CODES '
    + 'dead-letters it on attempt #1 instead of retrying it 20×');
});

ok('the guard runs before sendDiscord fetches the channel', () => {
  // Anchor inside sendDiscord: an unrelated channels.fetch lives in the
  // typing-indicator interval above it, so a plain indexOf would match that
  // one and pass for the wrong reason.
  const fnIdx = src.indexOf('async function sendDiscord(');
  assert.ok(fnIdx > 0, 'sendDiscord must exist');
  const guardIdx = src.indexOf('is not a snowflake', fnIdx);
  const fetchIdx = src.indexOf('await client.channels.fetch(chId)', fnIdx);
  assert.ok(guardIdx > 0 && fetchIdx > 0, 'both call sites must exist in sendDiscord');
  assert.ok(guardIdx < fetchIdx,
    'validating after the fetch would defeat the entire point — the round-trip '
    + 'would already have been spent');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
