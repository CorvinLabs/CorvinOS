#!/usr/bin/env node
// test_disclosure_dispatcher.js — Layer 19 disclosure + /join /pass.
//
// Covers dispatchReadOnlyDisclosure, owner-side /join /pass redirects via
// dispatch(), and the rendering helpers disclosureCardText /
// disclosureHasSeen / disclosureMarkSeen. Backed by python3 disclosure.py
// which uses CORVIN_HOME for state and the channel's settings.json for
// the intrinsic-owner whitelist.
//
// Run: node operator/bridges/shared/js/test_disclosure_dispatcher.js

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'disc-disp-'));
process.env.CORVIN_HOME = path.join(TMP, 'corvin');

const CHANNEL = '_test_l19';
const CHAT = 'c-l19';
const OWNER = 'owner-19';
const STRANGER = 'stranger-19';

const BRIDGES_DIR = path.resolve(__dirname, '..', '..', CHANNEL);
const SETTINGS = path.join(BRIDGES_DIR, 'settings.json');
fs.mkdirSync(BRIDGES_DIR, { recursive: true });
fs.writeFileSync(SETTINGS, JSON.stringify({ whitelist: [OWNER] }));
process.on('exit', () => {
  try { fs.rmSync(BRIDGES_DIR, { recursive: true, force: true }); } catch {}
});

delete require.cache[require.resolve('./in_chat_commands')];
const inChat = require('./in_chat_commands');
const {
  dispatch,
  dispatchReadOnlyDisclosure,
  disclosureCardText,
  disclosureHasSeen,
  disclosureMarkSeen,
} = inChat;

let pass = 0, fail = 0;
function ok(cond, label, extra) {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}` + (extra ? ` — ${extra}` : ''));
  if (cond) pass++; else fail++;
}

const CTX = (text, uid, isOwner) => ({
  text, channel: CHANNEL, chatKey: CHAT, uid, isOwner,
  settingsFile: SETTINGS,
});

// ── 1: owner-side /join /pass via dispatch return redirect text ──────────
console.log('\n[owner-side /join /pass]');
{
  const r1 = dispatch(CTX('/join', OWNER, true));
  ok(typeof r1.reply === 'string' && /owner|intrinsic/i.test(r1.reply),
     'owner /join returns redirect msg', `(${r1.reply.slice(0, 80)})`);
  const r2 = dispatch(CTX('/pass', OWNER, true));
  ok(typeof r2.reply === 'string' && /owner|acknowledge/i.test(r2.reply),
     'owner /pass returns redirect msg', `(${r2.reply.slice(0, 80)})`);
}

// ── 2: dispatchReadOnlyDisclosure rejects non-/join /pass text ──────────
console.log('\n[non-disclosure text → null]');
ok(dispatchReadOnlyDisclosure(CTX('hello', STRANGER, false)) === null,
   'plain text → null');
ok(dispatchReadOnlyDisclosure(CTX('/help', STRANGER, false)) === null,
   '/help → null');
ok(dispatchReadOnlyDisclosure(null) === null, 'null ctx → null');
ok(dispatchReadOnlyDisclosure(CTX('', STRANGER, false)) === null,
   'empty → null');

// ── 3: missing-uid daemon-bug hint ───────────────────────────────────────
console.log('\n[missing uid hint]');
{
  const r = dispatchReadOnlyDisclosure(CTX('/join', '', false));
  ok(/uid|daemon/i.test(r.reply), '/join without uid returns daemon-bug hint',
     `(${r.reply.slice(0, 80)})`);
}

// ── 4: stranger /join → registers as observer ────────────────────────────
console.log('\n[stranger /join]');
{
  const r = dispatchReadOnlyDisclosure(CTX('/join', STRANGER, false));
  ok(r && /observer|registered|✓/i.test(r.reply),
     'stranger /join returns observer-registered ack',
     `(${r.reply.slice(0, 80)})`);
  ok(r.kind === 'disclosure-joined', 'kind=disclosure-joined');
}

// ── 5: idempotent /join → already-observer ───────────────────────────────
console.log('\n[idempotent /join]');
{
  const r = dispatchReadOnlyDisclosure(CTX('/join', STRANGER, false));
  ok(/already|observer/i.test(r.reply),
     'second /join returns already-observer', `(${r.reply.slice(0, 80)})`);
}

// ── 6: /join as owner-via-readonly dispatcher → owner-already ───────────
console.log('\n[owner via readonly path]');
{
  const r = dispatchReadOnlyDisclosure(CTX('/join', OWNER, false));
  ok(/owner/i.test(r.reply),
     'owner /join via readonly dispatcher returns owner-already',
     `(${r.reply.slice(0, 80)})`);
}

// ── 7: stranger /pass → ack-without-grant ────────────────────────────────
console.log('\n[stranger /pass]');
{
  const fresh = 'fresh-19';
  const r = dispatchReadOnlyDisclosure(CTX('/pass', fresh, false));
  ok(r && /✓|acknowledged|pass/i.test(r.reply),
     'stranger /pass returns ack', `(${r.reply.slice(0, 80)})`);
  ok(r.kind === 'disclosure-passed', 'kind=disclosure-passed');
}

// ── 8: /pass as owner via readonly path → owner-already ──────────────────
console.log('\n[owner /pass via readonly]')
{
  const r = dispatchReadOnlyDisclosure(CTX('/pass', OWNER, false));
  ok(/owner/i.test(r.reply),
     'owner /pass via readonly returns owner-already',
     `(${r.reply.slice(0, 80)})`);
}

// ── 9: disclosureCardText DE + EN render ─────────────────────────────────
console.log('\n[card text DE + EN]');
{
  const de = disclosureCardText({ channel: CHANNEL, ownerLabel: 'Silvio',
                                  hasObserverTranscript: false, lang: 'de' });
  ok(de.length > 50 && de.includes('Silvio'),
     'DE card includes owner label', `(len=${de.length})`);
  const en = disclosureCardText({ channel: CHANNEL, ownerLabel: 'Silvio',
                                  hasObserverTranscript: false, lang: 'en' });
  ok(en.length > 50 && en.includes('Silvio'),
     'EN card includes owner label', `(len=${en.length})`);
  ok(de !== en, 'DE and EN differ');
}

// ── 10: disclosureHasSeen / disclosureMarkSeen round-trip ────────────────
console.log('\n[has_seen / mark_seen round-trip]');
{
  const fresh2 = 'fresh-19b';
  ok(disclosureHasSeen({ channel: CHANNEL, chatKey: CHAT, uid: fresh2 })
       === false, 'unseen returns false');
  ok((disclosureMarkSeen({ channel: CHANNEL, chatKey: CHAT, uid: fresh2,
                          action: 'pending' }) || {}).ok === true, 'mark_seen succeeds');
  ok(disclosureHasSeen({ channel: CHANNEL, chatKey: CHAT, uid: fresh2 })
       === true, 'after mark, has_seen returns true');
}

// ── 11: owner is implicit-seen ──────────────────────────────────────────
console.log('\n[owner implicit-seen]');
ok(disclosureHasSeen({ channel: CHANNEL, chatKey: CHAT, uid: OWNER }) === true,
   'owner has_seen always true');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
