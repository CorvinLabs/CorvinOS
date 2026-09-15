#!/usr/bin/env node
// test_sticky_progress_wiring.js — structural regression gate for the
// Discord daemon's sticky-progress (edit-in-place) + finalize-guard wiring.
//
// daemon.js constructs a real discord.js Client at require-time, so it
// can't be safely required inside a fast unit test without live credentials
// (see test_daemon_boot.sh for the boot-smoke coverage of that). The
// platform-agnostic guard logic itself (shared/js/sticky_progress.js) has
// full behavioral unit tests in shared/js/test_sticky_progress.js.
//
// What THIS test guards against: a future refactor silently dropping the
// wiring between the two — e.g. reverting to the old per-daemon
// Map()-based bookkeeping, or losing the "check isFinalized before
// dispatching to Discord" ordering, or losing the message.edit() call that
// makes this "sticky" instead of "spam a new message per heartbeat".

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

console.log('== discord sticky-progress wiring ==');

const src = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

ok('requires the shared sticky_progress helper', () => {
  assert.ok(/require\(['"]\.\.\/shared\/js\/sticky_progress['"]\)/.test(src),
    'daemon.js must require ../shared/js/sticky_progress — bespoke per-daemon '
    + 'Map()-based bookkeeping would drift from the other bridges');
});

ok('instantiates makeStickyProgress()', () => {
  assert.ok(/makeStickyProgress\(/.test(src));
});

ok('sendDiscord checks isFinalized before dispatching', () => {
  const fnIdx = src.indexOf('async function sendDiscord');
  assert.ok(fnIdx > -1, 'sendDiscord function not found');
  const guardIdx = src.indexOf('sticky.isFinalized(', fnIdx);
  const editIdx  = src.indexOf('.edit(payload.text)', fnIdx);
  assert.ok(guardIdx > -1 && editIdx > -1, 'markers not both found');
  assert.ok(guardIdx < editIdx,
    'the finalize-guard must be checked BEFORE any edit/send dispatch');
});

ok('progress payloads edit the sticky message in place (Message.edit)', () => {
  assert.ok(src.includes('existing.msg.edit(payload.text)'),
    'sticky-edit call missing — without it every progress payload would '
    + 'send a brand-new message instead of editing the existing one');
});

ok('real reply marks the turn finalized via sticky.markFinalized', () => {
  assert.ok(src.includes('sticky.markFinalized(msgId)'));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
