#!/usr/bin/env node
// test_observer_visibility.js — Layer 16, Phase 2 (visibility split).
//
// Asserts the lookup contract for `chat_profiles[<id>].observer_visibility`:
//   - missing → "off" (default — backward compat with read_only-only setups)
//   - explicit "off" → "off"
//   - explicit "transcript" → "transcript"
//   - invalid value → "off" (fail-open: a typo never silently flips a chat
//     into transcript mode without the operator knowing)
//   - default-profile fallback works
//   - per-chat override beats default profile
//
// Run: node operator/bridges/shared/js/test_observer_visibility.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const { getObserverVisibility } = require('./in_chat_commands');

let pass = 0, fail = 0;
function eq(actual, expected, label) {
  const ok = actual === expected;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label} — got ${JSON.stringify(actual)}`);
  if (ok) pass++; else fail++;
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'observer-vis-'));
const SETTINGS = path.join(TMP, 'settings.json');

function write(obj) { fs.writeFileSync(SETTINGS, JSON.stringify(obj, null, 2)); }

console.log('\n[no chat_profiles → default off]');
write({ whitelist: ['x'] });
eq(getObserverVisibility(SETTINGS, 'chat-1'), 'off', 'unset → off');

console.log('\n[explicit off]');
write({ chat_profiles: { 'chat-1': { observer_visibility: 'off' } } });
eq(getObserverVisibility(SETTINGS, 'chat-1'), 'off', 'explicit off');

console.log('\n[explicit transcript]');
write({ chat_profiles: { 'chat-1': { observer_visibility: 'transcript' } } });
eq(getObserverVisibility(SETTINGS, 'chat-1'), 'transcript', 'explicit transcript');

console.log('\n[invalid value → off (fail-open)]');
write({ chat_profiles: { 'chat-1': { observer_visibility: 'shouting' } } });
eq(getObserverVisibility(SETTINGS, 'chat-1'), 'off', 'invalid → off');

console.log('\n[default profile fallback]');
write({
  chat_profiles: {
    'default': { observer_visibility: 'transcript' },
    'chat-1': { persona: 'coder' }, // no observer_visibility on this chat
  },
});
eq(getObserverVisibility(SETTINGS, 'chat-1'), 'transcript',
  'unset chat picks up default-profile transcript');

console.log('\n[per-chat override beats default]');
write({
  chat_profiles: {
    'default': { observer_visibility: 'transcript' },
    'chat-quiet': { observer_visibility: 'off' },
  },
});
eq(getObserverVisibility(SETTINGS, 'chat-quiet'), 'off',
  'per-chat off beats default transcript');
eq(getObserverVisibility(SETTINGS, 'chat-other'), 'transcript',
  'unrelated chat still picks up default transcript');

console.log(`\n${pass} passed, ${fail} failed`);
fs.rmSync(TMP, { recursive: true, force: true });
process.exit(fail === 0 ? 0 : 1);
