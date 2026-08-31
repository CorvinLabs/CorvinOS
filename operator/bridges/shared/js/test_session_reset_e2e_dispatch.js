#!/usr/bin/env node
'use strict';
/**
 * E2E: typing "/new" in a bridge chat really resets the Claude session.
 *
 * This drives the REAL transport boundary the bridges use — the shared
 * in-chat dispatcher (`in_chat_commands.dispatch`), which spawns
 * `session_reset.py` as a subprocess exactly as daemon.js does. Nothing is
 * stubbed: if the wiring between the slash command and the on-disk state
 * breaks, this test fails.
 *
 * What it pins down (Discord channel 1501315335750684803, 2026-08-28):
 * `/new` used to report "voice state cleared: no" and leave
 * `.main_session.json` in place, so the next turn spawned
 * `claude --resume <old id>` and the conversation continued as if nothing
 * had happened. The assertions below are written against the two probes the
 * adapter itself applies on the next turn.
 *
 * Run: node operator/bridges/shared/js/test_session_reset_e2e_dispatch.js
 */
const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const SHARED = path.resolve(__dirname, '..');

let PASS = 0;
let FAIL = 0;

function ok(msg) { PASS++; console.log(`PASS: ${msg}`); }
function bad(msg) { FAIL++; console.log(`FAIL: ${msg}`); }
function eq(actual, expected, msg) {
  if (actual === expected) ok(msg);
  else bad(`${msg} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

// ── sandbox ────────────────────────────────────────────────────────────────

const home = fs.mkdtempSync(path.join(os.tmpdir(), 'corvin-reset-e2e-'));
process.env.CORVIN_HOME = home;
delete process.env.CORVIN_TENANT_ID;

// in_chat_commands resolves paths at require() time, so set env first.
const inChat = require('./in_chat_commands');

/** Resolve the session dir exactly as adapter._session_dir() does. */
function adapterSessionDir(channel, chatId) {
  const code = [
    'import sys',
    `sys.path.insert(0, ${JSON.stringify(SHARED)})`,
    'import paths',
    "safe = ''.join(c if c.isalnum() else '_' for c in sys.argv[2])[:64] or 'anon'",
    'print(paths.voice_session_dir(sys.argv[1], safe))',
  ].join('\n');
  return execFileSync('python3', ['-c', code, channel, chatId], {
    encoding: 'utf8', env: { ...process.env, CORVIN_HOME: home },
  }).trim();
}

function seedSession(dir) {
  fs.mkdirSync(dir, { recursive: true });
  // Claude conversation state — the next turn resumes from these.
  fs.writeFileSync(path.join(dir, '.main_session.json'), JSON.stringify({
    session_id: '1e53620a-335b-4a5c-a0fa-c1d08c9d3d82',
    saved_at: '2026-08-28T06:55:48Z',
  }));
  fs.writeFileSync(path.join(dir, '.session_started'), '');
  fs.writeFileSync(path.join(dir, '.claude.json'), '{"sessionId":"abc"}');
  fs.mkdirSync(path.join(dir, '.claude'), { recursive: true });
  fs.writeFileSync(path.join(dir, '.claude', 'history.jsonl'), '{"role":"user"}\n');
  // Project files — must survive.
  fs.writeFileSync(path.join(dir, 'notes.md'), '# notes\n');
}

/** The adapter's own next-turn probes: has_session + the --resume read. */
function adapterWouldResume(dir) {
  if (!fs.existsSync(dir)) return false;
  const entries = fs.readdirSync(dir);
  if (entries.some(e => e.startsWith('.claude'))) return true;
  return entries.includes('.session_started') || entries.includes('.main_session.json');
}

// ── case 1: /new through the real dispatcher ───────────────────────────────

console.log('\n=== case 1: "/new" via the real in-chat dispatcher ===');
const settingsFile = path.join(home, 'settings.json');
fs.writeFileSync(settingsFile, '{}');

const chat = '1501315335750684803';
const dir = adapterSessionDir('discord', chat);
seedSession(dir);
eq(adapterWouldResume(dir), true, 'seeded: adapter would resume the old session');

const res = inChat.dispatch({
  text: '/new',
  channel: 'discord',
  chatKey: chat,
  isOwner: true,
  settingsFile,
});

assert.ok(res, '/new must be handled by the dispatcher');
eq(res.kind, 'reset', 'dispatcher routed /new to the session reset');
ok(`reply:\n${String(res.reply).split('\n').map(l => '    ' + l).join('\n')}`);

eq(adapterWouldResume(dir), false,
   'after /new the adapter starts a FRESH session (no --resume, no --continue)');
eq(fs.existsSync(path.join(dir, '.main_session.json')), false,
   '.main_session.json removed');
eq(fs.existsSync(path.join(dir, '.claude')), false, '.claude/ removed');
eq(/voice state cleared: yes/.test(String(res.reply)), true,
   'reply reports the state as actually cleared');
eq(fs.existsSync(path.join(dir, 'notes.md')), true,
   'project file kept, as the reply promises');

// ── case 2: /reset and /clear are the same operation ───────────────────────

console.log('\n=== case 2: /reset and /clear behave identically ===');
for (const cmd of ['/reset', '/clear']) {
  const c = `chat-${cmd.slice(1)}`;
  const d = adapterSessionDir('discord', c);
  seedSession(d);
  const r = inChat.dispatch({
    text: cmd, channel: 'discord', chatKey: c, isOwner: true, settingsFile,
  });
  eq(r && r.kind, 'reset', `${cmd} routes to the session reset`);
  eq(adapterWouldResume(d), false, `${cmd} clears the resumable state`);
  eq(fs.existsSync(path.join(d, 'notes.md')), true, `${cmd} keeps project files`);
}

// ── case 3: idempotent — a second /new is a clean no-op ────────────────────

console.log('\n=== case 3: a second /new is a clean no-op ===');
const again = inChat.dispatch({
  text: '/new', channel: 'discord', chatKey: chat, isOwner: true, settingsFile,
});
eq(again && again.kind, 'reset', 'second /new still succeeds');
eq(/session reset failed|malformed JSON/.test(String(again.reply)), false,
   'second /new reports no error');

// ── summary ────────────────────────────────────────────────────────────────

fs.rmSync(home, { recursive: true, force: true });
console.log(`\n${'='.repeat(60)}\nPASS: ${PASS}  FAIL: ${FAIL}`);
process.exit(FAIL ? 1 : 0);
