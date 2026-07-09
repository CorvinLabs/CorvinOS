#!/usr/bin/env node
// test_outbox_poller.js — unit tests for startOutboxPoller's preCheck gating
// and the send-failure log dedup added after incident 2026-07-10 (Discord
// daemon logged "send failed … Expected token" twice per second per file
// while waiting out an offline login → 1000+ journal lines).

'use strict';

const fs   = require('fs');
const os   = require('os');
const path = require('path');

const { startOutboxPoller } = require('./outbox');

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'corvin-outbox-test-'));

let failures = 0;
function assert(cond, msg) {
  if (cond) {
    console.log('  ok  -', msg);
  } else {
    failures++;
    console.log('  FAIL -', msg);
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function writeEnvelope(name, payload) {
  fs.writeFileSync(path.join(tmpRoot, name), JSON.stringify(payload));
}

async function main() {
  console.log('test_outbox_poller');

  // 1. preCheck=false → sendFn is never invoked, file stays put.
  writeEnvelope('a.json', { channel: 'testchan', text: 'hi' });
  let sends = 0;
  let ready = false;
  const logs = [];
  const poller = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async () => { sends++; throw new Error('boom-1'); },
    preCheck: () => ready,
    logger: (m) => logs.push(m),
    intervalMs: 20,
  });
  await sleep(150);
  assert(sends === 0, 'preCheck=false gates sendFn entirely');
  assert(fs.existsSync(path.join(tmpRoot, 'a.json')), 'file waits in outbox while gated');
  assert(logs.length === 0, 'no failure spam while gated');

  // 2. preCheck flips true → sends start; identical failure logged once,
  //    not once per 20 ms tick.
  ready = true;
  await sleep(300);
  assert(sends > 3, `sendFn retried after gate opened (sends=${sends})`);
  const boom1 = logs.filter((m) => m.includes('boom-1'));
  assert(boom1.length === 1,
         `identical failure deduped to one log line (got ${boom1.length})`);
  poller.stop();

  // 3. Changed error message logs again immediately.
  const logs2 = [];
  let phase = 0;
  const poller2 = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async () => { throw new Error(phase === 0 ? 'err-A' : 'err-B'); },
    logger: (m) => logs2.push(m),
    intervalMs: 20,
  });
  await sleep(120);
  phase = 1;
  await sleep(120);
  poller2.stop();
  assert(logs2.some((m) => m.includes('err-A')), 'first error logged');
  assert(logs2.some((m) => m.includes('err-B')), 'changed error logged again');
  assert(logs2.filter((m) => m.includes('err-A')).length === 1, 'err-A logged exactly once');
  assert(logs2.filter((m) => m.includes('err-B')).length === 1, 'err-B logged exactly once');

  // 4. Successful send removes the file (dedup entry cleanup is internal —
  //    observable behavior: file gone, no further logs).
  const logs3 = [];
  const poller3 = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async () => {},
    logger: (m) => logs3.push(m),
    intervalMs: 20,
  });
  await sleep(150);
  poller3.stop();
  assert(!fs.existsSync(path.join(tmpRoot, 'a.json')), 'file delivered and unlinked');
  assert(logs3.length === 0, 'clean delivery logs nothing');

  fs.rmSync(tmpRoot, { recursive: true, force: true });

  if (failures > 0) {
    console.log(`FAILED — ${failures} assertion(s) failed.`);
    process.exit(1);
  }
  console.log('PASSED');
}

main().catch((e) => { console.error(e); process.exit(1); });
