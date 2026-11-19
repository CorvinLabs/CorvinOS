#!/usr/bin/env node
// test_inbound_log_pii.js — review 2026-11-19, R3-NEW-13.
//
// The WhatsApp daemon logged `from=<jid> ... text="<first 80 chars>"` for
// EVERY inbound message of the account, before any auth check — third-party
// chats included. The line is now built by shared/js/logger.js::inboundLogLine
// (sender fingerprint + text length). daemon.js opens a Baileys socket at
// require time, so the line is produced here through the real logger with a
// captured log file, and the daemon's call site is pinned structurally.

'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-log-'));
const LOG = path.join(tmp, 'corvin.log');
process.env.CORVIN_LOG_FILE = LOG;   // keep the file sink out of the live log
process.env.CORVIN_LOG_STDERR = '1';

const { makeLogger, inboundLogLine, senderFp } = require('../shared/js/logger');

let passed = 0, failed = 0;
function ok(label, fn) {
  try { fn(); console.log(`  ok  ${label}`); passed++; }
  catch (e) { console.error(`  FAIL ${label}\n       ${e.message}`); failed++; }
}

console.log('== whatsapp inbound log line carries no sender id and no text ==');

const JID = '4915112345678@s.whatsapp.net';
const TEXT = 'Meine IBAN ist DE89 3704 0044 0532 0130 00, bitte nicht weitergeben';

ok('captured log line: fingerprint + length only', () => {
  const log = makeLogger('wa-test');
  // Capture what the real logger emits (stderr sink is synchronous).
  const chunks = [];
  const realWrite = process.stderr.write;
  process.stderr.write = (c) => { chunks.push(String(c)); return true; };
  try { log(inboundLogLine({ from: JID, kind: 'text', text: TEXT })); }
  finally { process.stderr.write = realWrite; }
  const out = chunks.join('');
  assert.ok(out.includes('upsert: from='), `log line missing: ${out}`);   // positive control
  assert.ok(out.includes(`text_len=${TEXT.length}`));
  assert.ok(out.includes(`from=${senderFp(JID)}`));
  assert.ok(!out.includes('4915112345678'), 'phone number leaked');
  assert.ok(!out.includes('IBAN') && !out.includes('DE89') && !out.includes('weitergeben'), 'message text leaked');
});

ok('fingerprint is stable, short and not the id', () => {
  assert.strictEqual(senderFp(JID), senderFp(JID.toUpperCase()));
  assert.notStrictEqual(senderFp(JID), senderFp('4915100000000@s.whatsapp.net'));
  assert.ok(/^[0-9a-f]{12}$/.test(senderFp(JID)));
});

ok('empty / missing text is length 0, never "undefined"', () => {
  assert.ok(inboundLogLine({ from: JID, kind: 'voice', text: undefined }).endsWith('text_len=0'));
});

const src = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

ok('daemon.js: the pre-auth upsert line goes through inboundLogLine', () => {
  assert.ok(/log\(inboundLogLine\(\{ from: sender, kind: kindLog, text \}\)\);/.test(src));
  const iLog = src.indexOf('log(inboundLogLine(');
  const iAuth = src.indexOf('if (!authOk(sender, text, sender, m.key.fromMe))');
  assert.ok(iLog !== -1 && iAuth !== -1 && iLog < iAuth, 'the line under test is the one before the auth gate');
});

ok('daemon.js: no upsert log line carries the sender id or message text', () => {
  assert.ok(!/upsert: from=\$\{sender\}/.test(src));
  assert.ok(!/text="\$\{text\.slice/.test(src));
});

ok('daemon.js: read-only audit record carries a length, not a snippet', () => {
  assert.ok(!/snippet/.test(src.slice(src.indexOf('function readOnlyOk('), src.indexOf('const READ_ONLY_ACK'))));
});

fs.rmSync(tmp, { recursive: true, force: true });
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
