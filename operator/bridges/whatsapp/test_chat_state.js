#!/usr/bin/env node
// test_chat_state.js — covers the WhatsApp /off LID-mismatch bug.
//
// Historical bug: enabled_chats stored ONE form (e.g. "<id>@lid"), the next
// /off command arrived under the OTHER form ("<phone>@s.whatsapp.net" of
// the same chat), the filter compared the two literally, found no match,
// the entry stayed, and the bot kept reacting after the user had said
// /off. Every test here exercises that exact path against the new helpers
// in chat_state.js.

const assert = require('assert');
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

console.log('== chat_state ==');

ok('normalizeJid: strips device suffix on s.whatsapp.net', () => {
  assert.strictEqual(cs.normalizeJid('491729809432:11@s.whatsapp.net'),
                     '491729809432@s.whatsapp.net');
});

ok('normalizeJid: leaves bare phone JID alone', () => {
  assert.strictEqual(cs.normalizeJid('491729809432@s.whatsapp.net'),
                     '491729809432@s.whatsapp.net');
});

ok('normalizeJid: strips device suffix on lid', () => {
  assert.strictEqual(cs.normalizeJid('233118090961040:5@lid'),
                     '233118090961040@lid');
});

ok('normalizeJid: handles null / empty', () => {
  assert.strictEqual(cs.normalizeJid(null), null);
  assert.strictEqual(cs.normalizeJid(''), '');
  assert.strictEqual(cs.normalizeJid(undefined), undefined);
});

ok('chatJidsForMessage: returns both remoteJid and remoteJidAlt', () => {
  const jids = cs.chatJidsForMessage({
    key: { remoteJid: '491729@s.whatsapp.net', remoteJidAlt: '233118@lid' },
  });
  assert.deepStrictEqual(jids.sort(),
    ['233118@lid', '491729@s.whatsapp.net'].sort());
});

ok('chatJidsForMessage: legacy message without remoteJidAlt still works', () => {
  const jids = cs.chatJidsForMessage({ key: { remoteJid: '491729@s.whatsapp.net' } });
  assert.deepStrictEqual(jids, ['491729@s.whatsapp.net']);
});

ok('chatJidsForMessage: missing key returns empty array', () => {
  assert.deepStrictEqual(cs.chatJidsForMessage({}), []);
  assert.deepStrictEqual(cs.chatJidsForMessage(null), []);
});

ok('chatJidsForMessage: de-dupes when both forms are equal', () => {
  const jids = cs.chatJidsForMessage({
    key: { remoteJid: 'X@lid', remoteJidAlt: 'X@lid' },
  });
  assert.deepStrictEqual(jids, ['X@lid']);
});

ok('chatJidsForMessage: normalises device-suffix forms', () => {
  const jids = cs.chatJidsForMessage({
    key: { remoteJid: '491729:11@s.whatsapp.net', remoteJidAlt: '233118:5@lid' },
  });
  assert.deepStrictEqual(jids.sort(),
    ['233118@lid', '491729@s.whatsapp.net'].sort());
});

ok('isAnyChatEnabled: false on empty list', () => {
  assert.strictEqual(cs.isAnyChatEnabled([], { enabled_chats: ['X@lid'] }), false);
  assert.strictEqual(cs.isAnyChatEnabled(['X@lid'], { enabled_chats: [] }), false);
  assert.strictEqual(cs.isAnyChatEnabled(['X@lid'], {}), false);
});

ok('isAnyChatEnabled: matches when any alias is in enabled_chats', () => {
  const settings = { enabled_chats: ['233118@lid'] };
  // Message comes in with phone-form first, lid-form alt:
  const jids = ['491729@s.whatsapp.net', '233118@lid'];
  assert.strictEqual(cs.isAnyChatEnabled(jids, settings), true);
});

