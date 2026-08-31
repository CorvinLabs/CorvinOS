#!/usr/bin/env node
// Layer 16, Phase 2 (Read-Only-Rolle) E2E for makeAuth().
//
// We don't run a real daemon — we instantiate makeAuth() with a stub
// settings reader that contains a whitelist + read_only list, and
// trigger every classification path:
//
//   1. whitelist sender                  → classify=owner,    isReadOnly=false
//   2. read_only sender (1st time)       → isReadOnly=true,   firstDrop=true,
//                                          one bridge.read_only_drop event
//   3. read_only sender (2nd time, same chat) → firstDrop=false (silent ACK),
//                                          one MORE event
//   4. read_only sender on a different chat   → firstDrop=true again,
//                                          (per (chat, user) tracking)
//   5. unknown sender                    → classify=unknown,  isReadOnly=false,
//                                          authOk path remains the gate
//   6. collision (whitelist ∩ read_only) → classify=owner,    isReadOnly=false
//                                          (whitelist beats read_only)
//   7. audience='all' on a chat profile  → classify=owner,    isReadOnly=false
//                                          (chat-open trumps read_only)
//   8. chain integrity: voice-audit verify rc=0 over the events written
//
// Per-subtask E2E rule: hits the real on-disk audit chain, not a mock.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const { makeAuth } = require('./auth');

let pass = 0, fail = 0;
function t(label, ok, detail = '') {
  const mark = ok ? 'PASS' : 'FAIL';
  console.log(`  ${mark}  ${label}${detail ? ' — ' + detail : ''}`);
  if (ok) pass++; else fail++;
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'auth-readonly-'));
const AUDIT = path.join(TMP, 'audit.jsonl');
const SETTINGS = path.join(TMP, 'settings.json');
process.env.VOICE_AUDIT_PATH = AUDIT;
process.env.VOICE_AUDIT_STRICT = '1';

fs.writeFileSync(SETTINGS, JSON.stringify({
  whitelist: ['owner-uid', 'collision-uid'],
  read_only: ['observer-uid', 'collision-uid'],
  chat_profiles: { 'open-chat': { audience: 'all' } },
}, null, 2));

let _set = JSON.parse(fs.readFileSync(SETTINGS, 'utf-8'));
const auth = makeAuth({
  settingsFile: SETTINGS,
  currentSettings: () => _set,
  loadSettings: () => JSON.parse(fs.readFileSync(SETTINGS, 'utf-8')),
  logger: () => {},
  channel: 'discord',
});

function readEvents() {
  if (!fs.existsSync(AUDIT)) return [];
  return fs.readFileSync(AUDIT, 'utf-8')
    .split('\n').filter((l) => l.trim()).map(JSON.parse);
}
function readOnlyDropEvents() {
  return readEvents().filter(
    (e) => e.event_type === 'bridge.read_only_drop'
  );
}

// 1. whitelist sender
console.log('\n[whitelist sender → owner]');
t('classify(owner-uid) = owner',
  auth.classify('owner-uid', 'chat-1') === 'owner');
const r1 = auth.readOnlyOk('owner-uid', 'hi', 'chat-1');
t('readOnlyOk false for owner', r1.isReadOnly === false);
t('no audit event yet', readOnlyDropEvents().length === 0);

// 2. read_only sender, first time on chat-1
console.log('\n[read_only sender, first drop on chat-1]');
t('classify(observer-uid) = read_only',
  auth.classify('observer-uid', 'chat-1') === 'read_only');
const r2 = auth.readOnlyOk('observer-uid', 'rm -rf /', 'chat-1');
t('isReadOnly = true', r2.isReadOnly === true);
t('firstDrop = true', r2.firstDrop === true);
let evs = readOnlyDropEvents();
t('one read_only_drop event', evs.length === 1);
t('event has user=observer-uid',
  (evs[0]?.details || {}).user === 'observer-uid');
t('event has chat_key=chat-1',
  (evs[0]?.details || {}).chat_key === 'chat-1');
t('event details.first_drop=true',
  (evs[0]?.details || {}).first_drop === true);
t('event details.snippet captures attempt',
  (evs[0]?.details || {}).snippet === 'rm -rf /');

// 3. same sender again on chat-1 → firstDrop=false but still audited
console.log('\n[read_only sender, second drop on same chat]');
const r3 = auth.readOnlyOk('observer-uid', 'still trying', 'chat-1');
t('isReadOnly = true (still gated)', r3.isReadOnly === true);
t('firstDrop = false (already acked)', r3.firstDrop === false);
evs = readOnlyDropEvents();
t('two read_only_drop events now', evs.length === 2);
t('second event details.first_drop=false',
  (evs[1]?.details || {}).first_drop === false);

// 4. same sender on different chat → firstDrop resets
console.log('\n[read_only sender on a different chat]');
const r4 = auth.readOnlyOk('observer-uid', 'over here', 'chat-2');
t('firstDrop = true again on chat-2', r4.firstDrop === true);
evs = readOnlyDropEvents();
t('three read_only_drop events', evs.length === 3);

// 5. unknown sender stays a regular whitelist deny via authOk
console.log('\n[unknown sender stays a whitelist_deny]');
t('classify(stranger) = unknown',
  auth.classify('stranger-uid', 'chat-1') === 'unknown');
const r5 = auth.readOnlyOk('stranger-uid', 'hi', 'chat-1');
t('isReadOnly = false for stranger', r5.isReadOnly === false);
const ok5 = auth.authOk('stranger-uid', 'hi', 'chat-1');
t('authOk denies stranger', ok5 === false);
const totalEvs = readEvents();
t('whitelist_deny event written',
  totalEvs.some((e) => e.event_type === 'bridge.whitelist_deny'));

// 6. collision: in whitelist AND read_only → whitelist wins
console.log('\n[collision: whitelist beats read_only]');
t('classify(collision-uid) = owner',
  auth.classify('collision-uid', 'chat-1') === 'owner');
const r6 = auth.readOnlyOk('collision-uid', 'hi', 'chat-1');
t('readOnlyOk false on collision (owner wins)',
  r6.isReadOnly === false);
const dropsAfterCollision = readOnlyDropEvents().length;
t('no extra drop event for collision-uid',
  dropsAfterCollision === 3);

// 7. audience='all' on a chat profile → read_only is bypassed in that chat
console.log('\n[audience=all on a chat profile bypasses read_only]');
t('classify(observer-uid) on open-chat = owner',
  auth.classify('observer-uid', 'open-chat') === 'owner');
const r7 = auth.readOnlyOk('observer-uid', 'hi from open chat', 'open-chat');
t('readOnlyOk false in audience=all chat',
  r7.isReadOnly === false);
t('no extra drop event for open-chat',
  readOnlyDropEvents().length === 3);

// 8. chain integrity
console.log('\n[chain stays valid across all events]');
const verifyCli = path.resolve(__dirname, '..', '..', '..', 'voice',
  'scripts', 'voice_audit.py');
const v = spawnSync('python3',
  [verifyCli, '--path', AUDIT, 'verify'],
  { encoding: 'utf-8' });
t('verify rc=0', v.status === 0,
  `stderr=${(v.stderr || '').trim()}`);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
