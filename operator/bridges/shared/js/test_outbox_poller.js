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

  // 5. Without deadLetterDir the poller keeps retrying forever — bridges that
  //    never configured a dead-letter dir must not silently change behavior.
  writeEnvelope('b.json', { channel: 'testchan', text: 'permanent-fail' });
  const poller5 = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async () => { const e = new Error('nope'); e.code = 50035; throw e; },
    isPermanent: (e) => e.code === 50035,
    logger: () => {},
    intervalMs: 20,
  });
  await sleep(200);
  poller5.stop();
  assert(fs.existsSync(path.join(tmpRoot, 'b.json')),
         'no deadLetterDir → permanent failure still retried, file stays put');

  // 6. deadLetterDir + isPermanent → retired on the FIRST failure, not retried.
  const deadDir = path.join(tmpRoot, 'dead');
  const logs6 = [];
  let sends6 = 0;
  const poller6 = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async () => { sends6++; const e = new Error('Invalid Form Body'); e.code = 50035; throw e; },
    isPermanent: (e) => e.code === 50035,
    deadLetterDir: deadDir,
    logger: (m) => logs6.push(m),
    intervalMs: 20,
  });
  await sleep(200);
  poller6.stop();
  assert(!fs.existsSync(path.join(tmpRoot, 'b.json')), 'permanent failure left the outbox');
  assert(fs.existsSync(path.join(deadDir, 'b.json')), 'envelope moved to dead-letter dir');
  assert(sends6 === 1, `permanent error not retried (sends=${sends6})`);
  assert(logs6.some((m) => m.includes('dead-lettered b.json')), 'dead-letter is logged');
  const preserved = JSON.parse(fs.readFileSync(path.join(deadDir, 'b.json'), 'utf8'));
  assert(preserved.text === 'permanent-fail', 'envelope preserved byte-for-byte for re-queueing');
  const sidecar = JSON.parse(fs.readFileSync(path.join(deadDir, 'b.json.reason.json'), 'utf8'));
  assert(sidecar.reason === 'permanent send error' && sidecar.attempts === 1,
         'sidecar records reason and attempt count');

  // 7. Transient (unclassified) failures exhaust maxAttempts before retiring —
  //    a network blip must not retire a deliverable message on the first tick.
  writeEnvelope('c.json', { channel: 'testchan', text: 'transient' });
  let sends7 = 0;
  const poller7 = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async () => { sends7++; throw new Error('ECONNRESET'); },
    isPermanent: () => false,
    deadLetterDir: deadDir,
    maxAttempts: 3,
    logger: () => {},
    intervalMs: 20,
  });
  await sleep(250);
  poller7.stop();
  assert(sends7 === 3, `transient error retried up to maxAttempts (sends=${sends7})`);
  assert(fs.existsSync(path.join(deadDir, 'c.json')), 'transient failure retired after budget');
  const sidecar7 = JSON.parse(fs.readFileSync(path.join(deadDir, 'c.json.reason.json'), 'utf8'));
  assert(sidecar7.reason === '3 attempts exhausted', 'sidecar distinguishes exhaustion from permanent');

  // 8. Regression (incident 2026-07-25): a sendFn that returns normally is
  //    treated as delivered. The Discord daemon's `if (!ch) return` therefore
  //    unlinked finished replies. Any not-delivered path MUST throw — this test
  //    pins the contract the daemon relies on.
  writeEnvelope('d.json', { channel: 'testchan', text: 'must-not-vanish' });
  const poller8 = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async () => { throw new Error('channel 123 not found (cache miss or deleted)'); },
    isPermanent: () => false,
    deadLetterDir: deadDir,
    maxAttempts: 999,
    logger: () => {},
    intervalMs: 20,
  });
  await sleep(120);
  poller8.stop();
  assert(fs.existsSync(path.join(tmpRoot, 'd.json')),
         'throwing send keeps the envelope — reply is never silently lost');

  // 9. Regression (incident 2026-07-26): a sendFn that NEVER settles wedged
  //    the poller permanently — `running` stayed true, every later tick hit
  //    `if (running) return`, and 38 minutes of replies/heartbeats piled up
  //    in the outbox without a single log line. The send timeout must break
  //    the hang and let the envelope re-enter the normal retry path.
  fs.rmSync(path.join(tmpRoot, 'd.json'), { force: true });
  writeEnvelope('e.json', { channel: 'testchan', text: 'hangs-forever' });
  writeEnvelope('f.json', { channel: 'testchan', text: 'queued-behind-the-hang' });
  let hangs = 0;
  let delivered9 = 0;
  const logs9 = [];
  const poller9 = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async (payload) => {
      if (payload.text === 'hangs-forever') { hangs++; return new Promise(() => {}); }
      delivered9++;
    },
    isPermanent: () => false,
    logger: (m) => logs9.push(m),
    intervalMs: 20,
    sendTimeoutMs: 100,
    stallWarnMs: 60_000,
  });
  await sleep(400);
  poller9.stop();
  assert(hangs >= 2, `hanging send timed out and was retried (attempts=${hangs})`);
  assert(delivered9 >= 1,
         'envelope queued behind the hang still gets delivered');
  assert(!fs.existsSync(path.join(tmpRoot, 'f.json')),
         'the poller is not wedged — the queue keeps draining');
  assert(logs9.some((m) => m.includes('send timed out')),
         'the timeout is visible in the log, not silent');

  // 10. A tick that stalls past stallWarnMs must SAY so — the absence of any
  //     log line is what made the outage undiagnosable for 38 minutes.
  fs.rmSync(path.join(tmpRoot, 'e.json'), { force: true });
  writeEnvelope('g.json', { channel: 'testchan', text: 'stalls' });
  const logs10 = [];
  const poller10 = startOutboxPoller({
    outboxDir: tmpRoot,
    channel: 'testchan',
    sendFn: async () => new Promise(() => {}),
    logger: (m) => logs10.push(m),
    intervalMs: 20,
    sendTimeoutMs: 0,        // timeout disabled → only the stall detector can see it
    stallWarnMs: 80,
    stallResetMs: 200,
  });
  await sleep(500);
  poller10.stop();
  assert(logs10.some((m) => m.includes('tick stalled')),
         'a wedged tick is logged instead of failing silently');
  assert(logs10.some((m) => m.includes('force-releasing')),
         'stallResetMs releases the wedged flag so delivery resumes');
  const st10 = poller10.stats();
  assert(typeof st10.stalled_s === 'number',
         'stats() exposes stall duration for /status + watchdog');

  fs.rmSync(tmpRoot, { recursive: true, force: true });

  if (failures > 0) {
    console.log(`FAILED — ${failures} assertion(s) failed.`);
    process.exit(1);
  }
  console.log('PASSED');
}

main().catch((e) => { console.error(e); process.exit(1); });