ok('isAnyChatEnabled: matches across normalised device suffix', () => {
  const settings = { enabled_chats: ['491729@s.whatsapp.net'] };
  const jids = ['491729:11@s.whatsapp.net'];
  assert.strictEqual(cs.isAnyChatEnabled(jids, settings), true);
});

ok('isAnyChatEnabled: no match when neither alias listed', () => {
  const settings = { enabled_chats: ['SOMEONE_ELSE@lid'] };
  const jids = ['491729@s.whatsapp.net', '233118@lid'];
  assert.strictEqual(cs.isAnyChatEnabled(jids, settings), false);
});

ok('enableChats: adds all jids', () => {
  const settings = { enabled_chats: [] };
  cs.enableChats(['233118@lid', '491729@s.whatsapp.net'], settings);
  assert.deepStrictEqual(settings.enabled_chats.sort(),
    ['233118@lid', '491729@s.whatsapp.net'].sort());
});

ok('enableChats: idempotent — second call adds nothing', () => {
  const settings = { enabled_chats: ['233118@lid'] };
  cs.enableChats(['233118@lid'], settings);
  assert.strictEqual(settings.enabled_chats.length, 1);
});

ok('enableChats: handles missing array', () => {
  const settings = {};
  cs.enableChats(['X@lid'], settings);
  assert.deepStrictEqual(settings.enabled_chats, ['X@lid']);
});

ok('enableChats: drops null / empty entries', () => {
  const settings = { enabled_chats: [] };
  cs.enableChats([null, undefined, '', 'X@lid'], settings);
  assert.deepStrictEqual(settings.enabled_chats, ['X@lid']);
});

ok('disableChats: removes the matching entry', () => {
  const settings = { enabled_chats: ['233118@lid', 'OTHER@lid'] };
  cs.disableChats(['233118@lid'], settings);
  assert.deepStrictEqual(settings.enabled_chats, ['OTHER@lid']);
});

ok('disableChats: idempotent — already absent', () => {
  const settings = { enabled_chats: ['OTHER@lid'] };
  cs.disableChats(['233118@lid'], settings);
  assert.deepStrictEqual(settings.enabled_chats, ['OTHER@lid']);
});

// THE LOAD-BEARING BUG TEST — this is the user-reported regression. The
// settings stores one form, /off arrives under the other, both must be
// removed via the alias array.
ok('disableChats: removes both alias forms when message has both', () => {
  const settings = { enabled_chats: ['233118@lid'] };
  // /off command arrives as a fresh message; Baileys gives BOTH forms:
  const messageJids = cs.chatJidsForMessage({
    key: {
      remoteJid: '491729@s.whatsapp.net',  // phone-form leads this time
      remoteJidAlt: '233118@lid',
    },
  });
  cs.disableChats(messageJids, settings);
  assert.deepStrictEqual(settings.enabled_chats, []);
});

// Same bug, the OTHER way around: settings stored phone, /off arrives lid-first.
ok('disableChats: removes phone-form when /off arrives as lid', () => {
  const settings = { enabled_chats: ['491729@s.whatsapp.net'] };
  const messageJids = cs.chatJidsForMessage({
    key: { remoteJid: '233118@lid', remoteJidAlt: '491729@s.whatsapp.net' },
  });
  cs.disableChats(messageJids, settings);
  assert.deepStrictEqual(settings.enabled_chats, []);
});

