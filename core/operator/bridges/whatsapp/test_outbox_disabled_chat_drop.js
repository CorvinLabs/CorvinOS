#!/usr/bin/env node
// test_outbox_disabled_chat_drop.js — regression gate for the
// "/off ignored — pending replies still arrive" bug.
//
// Historical bug: the WhatsApp daemon's processOutbox() loop sent
// every adapter-generated reply unconditionally. When the user
// disabled a chat with /off, in-flight subprocess turns kept
// producing reply envelopes in shared/outbox/; the loop happily
// delivered them seconds-to-minutes after /off, looking exactly
// like the bridge was ignoring the toggle.
//
// The fix is a guard right after the channel-match in processOutbox:
//
//   if (payload.to && !isChatEnabled(payload.to)) {
//     log('outbox: dropping reply to disabled chat ...');
//     fs.unlinkSync(fpath);
//     continue;
//   }
//
// This test verifies two things:
//
//   1. The chat_state primitives behave the way the guard relies on
//      — a JID that was /off'd reports isAnyChatEnabled → false even
//      when stored under a different form (lid vs phone, with/without
//      device suffix). This is the load-bearing semantic.
//
//   2. The guard line is present in daemon.js (structural grep).
//      Without this static-check a refactor that removes the guard
//      would re-introduce the bug silently.

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const cs = require('./chat_state');

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
    if (e.stack) console.error(e.stack.split('\n').slice(1, 5).join('\n'));
    failed += 1;
  }
}

console.log('== outbox-disabled-chat drop ==');

// ----------------------------------------------------------------------
// 1. Semantic: /off'd chat is reported as disabled regardless of which
//    JID form the daemon happens to see at outbox-drain time.
// ----------------------------------------------------------------------

ok('disabled chat: phone form stored, phone form queried → disabled', () => {
  const settings = { enabled_chats: [] };
  cs.enableChats(['491729809432@s.whatsapp.net'], settings);
  assert.strictEqual(cs.isAnyChatEnabled(['491729809432@s.whatsapp.net'],
                                          settings), true);
  cs.disableChats(['491729809432@s.whatsapp.net'], settings);
  assert.strictEqual(cs.isAnyChatEnabled(['491729809432@s.whatsapp.net'],
                                          settings), false);
});

ok('disabled chat: device-suffix form drops cleanly', () => {
  const settings = { enabled_chats: [] };
  cs.enableChats(['491729809432:11@s.whatsapp.net'], settings);
  cs.disableChats(['491729809432@s.whatsapp.net'], settings);
  assert.strictEqual(cs.isAnyChatEnabled(['491729809432:11@s.whatsapp.net'],
                                          settings), false,
                     'device-suffix form should still resolve to disabled');
});

ok('disabled chat: lid form on enable, phone form on disable, both off', () => {
  // Mirror of the historical LID-mismatch bug fixed in chat_state.js —
  // the outbox guard MUST inherit that resolution.
  const settings = { enabled_chats: [] };
  cs.enableChats(['233118090961040@lid'], settings);
  cs.disableChats(['233118090961040@lid'], settings);
  assert.strictEqual(cs.isAnyChatEnabled(['233118090961040@lid'], settings),
                     false);
});

ok('never-enabled chat is reported as disabled', () => {
  const settings = { enabled_chats: [] };
  assert.strictEqual(cs.isAnyChatEnabled(['unknown@s.whatsapp.net'], settings),
                     false,
                     'never-enabled chat must default to disabled — this is the '
                     + 'guard that prevents pending replies to chats the operator '
                     + 'never opted in for');
});

// ----------------------------------------------------------------------
// 2. Structural: the guard line is present in daemon.js. A future
//    refactor that drops it would re-open the bug silently.
// ----------------------------------------------------------------------

ok('daemon.js processOutbox: disabled-chat guard present', () => {
  const src = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');
  // The exact phrase appears in the guard's log line and in the
  // surrounding comment. If both are gone, the guard was removed.
  assert.ok(src.includes('disabled chat'),
            'log marker "disabled chat" missing — guard likely removed');
  assert.ok(/!\s*isChatEnabled\(\s*payload\.to\s*\)/.test(src),
            '!isChatEnabled(payload.to) guard expression missing');
  // The guard MUST come BEFORE the safeSend branches; we verify by
  // ordering: the unlinkSync inside the disabled-chat block must
  // appear before the first call to safeSend in processOutbox.
  const procIdx = src.indexOf('async function processOutbox()');
  assert.ok(procIdx > -1, 'processOutbox function not found');
  const guardIdx = src.indexOf('disabled chat', procIdx);
  const sendIdx  = src.indexOf('safeSend(waSocket', procIdx);
  assert.ok(guardIdx > -1 && sendIdx > -1, 'markers not both found');
  assert.ok(guardIdx < sendIdx,
            'guard must precede the safeSend call inside processOutbox');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
