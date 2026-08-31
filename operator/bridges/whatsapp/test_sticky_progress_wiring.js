#!/usr/bin/env node
// test_sticky_progress_wiring.js — structural regression gate for the
// WhatsApp daemon's sticky-progress (edit-in-place) + finalize-guard
// wiring.
//
// daemon.js connects to Baileys/WhatsApp at require-time (or starts a
// pairing loop), so it can't be safely required inside a fast unit test
// without live credentials (see test_daemon_boot.sh's comment on why the
// WhatsApp daemon is skipped there too). The platform-agnostic guard logic
// itself (shared/js/sticky_progress.js) has full behavioral unit tests in
// shared/js/test_sticky_progress.js. This test guards the wiring between
// the two so a future refactor can't silently regress to "send a new
// message per heartbeat" — the exact gap this mechanism was built to close
// (WhatsApp was one of the five daemons with no _progress/_heartbeat
// handling at all before this change).
//
// Modeled on test_outbox_disabled_chat_drop.js's structural-grep pattern,
// which this codebase already uses for daemon.js files that can't be
// required directly in tests.

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

console.log('== whatsapp sticky-progress wiring ==');

const src = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

ok('requires the shared sticky_progress helper', () => {
  assert.ok(/require\(['"]\.\.\/shared\/js\/sticky_progress['"]\)/.test(src));
});

ok('instantiates makeStickyProgress()', () => {
  assert.ok(/makeStickyProgress\(/.test(src));
});

ok('processOutbox checks isFinalized before the MOCK/real dispatch', () => {
  const fnIdx = src.indexOf('async function processOutbox()');
  assert.ok(fnIdx > -1, 'processOutbox function not found');
  const guardIdx = src.indexOf('sticky.isFinalized(', fnIdx);
  const editIdx  = src.indexOf('edit: existing.key', fnIdx);
  assert.ok(guardIdx > -1 && editIdx > -1, 'markers not both found');
  assert.ok(guardIdx < editIdx,
    'the finalize-guard must be checked BEFORE any edit/send dispatch');
});

ok('progress payloads edit the sticky message via Baileys edit key', () => {
  assert.ok(src.includes('{ text: payload.text, edit: existing.key }'),
    'sticky-edit call missing — Baileys supports WhatsApp\'s native message-edit '
    + 'feature via sendMessage({..., edit: key}); without this call every '
    + 'progress payload would send a brand-new message instead of editing it');
});

ok('sticky delete uses the Baileys delete key (revoke)', () => {
  assert.ok(src.includes('{ delete: existing.key }') || src.includes('{ delete: prog.key }'));
});

ok('real reply marks the turn finalized via sticky.markFinalized', () => {
  assert.ok(src.includes('sticky.markFinalized(payload.msg_id)'));
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