// /on registers BOTH forms AND records them as aliases so a later /off
// under EITHER form drops the entry under the sibling form too. This is
// the load-bearing fix for the silent-stay-on bug where a chat enabled
// under both JID forms remained partially enabled after a single-form /off.
ok('roundtrip: /on registers both forms, /off (under either) clears both', () => {
  const settings = { enabled_chats: [] };
  const onMsg = {
    key: { remoteJid: '491729@s.whatsapp.net', remoteJidAlt: '233118@lid' },
  };
  cs.enableChats(cs.chatJidsForMessage(onMsg), settings);
  assert.strictEqual(settings.enabled_chats.length, 2);
  assert.ok(settings.chat_aliases &&
            settings.chat_aliases['491729@s.whatsapp.net'],
            'enableChats records alias map when both forms present');

  // Other person writes — comes in with EITHER form. Both lookups succeed.
  const otherMsg1 = { key: { remoteJid: '491729@s.whatsapp.net' } };
  assert.strictEqual(
    cs.isAnyChatEnabled(cs.chatJidsForMessage(otherMsg1), settings), true);
  const otherMsg2 = { key: { remoteJid: '233118@lid' } };
  assert.strictEqual(
    cs.isAnyChatEnabled(cs.chatJidsForMessage(otherMsg2), settings), true);

  // /off arrives under ONLY the lid form (no remoteJidAlt this time).
  // The fixed behaviour: alias-map expands the disable-set, so BOTH
  // entries are removed in this single call.
  const offMsg = { key: { remoteJid: '233118@lid' } };
  cs.disableChats(cs.chatJidsForMessage(offMsg), settings);
  assert.deepStrictEqual(settings.enabled_chats, [],
    'partial-form /off must symmetrically clear sibling form via aliases');
});

// User-reported regression scenario, end-to-end:
ok('regression: user /off in chat actually disables — bot drops other-person msgs', () => {
  const settings = { enabled_chats: ['233118090961040@lid'] };

  // /off command from owner; Baileys fills both forms:
  const offMsg = {
    key: {
      remoteJid: '491729809432@s.whatsapp.net',
      remoteJidAlt: '233118090961040@lid',
      fromMe: true,
    },
  };
  cs.disableChats(cs.chatJidsForMessage(offMsg), settings);
  assert.deepStrictEqual(settings.enabled_chats, [],
    'enabled_chats must be empty after /off');

  // The other person now writes under the lid form (the one that USED to be
  // in enabled_chats). The gate must say: not enabled.
  const otherMsg = { key: { remoteJid: '233118090961040@lid' } };
  assert.strictEqual(
    cs.isAnyChatEnabled(cs.chatJidsForMessage(otherMsg), settings), false,
    'after /off the other-person message must be dropped');
});

// Reported "/off doesn't actually disable" — the alias-map fix.
// Pre-fix: chat enabled under both forms via /on, then /off arrived under
// only one form (Baileys had not paired the alt this session) → only that
// form got removed, the sibling form stayed in enabled_chats, and the bot
// kept replying. With the alias map the partial /off now wipes both.
ok('regression: /off under one form clears the alias-paired sibling form', () => {
  const settings = { enabled_chats: [] };
  // /on came in with both forms paired.
  cs.enableChats(['12345@s.whatsapp.net', 'abc@lid'], settings);
  assert.strictEqual(settings.enabled_chats.length, 2);
  assert.deepStrictEqual(
    new Set(settings.chat_aliases['12345@s.whatsapp.net']),
    new Set(['abc@lid']));

  // /off comes in with ONLY the lid form (no alt this time).
  cs.disableChats(['abc@lid'], settings);
  assert.deepStrictEqual(settings.enabled_chats, [],
    'alias-paired phone form must vanish too');

  // Alias map pruned — no dead pointers left behind.
  assert.deepStrictEqual(settings.chat_aliases, {});
});

// ── disabled_chats hard-deny tests ────────────────────────────────────────────

ok('disabled_chats: blocks even when jid is in enabled_chats', () => {
  const settings = {
    enabled_chats: ['233118@lid'],
    disabled_chats: ['233118@lid'],
  };
  assert.strictEqual(cs.isAnyChatEnabled(['233118@lid'], settings), false,
    'disabled_chats must override enabled_chats');
});

