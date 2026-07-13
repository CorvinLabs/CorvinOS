#!/usr/bin/env node
// test_signal_daemon.js — integration/E2E tests for the Signal bridge.
//
// Tests the full message flow without a real signal-cli instance:
//   1. normPhone: E.164 normalisation
//   2. Auth gate: whitelisted / denied / rate-limited senders
//   3. Inbox write: correct JSON shape, channel='signal'
//   4. Envelope parsing: dataMessage / non-data (receipt) / group message
//   5. In-chat commands: /stop, /btw
//   6. Outbox → processOutboxPayload: sendSignal called correctly
//   7. Read-only gate: firstDrop ACK, subsequent silence

'use strict';

const assert = require('assert');
const fs     = require('fs');
const os     = require('os');
const path   = require('path');

// ── Temp dirs + settings ──────────────────────────────────────────────────────
const tmp         = fs.mkdtempSync(path.join(os.tmpdir(), 'signal-e2e-'));
const INBOX_DIR   = path.join(tmp, 'inbox');
const OUTBOX_DIR  = path.join(tmp, 'outbox');
const SETTINGS_FILE = path.join(tmp, 'settings.json');

fs.mkdirSync(INBOX_DIR,  { recursive: true });
fs.mkdirSync(OUTBOX_DIR, { recursive: true });

function writeSettings(obj) {
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(obj, null, 2));
}
function currentSettings() {
  return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8'));
}
function readInbox() {
  return fs.readdirSync(INBOX_DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(fs.readFileSync(path.join(INBOX_DIR, f), 'utf8')));
}
function clearInbox() {
  for (const f of fs.readdirSync(INBOX_DIR)) fs.unlinkSync(path.join(INBOX_DIR, f));
}

// ── Envelope builder helpers ──────────────────────────────────────────────────
function envelope(source, message, extra = {}) {
  return {
    envelope: {
      source,
      sourceDevice: 1,
      dataMessage: { message, timestamp: Date.now(), ...extra },
    },
  };
}
function receiptEnvelope(source) {
  return { envelope: { source, sourceDevice: 1, receiptMessage: { when: Date.now() } } };
}

// ── Load modules ──────────────────────────────────────────────────────────────
const { makeAuth }                     = require('../shared/js/auth');
const { makeHandler, normPhone }       = require('./handler');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    const r = fn();
    if (r && typeof r.then === 'function') {
      return r.then(() => { console.log(`  ✓ ${name}`); passed++; })
              .catch((e) => { console.error(`  ✗ ${name}\n    ${e.message}`); failed++; });
    }
    console.log(`  ✓ ${name}`);
    passed++;
    return Promise.resolve();
  } catch (e) {
    console.error(`  ✗ ${name}\n    ${e.message}`);
    failed++;
    return Promise.resolve();
  }
}

