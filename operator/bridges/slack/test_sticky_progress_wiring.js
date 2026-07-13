#!/usr/bin/env node
// test_sticky_progress_wiring.js — structural regression gate for the
// Slack daemon's sticky-progress (edit-in-place) + finalize-guard wiring.
//
// daemon.js constructs a real @slack/bolt App (Socket Mode) at require-time,
// so it can't be safely required inside a fast unit test without live
// credentials (see test_daemon_boot.sh for boot-smoke coverage). The
// platform-agnostic guard logic itself (shared/js/sticky_progress.js) has
// full behavioral unit tests in shared/js/test_sticky_progress.js. This
// test guards the wiring between the two so a future refactor can't
// silently regress to "post a new message per heartbeat".

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

console.log('== slack sticky-progress wiring ==');

const src = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

ok('requires the shared sticky_progress helper', () => {
  assert.ok(/require\(['"]\.\.\/shared\/js\/sticky_progress['"]\)/.test(src));
});

ok('instantiates makeStickyProgress()', () => {
  assert.ok(/makeStickyProgress\(/.test(src));
});

ok('sendSlack checks isFinalized before dispatching', () => {
  const fnIdx = src.indexOf('async function sendSlack');
  assert.ok(fnIdx > -1, 'sendSlack function not found');
  const guardIdx = src.indexOf('sticky.isFinalized(', fnIdx);
  const updateIdx = src.indexOf('chat.update(', fnIdx);
  assert.ok(guardIdx > -1 && updateIdx > -1, 'markers not both found');
  assert.ok(guardIdx < updateIdx,
    'the finalize-guard must be checked BEFORE any edit/send dispatch');
});

ok('progress payloads edit the sticky message via chat.update', () => {
  assert.ok(src.includes('app.client.chat.update('),
    'sticky-edit call missing — without it every progress payload would '
    + 'post a brand-new message instead of updating the existing one');
});

ok('sticky delete uses chat.delete', () => {
  assert.ok(src.includes('app.client.chat.delete('));
});

ok('real reply marks the turn finalized via sticky.markFinalized', () => {
  assert.ok(src.includes('sticky.markFinalized(msgId)'));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