ok('disabled_chats: /off with phone form blocks lid-only subsequent message via deny-list', () => {
  // Real-world scenario: settings had both forms, /off arrived with phone form only.
  // Old code: phone form removed from enabled_chats, lid form stays → bot keeps responding.
  // New code: phone form added to disabled_chats → subsequent lid-only message
  //           is checked: lid not in disabled_chats, so disabled_chats alone doesn't help,
  //           BUT if the next message carries the phone form as remoteJidAlt it IS caught.
  // This test covers the case where the next message has BOTH forms (normal Baileys behavior).
  const settings = {
    enabled_chats: ['233118@lid', '491729@s.whatsapp.net'],
  };
  // /off arrives with phone form only (no remoteJidAlt this session).
  cs.disableChats(['491729@s.whatsapp.net'], settings);
  assert.ok(
    Array.isArray(settings.disabled_chats) && settings.disabled_chats.includes('491729@s.whatsapp.net'),
    'phone form must be in disabled_chats after /off');

  // Next incoming message has BOTH forms (typical Baileys behavior).
  const nextJids = ['233118@lid', '491729@s.whatsapp.net'];
  assert.strictEqual(cs.isAnyChatEnabled(nextJids, settings), false,
    'message with both forms must be blocked because phone form is in disabled_chats');
});

ok('disabled_chats: /off with both forms adds both to deny-list', () => {
  const settings = { enabled_chats: ['233118@lid', '491729@s.whatsapp.net'] };
  cs.disableChats(['233118@lid', '491729@s.whatsapp.net'], settings);
  const denied = new Set(settings.disabled_chats);
  assert.ok(denied.has('233118@lid'), 'lid form must be in disabled_chats');
  assert.ok(denied.has('491729@s.whatsapp.net'), 'phone form must be in disabled_chats');
  assert.deepStrictEqual(settings.enabled_chats, []);
});

ok('disabled_chats: /on removes from deny-list', () => {
  const settings = {
    enabled_chats: [],
    disabled_chats: ['233118@lid', '491729@s.whatsapp.net'],
  };
  cs.enableChats(['233118@lid', '491729@s.whatsapp.net'], settings);
  assert.strictEqual((settings.disabled_chats || []).length, 0,
    'disabled_chats must be cleared after /on');
  assert.ok(settings.enabled_chats.includes('233118@lid'));
});

ok('disabled_chats: expanded via aliases — partial /off blocks all forms', () => {
  const settings = { enabled_chats: [] };
  // /on with both forms creates alias map.
  cs.enableChats(['12345@s.whatsapp.net', 'abc@lid'], settings);
  // /off with only phone form.
  cs.disableChats(['12345@s.whatsapp.net'], settings);
  // expanded alias means lid is ALSO in disabled_chats.
  const denied = new Set((settings.disabled_chats || []).map(cs.normalizeJid));
  assert.ok(denied.has('abc@lid'),
    'alias-expanded /off must add lid form to disabled_chats');
  // Any future message under either form is blocked.
  assert.strictEqual(cs.isAnyChatEnabled(['abc@lid'], settings), false);
  assert.strictEqual(cs.isAnyChatEnabled(['12345@s.whatsapp.net'], settings), false);
});

// ── maybeRegisterAliases tests ─────────────────────────────────────────────────

ok('maybeRegisterAliases: registers mutual aliases, returns true on new link', () => {
  const settings = {};
  const changed = cs.maybeRegisterAliases(['491729@s.whatsapp.net', '233118@lid'], settings);
  assert.strictEqual(changed, true);
  assert.deepStrictEqual(
    new Set(settings.chat_aliases['491729@s.whatsapp.net']),
    new Set(['233118@lid']));
  assert.deepStrictEqual(
    new Set(settings.chat_aliases['233118@lid']),
    new Set(['491729@s.whatsapp.net']));
});

ok('maybeRegisterAliases: returns false when no new link needed', () => {
  const settings = {
    chat_aliases: {
      '491729@s.whatsapp.net': ['233118@lid'],
      '233118@lid': ['491729@s.whatsapp.net'],
    },
  };
  const changed = cs.maybeRegisterAliases(['491729@s.whatsapp.net', '233118@lid'], settings);
  assert.strictEqual(changed, false, 'no-op when aliases already registered');
});

