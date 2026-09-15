#!/usr/bin/env node
// PENTEST-3c regression: an EMPTY whitelist must not fail-open to "owner" on a
// fail-closed (public) channel like email. makeAuth({ denyOnEmptyWhitelist })
// denies everyone on an empty whitelist EXCEPT a valid `/auth <pin>` claim, and
// EXCEPT when the operator explicitly sets `dev_mode: true`.
//
// It also verifies the legacy channels (no denyOnEmptyWhitelist) keep their
// empty-whitelist=owner first-run/claim behaviour, so discord/telegram/slack
// are not regressed.
//
// Run: node operator/bridges/shared/js/test_auth_empty_whitelist_deny.js

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

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'auth-empty-wl-'));

function mkAuth(settings, opts = {}) {
  const SETTINGS = path.join(TMP, `s-${Math.random().toString(36).slice(2)}.json`);
  fs.writeFileSync(SETTINGS, JSON.stringify(settings, null, 2));
  return makeAuth({
    settingsFile: SETTINGS,
    currentSettings: () => JSON.parse(fs.readFileSync(SETTINGS, 'utf-8')),
    loadSettings: () => JSON.parse(fs.readFileSync(SETTINGS, 'utf-8')),
    logger: () => {},
    channel: opts.channel || 'email',
    denyOnEmptyWhitelist: opts.denyOnEmptyWhitelist,
  });
}

// 1. Fail-closed channel, empty whitelist, no pin → DENY everyone.
console.log('\n[email + empty whitelist + no pin → deny]');
{
  const a = mkAuth({ whitelist: [] }, { denyOnEmptyWhitelist: true });
  t('authOk denies stranger', a.authOk('stranger@evil.tld', 'hi', 'stranger@evil.tld') === false);
  t('classify = unknown (not owner)', a.classify('stranger@evil.tld', 'stranger@evil.tld') === 'unknown');
}

// 2. Fail-closed channel, empty whitelist, PIN set → owner can still CLAIM.
console.log('\n[email + empty whitelist + pin → /auth claim still works]');
{
  const a = mkAuth({ whitelist: [], pin: 'sekret' }, { denyOnEmptyWhitelist: true });
  t('random /auth wrong pin denied', a.authOk('owner@corp.tld', '/auth nope', 'owner@corp.tld') === false);
  t('valid /auth pin admits (claim)', a.authOk('owner@corp.tld', '/auth sekret', 'owner@corp.tld') === true);
  t('non-command stranger still denied', a.authOk('stranger@evil.tld', 'hello', 'stranger@evil.tld') === false);
}

// 3. Fail-closed channel, empty whitelist, dev_mode:true → legacy open (escape).
console.log('\n[email + empty whitelist + dev_mode → open (explicit opt-in)]');
{
  const a = mkAuth({ whitelist: [], dev_mode: true }, { denyOnEmptyWhitelist: true });
  t('authOk accepts (dev_mode)', a.authOk('anyone@x.tld', 'hi', 'anyone@x.tld') === true);
  t('classify = owner (dev_mode)', a.classify('anyone@x.tld', 'anyone@x.tld') === 'owner');
}

// 4. Legacy channel (no denyOnEmptyWhitelist) → empty whitelist still = owner.
//    This is the discord/telegram/slack first-run/owner-claim path; must NOT
//    regress.
console.log('\n[legacy channel + empty whitelist → first-run owner (unchanged)]');
{
  const a = mkAuth({ whitelist: [] }, { channel: 'discord' /* no denyOnEmptyWhitelist */ });
  t('authOk accepts first-run', a.authOk('first-user', 'hi', 'chat-1') === true);
  t('classify = owner first-run', a.classify('first-user', 'chat-1') === 'owner');
}

// 5. Populated whitelist behaves identically with or without the flag.
console.log('\n[populated whitelist unaffected by the flag]');
{
  const a = mkAuth({ whitelist: ['owner@corp.tld'] }, { denyOnEmptyWhitelist: true });
  t('listed owner accepted', a.authOk('owner@corp.tld', 'hi', 'owner@corp.tld') === true);
  t('unlisted denied', a.authOk('other@corp.tld', 'hi', 'other@corp.tld') === false);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
