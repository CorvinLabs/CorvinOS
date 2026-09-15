#!/usr/bin/env node
// Phase H3 E2E: makeAuth() emits audit events on the security paths.
//
// We don't run a real daemon — we instantiate makeAuth with a stub
// settings reader and trigger each path:
//   - rejected uid (not in whitelist)         → bridge.whitelist_deny
//   - /auth <wrong-pin>                       → bridge.pin_failure
//   - /auth <right-pin>                       → bridge.login
//   - rate-limit hit                          → bridge.rate_limit_exceeded
//
// Each event is asserted in the on-disk audit log via VOICE_AUDIT_PATH.
// The Python `voice-audit verify` is run at the end to confirm the
// chain stays valid across the four hand-written events.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync, spawnSync } = require('child_process');

const { makeAuth } = require('./auth');

let pass = 0, fail = 0;
function t(label, ok, detail = '') {
  const mark = ok ? 'PASS' : 'FAIL';
  console.log(`  ${mark}  ${label}${detail ? ' — ' + detail : ''}`);
  if (ok) pass++; else fail++;
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'auth-audit-'));
const AUDIT = path.join(TMP, 'audit.jsonl');
const SETTINGS = path.join(TMP, 'settings.json');
process.env.VOICE_AUDIT_PATH = AUDIT;
process.env.VOICE_AUDIT_STRICT = '1';

// stub settings file
fs.writeFileSync(SETTINGS, JSON.stringify({
  whitelist: ['allowed-user'],
  pin: 'sekret-pin',
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

// 1. whitelist deny
console.log('\n[whitelist deny → bridge.whitelist_deny]');
const allow1 = auth.authOk('hostile-id', 'hello', 'chat-1');
t('rejected', allow1 === false);
// Because the audit emit is sync, the file is on disk by now.
let events = readEvents();
t('one event recorded',     events.length === 1);
t('event_type matches',
  events[0]?.event_type === 'bridge.whitelist_deny');
t('details.user = hostile-id',
  (events[0]?.details || {}).user === 'hostile-id');
t('details.channel = discord',
  (events[0]?.details || {}).channel === 'discord');

// 2. /auth wrong-pin → bridge.pin_failure (still rejects)
console.log('\n[/auth wrong-pin → bridge.pin_failure]');
const allow2 = auth.authOk('attacker-1', '/auth wrong', 'chat-1');
t('rejected', allow2 === false);
events = readEvents();
t('two events now', events.length === 2);
t('second is bridge.pin_failure',
  events[1]?.event_type === 'bridge.pin_failure');

// 3. /auth right-pin → bridge.login (and added to whitelist)
console.log('\n[/auth right-pin → bridge.login + whitelist update]');
const allow3 = auth.authOk('new-user', '/auth sekret-pin', 'chat-1');
t('admitted', allow3 === true);
events = readEvents();
t('three events now', events.length === 3);
t('third is bridge.login',
  events[2]?.event_type === 'bridge.login');
const updated = JSON.parse(fs.readFileSync(SETTINGS, 'utf-8'));
t('new-user added to whitelist',
  (updated.whitelist || []).includes('new-user'));

// 4. rate-limit hit
console.log('\n[rate-limit exceeded → bridge.rate_limit_exceeded]');
auth.rateAllow('uid', 2);
auth.rateAllow('uid', 2);
const r3 = auth.rateAllow('uid', 2);
t('third call rejected', r3 === false);
events = readEvents();
t('four events now', events.length === 4);
t('fourth is bridge.rate_limit_exceeded',
  events[3]?.event_type === 'bridge.rate_limit_exceeded');

// 5. chain integrity: voice-audit verify rc=0
console.log('\n[chain stays valid across the four events]');
const verifyCli = path.resolve(__dirname, '..', '..', '..', 'voice',
  'scripts', 'voice_audit.py');
const v = spawnSync('python3',
  [verifyCli, '--path', AUDIT, 'verify'],
  { encoding: 'utf-8' });
t('verify rc=0', v.status === 0,
  `stderr=${(v.stderr || '').trim()}`);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