ok('maybeRegisterAliases: single-form message is a no-op', () => {
  const settings = {};
  const changed = cs.maybeRegisterAliases(['491729@s.whatsapp.net'], settings);
  assert.strictEqual(changed, false);
  assert.ok(!settings.chat_aliases, 'chat_aliases must not be created for single-form');
});

ok('maybeRegisterAliases: enables disableChats to remove sibling form retroactively', () => {
  // Pre-existing state: two forms in enabled_chats, no aliases (old daemon state).
  const settings = {
    enabled_chats: ['233118@lid', '491729@s.whatsapp.net'],
  };
  // Next incoming message carries both forms → aliases get registered.
  cs.maybeRegisterAliases(['491729@s.whatsapp.net', '233118@lid'], settings);
  // Now /off arrives with only phone form.
  cs.disableChats(['491729@s.whatsapp.net'], settings);
  // Both must be gone because the alias map now links them.
  assert.deepStrictEqual(settings.enabled_chats, [],
    'aliases registered by maybeRegisterAliases must allow /off to clear sibling form');
  // And both in deny list for belt-and-suspenders.
  const denied = new Set((settings.disabled_chats || []).map(cs.normalizeJid));
  assert.ok(denied.has('491729@s.whatsapp.net'));
  assert.ok(denied.has('233118@lid'));
});

// ── Re-pair outbound regression (2026-07-22) ────────────────────────────────
// After a re-pair WhatsApp issues a NEW @lid for the same chat. /on arrived
// under the phone form only (enabled_chats=[phone]), the content message then
// carried remoteJid=<new-lid> + remoteJidAlt=<phone>, so the inbound check
// passed via the phone alt and maybeRegisterAliases linked the two forms.
// The reply envelope's `to` is the raw remoteJid (<new-lid>) — the outbound
// gate calls isAnyChatEnabled([<new-lid>]) with a single form. Before the fix
// the enabled-side lookup did NOT expand aliases (only the deny side did), so
// every reply was dropped as "disabled chat" while inbound kept working.

ok('re-pair: reply to @lid form passes when phone form is enabled + aliased', () => {
  const settings = { enabled_chats: ['491729@s.whatsapp.net'] };
  // Content message carries both forms → daemon registers the alias.
  cs.maybeRegisterAliases(['77771111@lid', '491729@s.whatsapp.net'], settings);
  // Outbound gate sees only the raw reply JID (the new @lid form).
  assert.strictEqual(
    cs.isAnyChatEnabled(['77771111@lid'], settings), true,
    'reply to aliased @lid form must be treated as enabled');
});

ok('re-pair: reply to phone form passes when only @lid form is enabled + aliased', () => {
  const settings = { enabled_chats: ['77771111@lid'] };
  cs.maybeRegisterAliases(['491729@s.whatsapp.net', '77771111@lid'], settings);
  assert.strictEqual(
    cs.isAnyChatEnabled(['491729@s.whatsapp.net'], settings), true,
    'reverse direction must be symmetric');
});

ok('re-pair: alias expansion must NOT bypass the hard deny-list', () => {
  const settings = {
    enabled_chats: ['491729@s.whatsapp.net'],
    disabled_chats: ['77771111@lid'],
  };
  cs.maybeRegisterAliases(['77771111@lid', '491729@s.whatsapp.net'], settings);
  assert.strictEqual(
    cs.isAnyChatEnabled(['77771111@lid'], settings), false,
    'deny-list still wins over alias-expanded enabled match');
});

ok('unaliased unknown JID stays disabled (no false-open)', () => {
  const settings = { enabled_chats: ['491729@s.whatsapp.net'] };
  assert.strictEqual(cs.isAnyChatEnabled(['999@lid'], settings), false,
    'a JID with no alias link must not become enabled');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
