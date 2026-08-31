// Per-subtask E2E for the /lang slash-command dispatcher.
//
// Covers the JS round-trip via the real `lang_cli.py` Python backend in
// a sandboxed XDG_CONFIG_HOME, so the test never touches the user's
// real ~/.config/corvin-voice/ directory. Asserts that:
//
//   * `/lang` shows the unset state in English by default
//   * `/lang zh-Hans` flips display_language and reports back IN the
//     newly-set language (Simplified Chinese reply text)
//   * `/lang japanese` folds the alias to `ja`
//   * `/lang xyzzy` returns a friendly unknown-code error
//   * `/lang clear` removes the entry
//   * `/lang list` enumerates the registered codes
//   * `/lang` after `/lang de` reports the German current line
//
// The test deliberately does NOT mock the CLI — it runs the actual
// child-process so a regression in lang_cli.py surfaces here too.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');

// Sandbox the user's profile directory.
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'lang-dispatch-'));
process.env.XDG_CONFIG_HOME = TMP;

const inChatCmds = require('./in_chat_commands.js');

function ctx(extra) {
  return Object.assign({
    channel: 'discord', chatKey: 'lang-test', uid: 'u-1', isOwner: true,
  }, extra || {});
}

let pass = 0, fail = 0;
function t(name, fn) {
  try { fn(); console.log('  ok  ', name); pass++; }
  catch (e) { console.log('  FAIL', name, '\n       ', e.message); fail++; }
}

console.log('Test: /lang dispatcher (sandbox=' + TMP + ')');

t('show unset → English current_unset line', () => {
  const r = inChatCmds.dispatch({ text: '/lang', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-show-unset');
  assert.match(r.reply, /No language preference/);
  assert.match(r.reply, /English/);
});

t('set zh-Hans → reply rendered in Simplified Chinese', () => {
  const r = inChatCmds.dispatch({ text: '/lang zh-Hans', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-set');
  // The acknowledgement comes back IN the newly-set language. We
  // shipped no zh-Hans bundle yet, so the fallback is the EN string —
  // but it MUST mention Simplified Chinese as the resolved name.
  assert.match(r.reply, /Simplified Chinese/);
});

t('show after set → current line + zh-Hans code', () => {
  const r = inChatCmds.dispatch({ text: '/lang', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-show');
  assert.match(r.reply, /zh-Hans/);
  assert.match(r.reply, /Simplified Chinese/);
});

t('set japanese alias → folded to ja', () => {
  const r = inChatCmds.dispatch({ text: '/lang japanese', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-set');
  assert.match(r.reply, /Japanese/);
  // After this the show line must report ja
  const r2 = inChatCmds.dispatch({ text: '/lang', ctx: ctx() });
  assert.match(r2.reply, /\bja\b/);
});

t('set xyzzy → unknown error, keeps prior state', () => {
  const r = inChatCmds.dispatch({ text: '/lang xyzzy', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-unknown');
  assert.match(r.reply, /Unknown language code|Unbekannter Sprachcode/);
  assert.match(r.reply, /xyzzy/);
  // Prior state must survive (we set ja above)
  const r2 = inChatCmds.dispatch({ text: '/lang', ctx: ctx() });
  assert.match(r2.reply, /Japanese/);
});

t('clear → removes entry, show goes back to unset', () => {
  const r = inChatCmds.dispatch({ text: '/lang clear', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-clear');
  const r2 = inChatCmds.dispatch({ text: '/lang', ctx: ctx() });
  assert.strictEqual(r2.kind, 'lang-show-unset');
});

t('list → enumerates known codes', () => {
  const r = inChatCmds.dispatch({ text: '/lang list', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-list');
  for (const code of ['de', 'en', 'zh-Hans', 'ja', 'ar', 'fr', 'es']) {
    assert.match(r.reply, new RegExp(`\\b${code.replace('-', '-')}\\b`),
                 `${code} missing from list`);
  }
});

t('set de → reply rendered in German', () => {
  const r = inChatCmds.dispatch({ text: '/lang de', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-set');
  // We DO have a de bundle, so this should be the German confirmation.
  assert.match(r.reply, /Sprache auf/);
});

t('show after set de → German current line', () => {
  const r = inChatCmds.dispatch({ text: '/lang', ctx: ctx() });
  assert.match(r.reply, /Aktuelle Sprache/);
});

t('alias German also works', () => {
  inChatCmds.dispatch({ text: '/lang clear', ctx: ctx() });
  const r = inChatCmds.dispatch({ text: '/lang German', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-set');
  assert.match(r.reply, /Sprache auf/);
});

t('case insensitivity — clear via uppercase', () => {
  const r = inChatCmds.dispatch({ text: '/lang CLEAR', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-clear');
});

t('reset is alias for clear', () => {
  inChatCmds.dispatch({ text: '/lang ja', ctx: ctx() });
  const r = inChatCmds.dispatch({ text: '/lang reset', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-clear');
  const r2 = inChatCmds.dispatch({ text: '/lang', ctx: ctx() });
  assert.strictEqual(r2.kind, 'lang-show-unset');
});

t('off is alias for clear', () => {
  inChatCmds.dispatch({ text: '/lang ja', ctx: ctx() });
  const r = inChatCmds.dispatch({ text: '/lang off', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-clear');
});

t('help / ? → usage line', () => {
  const r = inChatCmds.dispatch({ text: '/lang help', ctx: ctx() });
  assert.strictEqual(r.kind, 'lang-help');
  assert.match(r.reply, /Usage|Benutzung/);
});

console.log(`\n${pass} pass, ${fail} fail`);
process.exit(fail ? 1 : 0);
