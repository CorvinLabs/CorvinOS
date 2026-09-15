#!/usr/bin/env node
// test_outbox_poller.js — unit tests for startOutboxPoller's preCheck gating
// and the send-failure log dedup added after incident 2026-07-10 (Discord
// daemon logged "send failed … Expected token" twice per second per file
// while waiting out an offline login → 1000+ journal lines).

'use strict';

const fs   = require('fs');
const os   = require('os');
const path = require('path');

const { startOutboxPoller, countPending } = require('./outbox');

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

  // 4. Successful send removes the file and says so.
  //    The assertion here used to be `logs.length === 0`. Its stated intent was
  //    "the dedup bookkeeping must not leak log lines" — silence was the proxy, not
  //    the goal. Taken literally it mandated that a DELIVERED message look exactly
  //    like a silently dropped one, which is how verifying a single Discord
  //    round-trip on 2026-07-26 ended up requiring a Discord REST query: the daemon
  //    had been quiet for two hours and the journal could not tell the two apart.
  //    Now: success logs exactly one line, and no failure line appears.
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
  assert(logs3.length === 1, `clean delivery logs exactly one line (got ${logs3.length})`);
  assert(logs3[0].includes('outbox: sent'), 'the line names the delivery');
  assert(logs3[0].includes('testchan'), 'the line names the channel');
  assert(!logs3.some((m) => m.includes('failed')), 'no failure line on a clean send');

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

  // 11. A FINISHED ANSWER is never destroyed by a writer bug.
  //     Both of these paths used to unlink() the envelope. The file in the outbox is
  //     an answer the engine already produced and the user is waiting for: an
  //     unparseable envelope is usually a truncated write (crash mid-write, disk
  //     full) whose text is still recoverable by hand, and a missing `channel` is a
  //     writer bug that will be fixed and the envelope re-queued. Deleting the only
  //     copy makes both unrecoverable. Dead-letter instead.
  const dlRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'outbox-dl-'));
  const dlDead = path.join(dlRoot, 'dead');
  fs.writeFileSync(path.join(dlRoot, 'truncated.json'), '{"channel":"testchan","text":"half a rep');
  fs.writeFileSync(path.join(dlRoot, 'nochannel.json'), JSON.stringify({ text: 'answer without a channel' }));
  const logs11 = [];
  const poller11 = startOutboxPoller({
    outboxDir: dlRoot,
    channel: 'testchan',
    sendFn: async () => {},
    logger: (m) => logs11.push(m),
    intervalMs: 20,
    deadLetterDir: dlDead,
  });
  await sleep(200);
  poller11.stop();
  assert(!fs.existsSync(path.join(dlRoot, 'truncated.json')), 'the bad envelope left the outbox');
  assert(fs.existsSync(path.join(dlDead, 'truncated.json')),
         'an unparseable envelope is dead-lettered, not deleted');
  assert(fs.readFileSync(path.join(dlDead, 'truncated.json'), 'utf8').includes('half a rep'),
         'the recoverable text survived');
  assert(fs.existsSync(path.join(dlDead, 'nochannel.json')),
         'an envelope with no channel is dead-lettered, not deleted');
  assert(fs.existsSync(path.join(dlDead, 'truncated.json.reason.json')),
         'the diagnosis sidecar says why');
  fs.rmSync(dlRoot, { recursive: true, force: true });

  // 11b. A TRANSIENT read failure (e.g. an AV scanner or OneDrive briefly
  //      holding a sharing lock right after the Python side creates the
  //      file) must NOT be treated as a corrupt envelope. Before this fix,
  //      readFileSync and JSON.parse shared one try/catch — any
  //      readFileSync error was indistinguishable from bad JSON and
  //      immediately dead-lettered (or deleted, with no deadLetterDir
  //      configured), destroying a perfectly good, already-produced reply
  //      for a purely transient filesystem hiccup. Simulated here by
  //      monkey-patching fs.readFileSync to throw exactly once for the
  //      target file, then succeed normally on the next tick.
  const trRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'outbox-transient-'));
  fs.writeFileSync(path.join(trRoot, 'flaky.json'),
                    JSON.stringify({ channel: 'testchan', text: 'transient-read-survivor' }));
  const logs11b = [];
  let trSends = 0;
  let readAttempts = 0;
  const realReadFileSync = fs.readFileSync;
  fs.readFileSync = function (p, ...rest) {
    if (typeof p === 'string' && p.endsWith('flaky.json')) {
      readAttempts++;
      if (readAttempts === 1) {
        const err = new Error('EBUSY: resource busy or locked');
        err.code = 'EBUSY';
        throw err;
      }
    }
    return realReadFileSync.call(fs, p, ...rest);
  };
  const poller11b = startOutboxPoller({
    outboxDir: trRoot,
    channel: 'testchan',
    sendFn: async () => { trSends++; },
    logger: (m) => logs11b.push(m),
    intervalMs: 20,
  });
  await sleep(150);
  poller11b.stop();
  fs.readFileSync = realReadFileSync;
  assert(readAttempts >= 2, `readFileSync was retried after the transient failure (attempts=${readAttempts})`);
  assert(trSends === 1, `the envelope was delivered exactly once despite the transient read error (sends=${trSends})`);
  assert(logs11b.some((m) => m.includes('transient read error')),
         'the transient read failure is logged distinctly from a bad-JSON drop');
  assert(!logs11b.some((m) => m.includes('bad JSON') || m.includes('unparseable')),
         'a transient read error is never misreported as a parse/corruption failure');
  fs.rmSync(trRoot, { recursive: true, force: true });

  // 12. Regression (incident 2026-07-27): a preCheck that stays false forever
  //     settles every tick instantly, so `stalled_s` (test 10) never sees it —
  //     that gap let a Discord daemon look healthy (`poller_stalled_s: 0`)
  //     for 90 minutes while 5 replies sat undelivered with zero log lines.
  //     A stuck preCheck must be logged the same way a stuck tick is.
  const pcRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'outbox-precheck-'));
  fs.writeFileSync(path.join(pcRoot, 'h.json'), JSON.stringify({ channel: 'testchan', text: 'gated' }));
  const logs12 = [];
  let pcReady = false;
  const poller12 = startOutboxPoller({
    outboxDir: pcRoot,
    channel: 'testchan',
    sendFn: async () => {},
    preCheck: () => pcReady,
    logger: (m) => logs12.push(m),
    intervalMs: 20,
    stallWarnMs: 80,
  });
  await sleep(250);
  assert(logs12.some((m) => m.includes('preCheck has been blocking delivery')),
         'a preCheck stuck false is logged, not silent');
  assert(logs12.filter((m) => m.includes('preCheck has been blocking delivery')).length === 1,
         'the preCheck-stall log is deduped, not spammed every 20ms tick');
  const st12 = poller12.stats();
  // Rounded to whole seconds (like stalled_s in test 10) — at these ms-scale
  // test intervals it may legitimately round to 0, so only the type/shape is
  // checked here; the "> 0 while stuck" behavior is exercised for real by the
  // dedup-log assertion above, which only fires once stalledMs > stallWarnMs.
  assert(st12.precheck_stalled_s >= 0 && typeof st12.precheck_stalled_s === 'number',
         'stats() exposes precheck_stalled_s for /status');

  // preCheck flipping true clears the stall and lets delivery resume — the
  // gate is transient (a reconnect), not a permanent failure.
  pcReady = true;
  await sleep(100);
  poller12.stop();
  assert(!fs.existsSync(path.join(pcRoot, 'h.json')), 'delivery resumes once preCheck passes again');
  assert(poller12.stats().precheck_stalled_s === 0, 'precheck_stalled_s resets once the gate opens');
  fs.rmSync(pcRoot, { recursive: true, force: true });

  // 13. countPending() — the SHARED outbox directory holds every channel's
  //     envelopes. A naive `readdirSync(...).length` (removed 2026-07-27)
  //     reported the same total for whatsapp/discord/email regardless of
  //     which channel actually owned the backlog. countPending must filter
  //     by the envelope's own `channel` field.
  const cpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'outbox-countpending-'));
  fs.writeFileSync(path.join(cpRoot, 'x1.json'), JSON.stringify({ channel: 'discord', text: 'a' }));
  fs.writeFileSync(path.join(cpRoot, 'x2.json'), JSON.stringify({ channel: 'discord', text: 'b' }));
  fs.writeFileSync(path.join(cpRoot, 'x3.json'), JSON.stringify({ channel: 'email', text: 'c' }));
  fs.writeFileSync(path.join(cpRoot, 'x4.json'), 'not valid json');
  fs.writeFileSync(path.join(cpRoot, 'x5.txt'), 'ignored, not .json');
  assert(countPending(cpRoot, 'discord') === 2, 'counts only envelopes for the requested channel');
  assert(countPending(cpRoot, 'email') === 1, 'a different channel gets its own count, not the shared total');
  assert(countPending(cpRoot, 'whatsapp') === 0, 'a channel with no envelopes counts zero, not the total');
  assert(countPending('/no/such/dir', 'discord') === 0, 'a missing outbox directory counts zero, does not throw');
  fs.rmSync(cpRoot, { recursive: true, force: true });

  fs.rmSync(tmpRoot, { recursive: true, force: true });

  if (failures > 0) {
    console.log(`FAILED — ${failures} assertion(s) failed.`);
    process.exit(1);
  }
  console.log('PASSED');
}

main().catch((e) => { console.error(e); process.exit(1); });