async function run() {

  // ── normPhone ──────────────────────────────────────────────────────────────
  console.log('\nnormPhone');

  await test('already E.164: no change', () => {
    assert.strictEqual(normPhone('+491234567890'), '+491234567890');
  });
  await test('adds leading +', () => {
    assert.strictEqual(normPhone('491234567890'), '+491234567890');
  });
  await test('strips spaces and dashes', () => {
    assert.strictEqual(normPhone('+49 123 456-7890'), '+491234567890');
  });
  await test('empty input: returns empty string', () => {
    assert.strictEqual(normPhone(''), '');
    assert.strictEqual(normPhone(null), '');
  });

  // ── Auth gate ──────────────────────────────────────────────────────────────
  console.log('\nAuth gate');

  await test('DEV mode (empty whitelist): message written to inbox', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const sent = [];
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async (r, m) => sent.push({ r, m }),
    });
    const id = await handleEnvelope(envelope('+491234567890', 'Hello Signal!'));
    assert.ok(id, 'must return message id');
    const [msg] = readInbox();
    assert.strictEqual(msg.channel, 'signal');
    assert.strictEqual(msg.from, '+491234567890');
    assert.strictEqual(msg.text, 'Hello Signal!');
    assert.strictEqual(msg.chat_id, '+491234567890');
  });

  await test('Whitelisted number: message accepted', async () => {
    writeSettings({ whitelist: ['+491234567890'], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    const id = await handleEnvelope(envelope('+491234567890', 'Hi'));
    assert.ok(id);
    assert.strictEqual(readInbox().length, 1);
  });

  await test('Unknown number: denied, no inbox write, auth reply sent', async () => {
    writeSettings({ whitelist: ['+4900000000'], rate_limit_per_hour: 60 });
    clearInbox();
    const sent = [];
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async (r, m) => sent.push({ r, m }),
    });
    const id = await handleEnvelope(envelope('+491234567890', 'Hi'));
    assert.strictEqual(id, null);
    assert.strictEqual(readInbox().length, 0);
    assert.ok(sent.length > 0, 'must send deny reply');
    assert.ok(sent[0].m.includes('+491234567890'), 'reply must include number');
  });

  await test('Rate-limited number: second message dropped', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 1 });
    clearInbox();
    const sent = [];
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async (r, m) => sent.push(m),
    });
    await handleEnvelope(envelope('+491234567890', 'first'));
    clearInbox();
    const id2 = await handleEnvelope(envelope('+491234567890', 'second'));
    assert.strictEqual(id2, null);
    assert.strictEqual(readInbox().length, 0);
    assert.ok(sent.some((m) => /rate/i.test(m)), 'must send rate-limit notice');
  });

  // ── Envelope parsing ────────────────────────────────────────────────────────
  console.log('\nEnvelope parsing');

  await test('Receipt envelope: returns null (no inbox write)', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    const id = await handleEnvelope(receiptEnvelope('+491234567890'));
    assert.strictEqual(id, null);
    assert.strictEqual(readInbox().length, 0);
  });

  await test('Flat envelope shape (no .envelope wrapper): still handled', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    // Some signal-cli versions return flat shape
    const id = await handleEnvelope({
      source: '+491234567890',
      dataMessage: { message: 'flat shape', timestamp: Date.now() },
    });
    assert.ok(id, 'flat shape must still produce inbox write');
  });

  await test('Group message: chat_id uses group prefix', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    await handleEnvelope(envelope('+491234567890', 'Group msg', {
      groupInfo: { groupId: 'abc123', type: 'DELIVER' },
    }));
    const [msg] = readInbox();
    assert.ok(msg.chat_id.startsWith('group:'), 'group chat_id must start with group:');
    assert.ok(msg.chat_id.includes('abc123'));
  });

  // ── In-chat commands ────────────────────────────────────────────────────────
  console.log('\nIn-chat commands');

  await test('/stop writes cancel envelope', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    await handleEnvelope(envelope('+491234567890', '/stop'));
    const [msg] = readInbox();
    assert.strictEqual(msg._cancel, true);
  });

  await test('/btw writes btw envelope with text', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    await handleEnvelope(envelope('+491234567890', '/btw consider this too'));
    const [msg] = readInbox();
    assert.strictEqual(msg._btw, true);
    assert.strictEqual(msg.text, 'consider this too');
  });

  await test('empty message: no inbox write', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    const id = await handleEnvelope(envelope('+491234567890', ''));
    assert.strictEqual(id, null);
    assert.strictEqual(readInbox().length, 0);
  });

  // ── Outbox → processOutboxPayload ─────────────────────────────────────────
  console.log('\nOutbox → processOutboxPayload');

  await test('plain text: sendSignal called with correct args', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    const calls = [];
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async (r, m) => calls.push({ r, m }),
    });
    const ok = await processOutboxPayload({ channel: 'signal', chat_id: '+491234567890', text: 'Hello back!' });
    assert.strictEqual(ok, true);
    assert.strictEqual(calls.length, 1);
    assert.strictEqual(calls[0].r, '+491234567890');
    assert.strictEqual(calls[0].m, 'Hello back!');
  });

  await test('missing chat_id: returns false', async () => {
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    const ok = await processOutboxPayload({ channel: 'signal', text: 'hi' });
    assert.strictEqual(ok, false);
  });

  await test('send failure: throws (outbox poller retries)', async () => {
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null,
      sendSignal: async () => { throw new Error('connection refused'); },
    });
    await assert.rejects(
      () => processOutboxPayload({ channel: 'signal', chat_id: '+491234567890', text: 'hi' }),
      /connection refused/,
    );
  });

  // ── Sticky progress (edit-in-place) + finalize guard ──────────────────────
  console.log('\nSticky progress + finalize guard');

  await test('first _progress: sends a new message and remembers its timestamp', async () => {
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const calls = [];
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null,
      sendSignal: async (r, m, opts) => { calls.push({ r, m, opts }); return { timestamp: '1000' }; },
    });
    const ok = await processOutboxPayload({
      channel: 'signal', chat_id: '+491234567890', msg_id: 'turn-1',
      _progress: true, text: 'thinking…',
    });
    assert.strictEqual(ok, true);
    assert.strictEqual(calls.length, 1);
    assert.strictEqual(calls[0].opts, undefined, 'first progress must NOT pass editTimestamp');
  });

  await test('second _progress for the same chat: edits in place via edit_timestamp', async () => {
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const calls = [];
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null,
      sendSignal: async (r, m, opts) => { calls.push({ r, m, opts }); return { timestamp: '2000' }; },
    });
    await processOutboxPayload({ channel: 'signal', chat_id: '+491111111111', msg_id: 'turn-2', _progress: true, text: 'step 1' });
    await processOutboxPayload({ channel: 'signal', chat_id: '+491111111111', msg_id: 'turn-2', _progress: true, text: 'step 2' });
    assert.strictEqual(calls.length, 2);
    assert.strictEqual(calls[1].m, 'step 2');
    assert.deepStrictEqual(calls[1].opts, { editTimestamp: '2000' }, 'second progress must edit the first message');
  });

  await test('_heartbeat does not open a second sticky slot when progress already owns it', async () => {
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const calls = [];
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null,
      sendSignal: async (r, m, opts) => { calls.push({ r, m, opts }); return { timestamp: '3000' }; },
    });
    await processOutboxPayload({ channel: 'signal', chat_id: '+492222222222', msg_id: 'turn-3', _progress: true, text: 'progress' });
    await processOutboxPayload({ channel: 'signal', chat_id: '+492222222222', msg_id: 'turn-3', _heartbeat: true, text: 'still working' });
    // Only the initial progress send — the heartbeat must not fire a second send.
    assert.strictEqual(calls.length, 1);
  });

  await test('real reply: remote-deletes the sticky and marks the turn finalized', async () => {
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const sendCalls = [];
    const deleteCalls = [];
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null,
      sendSignal: async (r, m, opts) => { sendCalls.push({ r, m, opts }); return { timestamp: '4000' }; },
      deleteSignal: async (r, ts) => { deleteCalls.push({ r, ts }); },
    });
    await processOutboxPayload({ channel: 'signal', chat_id: '+493333333333', msg_id: 'turn-4', _progress: true, text: 'working…' });
    await processOutboxPayload({ channel: 'signal', chat_id: '+493333333333', msg_id: 'turn-4', text: 'Here is your answer.' });
    assert.strictEqual(deleteCalls.length, 1, 'sticky must be remote-deleted before the real reply');
    assert.strictEqual(deleteCalls[0].ts, '4000');
    assert.strictEqual(sendCalls[sendCalls.length - 1].m, 'Here is your answer.');
  });

  await test('late progress/heartbeat for an already-finalized msg_id is dropped silently', async () => {
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const calls = [];
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null,
      sendSignal: async (r, m, opts) => { calls.push({ r, m, opts }); return { timestamp: '5000' }; },
      deleteSignal: async () => {},
    });
    await processOutboxPayload({ channel: 'signal', chat_id: '+494444444444', msg_id: 'turn-5', text: 'The real answer.' });
    const beforeLateDrop = calls.length;
    // Simulates the outbox alphabetical-sort race: a _hb.json for the same
    // msg_id is processed after the real reply already shipped.
    const ok = await processOutboxPayload({ channel: 'signal', chat_id: '+494444444444', msg_id: 'turn-5', _heartbeat: true, text: 'still working' });
    assert.strictEqual(ok, true, 'stale drop still reports handled=true (file must be treated as consumed)');
    assert.strictEqual(calls.length, beforeLateDrop, 'no Signal call must be made for the stale heartbeat');
  });

  await test('sticky edit failure falls back to remote-delete + fresh send', async () => {
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    let sendCount = 0;
    const deleteCalls = [];
    const { processOutboxPayload } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null,
      sendSignal: async (r, m, opts) => {
        sendCount += 1;
        if (opts && opts.editTimestamp) throw new Error('edit window expired');
        return { timestamp: `ts-${sendCount}` };
      },
      deleteSignal: async (r, ts) => { deleteCalls.push(ts); },
    });
    await processOutboxPayload({ channel: 'signal', chat_id: '+495555555555', msg_id: 'turn-6', _progress: true, text: 'step 1' });
    await processOutboxPayload({ channel: 'signal', chat_id: '+495555555555', msg_id: 'turn-6', _progress: true, text: 'step 2' });
    // call 1: initial send (1). call 2: failed edit attempt (2) + fallback fresh send (3).
    assert.strictEqual(sendCount, 3, 'initial send + failed edit attempt + fallback fresh send');
    assert.strictEqual(deleteCalls.length, 1, 'failed edit must remote-delete the stale sticky before sending fresh');
  });

  // ── Inbox envelope shape ───────────────────────────────────────────────────
  console.log('\nInbox envelope shape');

  await test('inbox JSON has all required fields', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'signal', normalize: normPhone });
    const { handleEnvelope } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, sendSignal: async () => {},
    });
    await handleEnvelope(envelope('+491234567890', 'check shape'));
    const [msg] = readInbox();
    assert.ok(msg.id,                     'must have id');
    assert.strictEqual(msg.channel, 'signal');
    assert.ok(msg.from,                   'must have from');
    assert.ok(msg.chat_id,                'must have chat_id');
    assert.ok(typeof msg.ts === 'number', 'ts must be number');
    assert.strictEqual(msg.text, 'check shape');
  });

  // ── Cleanup ────────────────────────────────────────────────────────────────
  fs.rmSync(tmp, { recursive: true, force: true });

  console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
