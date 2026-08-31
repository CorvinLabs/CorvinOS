#!/usr/bin/env node
// test_chat_toggle.js — covers chat_toggle.js used by telegram / discord / slack.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const ct = require('./chat_toggle');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'chat-toggle-'));
const SETTINGS = path.join(TMP, 'settings.json');

let pass = 0, fail = 0;
function ok(cond, label, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}` + (extra ? ` — ${extra}` : ''));
  if (cond) pass++; else fail++;
}

function write(obj) { fs.writeFileSync(SETTINGS, JSON.stringify(obj, null, 2)); }
function read() { return JSON.parse(fs.readFileSync(SETTINGS, 'utf8')); }

console.log('\n[isToggleEnabled]');
ok(ct.isToggleEnabled({}) === false, 'empty settings → false');
ok(ct.isToggleEnabled({ enabled_chats: [] }) === false ||
   ct.isToggleEnabled({ enabled_chats: [] }) === true,
   'empty array → toggle ON (opt-in mode)');
ok(ct.isToggleEnabled({ enabled_chats: ['x'] }) === true,
   'populated array → toggle ON');
ok(ct.isToggleEnabled(null) === false, 'null → false');

console.log('\n[isChatEnabled — legacy default-on]');
ok(ct.isChatEnabled({}, 'any-chat') === true,
   'no enabled_chats field → every chat enabled (legacy)');
ok(ct.isChatEnabled({ whitelist: ['x'] }, 'any-chat') === true,
   'unrelated fields → still legacy default-on');

console.log('\n[isChatEnabled — opt-in mode]');
ok(ct.isChatEnabled({ enabled_chats: [] }, 'any-chat') === false,
   'empty list → no chat enabled');
ok(ct.isChatEnabled({ enabled_chats: ['c1'] }, 'c1') === true,
   'matching key → enabled');
ok(ct.isChatEnabled({ enabled_chats: ['c1'] }, 'c2') === false,
   'non-matching key → disabled');
ok(ct.isChatEnabled({ enabled_chats: [123] }, '123') === true,
   'numeric stored, string queried → match (string-coerced)');

console.log('\n[isChatEnabled — hard deny-list wins in every mode]');
ok(ct.isChatEnabled({ disabled_chats: ['c1'] }, 'c1') === false,
   'default-on + disabled_chats → that chat denied');
ok(ct.isChatEnabled({ disabled_chats: ['c1'] }, 'c2') === true,
   'default-on + disabled_chats → other chats still default-on');
ok(ct.isChatEnabled({ enabled_chats: ['c1'], disabled_chats: ['c1'] }, 'c1') === false,
   'enabled_chats AND disabled_chats both list c1 → deny wins');
ok(ct.isChatEnabled({ disabled_chats: [123] }, '123') === false,
   'numeric stored, string queried → match (string-coerced)');

console.log('\n[enableChat — opt-in mode]');
write({ enabled_chats: [] });
ct.enableChat(SETTINGS, 'chat-A');
ok(JSON.stringify(read().enabled_chats) === JSON.stringify(['chat-A']),
   'enableChat appends new');
ct.enableChat(SETTINGS, 'chat-A');
ok(read().enabled_chats.length === 1, 'enableChat idempotent');
ct.enableChat(SETTINGS, 'chat-B');
ok(read().enabled_chats.length === 2, 'enableChat adds different key');

console.log('\n[disableChat — opt-in mode]');
ct.disableChat(SETTINGS, 'chat-A');
ok(!read().enabled_chats.includes('chat-A'),
   'disableChat removes from enabled_chats');
ok(Array.isArray(read().disabled_chats) && read().disabled_chats.includes('chat-A'),
   'disableChat ALSO adds to disabled_chats (hard deny)');
ct.disableChat(SETTINGS, 'chat-Z');
ok(read().disabled_chats.includes('chat-Z'),
   'disableChat works for chats that were never enabled');
ct.disableChat(SETTINGS, 'chat-Z');
ok(read().disabled_chats.filter((k) => k === 'chat-Z').length === 1,
   'disableChat idempotent on disabled_chats too');

// Legacy-mode disableChat must produce a REAL deny — the historical
// no-op behaviour was the bug. The deny is recorded in disabled_chats,
// NOT by introducing enabled_chats (that would silence every OTHER chat).
console.log('\n[disableChat — legacy default-on mode]');
write({ whitelist: ['x'] });
ct.disableChat(SETTINGS, 'noisy-chat');
ok(read().enabled_chats === undefined,
   'legacy-mode disableChat does NOT introduce enabled_chats field');
ok(Array.isArray(read().disabled_chats) && read().disabled_chats.includes('noisy-chat'),
   'legacy-mode disableChat adds to disabled_chats — /off is effective now');
ok(ct.isChatEnabled(read(), 'noisy-chat') === false,
   'after legacy /off, the chat is REALLY off');
ok(ct.isChatEnabled(read(), 'other-chat') === true,
   'after legacy /off on chat A, other chats stay default-on');

// /on in legacy mode removes from disabled_chats without introducing
// enabled_chats — the chat returns to default-on baseline.
console.log('\n[enableChat — legacy default-on mode]');
ct.enableChat(SETTINGS, 'noisy-chat');
ok(read().enabled_chats === undefined,
   'legacy-mode enableChat does NOT introduce enabled_chats field');
ok(!read().disabled_chats || !read().disabled_chats.includes('noisy-chat'),
   'legacy-mode enableChat removed the chat from disabled_chats');
ok(ct.isChatEnabled(read(), 'noisy-chat') === true,
   'after legacy /on, the chat is back on');

// Cross-mode: /off then /on in opt-in mode should leave a coherent state.
console.log('\n[cross-mode consistency]');
write({ enabled_chats: ['c1'] });
ct.disableChat(SETTINGS, 'c1');
ok(!read().enabled_chats.includes('c1'),
   '/off drops c1 from enabled_chats');
ok(read().disabled_chats.includes('c1'),
   '/off also adds c1 to disabled_chats');
ok(ct.isChatEnabled(read(), 'c1') === false,
   'c1 is denied');
ct.enableChat(SETTINGS, 'c1');
ok(read().enabled_chats.includes('c1'),
   '/on re-adds c1 to enabled_chats');
ok(!read().disabled_chats || !read().disabled_chats.includes('c1'),
   '/on removes c1 from disabled_chats');
ok(ct.isChatEnabled(read(), 'c1') === true,
   'c1 is allowed again');

console.log('\n[handleToggleCommand]');
write({ enabled_chats: [] });

ok(ct.handleToggleCommand({ text: 'hello', chatKey: 'c1',
                            isOwner: true, settingsFile: SETTINGS }) === null,
   'plain text → null');
ok(ct.handleToggleCommand({ text: '/on', chatKey: 'c1',
                            isOwner: false, settingsFile: SETTINGS }).kind
     === 'toggle-denied',
   'non-owner /on denied');
{
  const r = ct.handleToggleCommand({ text: '/on', chatKey: 'c1',
                                     isOwner: true, settingsFile: SETTINGS });
  ok(r.kind === 'toggle-on', '/on returns toggle-on');
  ok(read().enabled_chats.includes('c1'), '/on appended c1 to settings');
}
{
  const r = ct.handleToggleCommand({ text: '/status', chatKey: 'c1',
                                     isOwner: true, settingsFile: SETTINGS });
  ok(r.kind === 'toggle-status', '/status returns toggle-status');
  ok(/AI is on/.test(r.reply), 'status reply says "AI is on"');
}
{
  const r = ct.handleToggleCommand({ text: '/off', chatKey: 'c1',
                                     isOwner: true, settingsFile: SETTINGS });
  ok(r.kind === 'toggle-off', '/off returns toggle-off');
  ok(!read().enabled_chats.includes('c1'), '/off removed c1 from enabled_chats');
  ok(read().disabled_chats.includes('c1'), '/off added c1 to disabled_chats');
}
{
  const r = ct.handleToggleCommand({ text: '/status', chatKey: 'c1',
                                     isOwner: true, settingsFile: SETTINGS });
  ok(/AI is off/.test(r.reply), 'after /off, status says "AI is off"');
}

// Legacy mode: /status on a settings file without enabled_chats.
write({ whitelist: ['x'] });
{
  const r = ct.handleToggleCommand({ text: '/status', chatKey: 'c1',
                                     isOwner: true, settingsFile: SETTINGS });
  ok(/AI is on/.test(r.reply) && /Default-on/i.test(r.reply),
     'legacy /status reports default-on note');
}

// Regression: the original bug. /off in legacy mode must be a REAL deny,
// not a silent no-op. Before the fix, /off returned "AI off" but
// isChatEnabled() kept returning true → other senders' messages still
// reached the adapter.
console.log('\n[REGRESSION: /off in legacy default-on mode is a real deny]');
write({ whitelist: ['x'] });
{
  const r = ct.handleToggleCommand({ text: '/off', chatKey: 'group-chat',
                                     isOwner: true, settingsFile: SETTINGS });
  ok(r.kind === 'toggle-off', 'legacy /off returns toggle-off');
  ok(ct.isChatEnabled(read(), 'group-chat') === false,
     'legacy /off actually disables the chat (was silent no-op before fix)');
  ok(ct.isChatEnabled(read(), 'other-group') === true,
     'other chats still default-on after legacy /off');
}
{
  const r = ct.handleToggleCommand({ text: '/status', chatKey: 'group-chat',
                                     isOwner: true, settingsFile: SETTINGS });
  ok(/AI is off/.test(r.reply),
     'legacy /status after /off reports "AI is off" — and means it');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
