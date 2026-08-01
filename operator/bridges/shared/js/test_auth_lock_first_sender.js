#!/usr/bin/env node
// 2026-08-02 regression: an empty whitelist on Telegram/Slack/Teams meant
// EVERY sender was owner, forever, with no lock — the same gap Discord and
// WhatsApp each fixed with a bespoke per-bridge mechanism (AutoOwnershipBridge,
// a custom authOk()). This is the shared equivalent: makeAuth({
// lockFirstSender: true }) persists the FIRST sender into `whitelist` and
// denies everyone else from then on, converging through the existing
// whitelist check rather than a second state machine.
//
// Run: node operator/bridges/shared/js/test_auth_lock_first_sender.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const { makeAuth } = require('./auth');

let pass = 0, fail = 0;
function t(label, ok, detail = '') {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ' — ' + detail : ''}`);
  if (ok) pass++; else fail++;
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'auth-lock-first-'));

function mkAuth(settings, opts = {}) {
  const SETTINGS = path.join(TMP, `s-${Math.random().toString(36).slice(2)}.json`);
  fs.writeFileSync(SETTINGS, JSON.stringify(settings, null, 2));
  const auth = makeAuth({
    settingsFile: SETTINGS,
    currentSettings: () => JSON.parse(fs.readFileSync(SETTINGS, 'utf-8')),
    loadSettings: () => JSON.parse(fs.readFileSync(SETTINGS, 'utf-8')),
    logger: () => {},
    channel: opts.channel || 'telegram',
    lockFirstSender: opts.lockFirstSender,
    denyOnEmptyWhitelist: opts.denyOnEmptyWhitelist,
  });
  return { auth, settingsFile: SETTINGS };
}

// 1. Empty whitelist, lockFirstSender: true — first sender is admitted AND
//    persisted into whitelist.
console.log('\n[empty whitelist + lockFirstSender → first sender locked in]');
{
  const { auth, settingsFile } = mkAuth({ whitelist: [] }, { lockFirstSender: true });
  t('first sender admitted', auth.authOk('user1', 'hi', 'user1') === true);
  const saved = JSON.parse(fs.readFileSync(settingsFile, 'utf-8'));
  t('whitelist now contains the first sender', (saved.whitelist || []).includes('user1'),
    JSON.stringify(saved.whitelist));
}

// 2. Same scenario — a SECOND, different sender arriving after the lock must
//    be denied (the exact bug: previously every sender was admitted forever).
console.log('\n[after first-sender lock → a later different sender is denied]');
{
  const { auth } = mkAuth({ whitelist: [] }, { lockFirstSender: true });
  t('first sender admitted', auth.authOk('user1', 'hi', 'user1') === true);
  t('second, different sender DENIED (the bug this fixes)',
    auth.authOk('user2-attacker', 'hi', 'user2-attacker') === false);
  // Re-fetch a fresh auth instance pointed at the SAME (now-locked) settings
  // file, simulating a daemon restart — the lock must survive, not just live
  // in an in-memory flag that resets.
}

// 3. Persistence survives a fresh makeAuth() call against the same settings
//    file (simulates a daemon restart) — this is why we write to disk
//    instead of an in-memory-only counter.
console.log('\n[lock persists across a simulated daemon restart]');
{
  const SETTINGS = path.join(TMP, `s-restart-${Math.random().toString(36).slice(2)}.json`);
  fs.writeFileSync(SETTINGS, JSON.stringify({ whitelist: [] }, null, 2));
  const cs = () => JSON.parse(fs.readFileSync(SETTINGS, 'utf-8'));
  const auth1 = makeAuth({
    settingsFile: SETTINGS, currentSettings: cs, loadSettings: cs, logger: () => {},
    channel: 'slack', lockFirstSender: true,
  });
  t('first boot: first sender admitted', auth1.authOk('owner1', 'hi', 'owner1') === true);
  // Fresh makeAuth() call = simulated restart, reading the same file.
  const auth2 = makeAuth({
    settingsFile: SETTINGS, currentSettings: cs, loadSettings: cs, logger: () => {},
    channel: 'slack', lockFirstSender: true,
  });
  t('after restart: original owner still admitted', auth2.authOk('owner1', 'hi', 'owner1') === true);
  t('after restart: a new stranger still denied', auth2.authOk('stranger', 'hi', 'stranger') === false);
}

// 4. A populated whitelist is unaffected by the flag — lockFirstSender only
//    matters while the whitelist is empty.
console.log('\n[populated whitelist unaffected by lockFirstSender]');
{
  const { auth } = mkAuth({ whitelist: ['already-owner'] }, { lockFirstSender: true });
  t('listed owner still accepted', auth.authOk('already-owner', 'hi', 'already-owner') === true);
  t('unlisted sender still denied (no re-claim of a non-empty whitelist)',
    auth.authOk('newcomer', 'hi', 'newcomer') === false);
}

// 5. denyOnEmptyWhitelist takes precedence — a channel can't be BOTH
//    fail-closed AND lock-to-first-sender (fail-closed already means no
//    claim is possible at all).
console.log('\n[denyOnEmptyWhitelist takes precedence over lockFirstSender]');
{
  const { auth } = mkAuth({ whitelist: [] }, { lockFirstSender: true, denyOnEmptyWhitelist: true });
  t('fail-closed wins: sender denied, no silent claim', auth.authOk('user1', 'hi', 'user1') === false);
}

console.log(`\n${pass + fail} total, ${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
