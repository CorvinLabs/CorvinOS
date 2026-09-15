#!/usr/bin/env node
// test_consent_dispatcher.js — Layer 17 read-only-side dispatcher.
//
// Covers `dispatchReadOnlyConsent` from in_chat_commands.js, which is the
// hook every daemon's read-only branch calls before maybeForwardAsObserver.
//
// The dispatcher spawns python3 consent.py for /consent on/off/<ttl>/status,
// so the test needs a real CORVIN_HOME sandbox so the CLI can read/write
// the per-chat store. The /share branch is purely JS-side (no spawn).
//
// Run: node operator/bridges/shared/js/test_consent_dispatcher.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

// Re-import the module fresh so any prior test's require cache cannot
// leak the wrong CONSENT_CLI path (delete cache key before require).
delete require.cache[require.resolve('./in_chat_commands')];
const inChat = require('./in_chat_commands');
const { dispatchReadOnlyConsent } = inChat;

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'consent-disp-'));
const SETTINGS = path.join(TMP, 'settings.json');
process.env.CORVIN_HOME = path.join(TMP, 'corvin');

let pass = 0, fail = 0;
function ok(cond, label, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}` + (extra ? ` — ${extra}` : ''));
  if (cond) pass++; else fail++;
}

function writeSettings(obj) {
  fs.writeFileSync(SETTINGS, JSON.stringify(obj, null, 2));
}

const CTX = (text, extra = {}) => ({
  text,
  channel: 'telegram',
  chatKey: 'chat-1',
  uid: 'mit-leser',
  settingsFile: SETTINGS,
  ...extra,
});

// ── 1: Non-/consent /share text returns null ─────────────────────────────
console.log('\n[non-consent text → null]');
writeSettings({});
ok(dispatchReadOnlyConsent(CTX('hello there')) === null, 'plain text → null');
ok(dispatchReadOnlyConsent(CTX('/help')) === null, '/help → null');
ok(dispatchReadOnlyConsent(CTX('')) === null, 'empty → null');
ok(dispatchReadOnlyConsent(null) === null, 'null ctx → null');

// ── 2: /share without transcript flag → hint, no admit ────────────────────
console.log('\n[/share without observer_visibility=transcript]');
writeSettings({});
{
  const r = dispatchReadOnlyConsent(CTX('/share hi'));
  ok(r && !r.admitShare, '/share blocked', JSON.stringify(r));
  ok(r && /observer-transcript/i.test(r.reply || ''), 'hint mentions transcript flag');
}

// ── 3: /share WITH transcript flag → admit + sharePayload ─────────────────
console.log('\n[/share WITH transcript on]');
writeSettings({
  chat_profiles: { 'chat-1': { observer_visibility: 'transcript' } },
});
{
  const r = dispatchReadOnlyConsent(CTX('/share single line please'));
  ok(r && r.admitShare === true, 'admitShare=true');
  ok(r && r.sharePayload === 'single line please', 'payload extracted');
  ok(r && r.kind === 'consent-share-admit', 'kind=consent-share-admit');
}
{
  // bare /share → usage hint, no admit
  const r = dispatchReadOnlyConsent(CTX('/share'));
  ok(r && !r.admitShare, 'bare /share → no admit');
  ok(r && /Usage/i.test(r.reply || ''), 'bare /share gives usage');
}
{
  // case-insensitive
  const r = dispatchReadOnlyConsent(CTX('/SHARE  multi  line'));
  ok(r && r.admitShare === true, 'uppercase /SHARE accepted');
  ok(r && r.sharePayload === 'multi  line', 'whitespace preserved in payload');
}

// ── 4: /consent help ──────────────────────────────────────────────────────
console.log('\n[/consent help]');
{
  const r = dispatchReadOnlyConsent(CTX('/consent'));
  ok(r && r.kind === 'consent-help', 'bare /consent → help', JSON.stringify(r && r.kind));
  ok(r && /durable consent/i.test(r.reply || ''), 'help mentions durable');
}

// ── 5: /consent on (durable) round-trip ───────────────────────────────────
console.log('\n[/consent on durable + status round-trip]');
writeSettings({});
{
  const r = dispatchReadOnlyConsent(CTX('/consent on'));
  ok(r && r.kind === 'consent-on', '/consent on succeeds', JSON.stringify(r));
  ok(r && /durable/i.test(r.reply || ''), 'reply mentions durable');
}
{
  const r = dispatchReadOnlyConsent(CTX('/consent status'));
  ok(r && r.kind === 'consent-status', '/consent status reads back');
  ok(r && /durable/i.test(r.reply || ''), 'status shows durable');
}
{
  const r = dispatchReadOnlyConsent(CTX('/consent off'));
  ok(r && r.kind === 'consent-off', '/consent off succeeds');
  ok(r && /revoked/i.test(r.reply || ''), 'reply mentions revoked');
}
{
  const r = dispatchReadOnlyConsent(CTX('/consent status'));
  ok(r && /not granted/i.test(r.reply || ''), 'status after off → not granted');
}

// ── 6: /consent <duration> ────────────────────────────────────────────────
console.log('\n[/consent <duration>]');
{
  const r = dispatchReadOnlyConsent(CTX('/consent 1h'));
  ok(r && r.kind === 'consent-on', '/consent 1h succeeds');
  ok(r && /1h/.test(r.reply || ''), 'reply mentions 1h');
}
{
  const r = dispatchReadOnlyConsent(CTX('/consent garbage'));
  ok(r && r.kind === 'consent-error', 'garbage duration → error');
  ok(r && /invalid/i.test(r.reply || ''), 'reply names "invalid"');
}

// ── 7: missing uid → friendly error ───────────────────────────────────────
console.log('\n[missing uid]');
{
  const ctx = { text: '/consent on', channel: 'telegram', chatKey: 'chat-1',
                uid: '', settingsFile: SETTINGS };
  const r = dispatchReadOnlyConsent(ctx);
  ok(r && /cannot identify/i.test(r.reply || ''), 'no-uid → friendly error');
}

console.log(`\n${pass} passed, ${fail} failed`);
fs.rmSync(TMP, { recursive: true, force: true });
delete process.env.CORVIN_HOME;
process.exit(fail === 0 ? 0 : 1);
