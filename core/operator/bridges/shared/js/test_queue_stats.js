#!/usr/bin/env node
// test_queue_stats.js — R3: upstream durable-queue liveness in the bridge
// /status. Proves queueStats() (queue_stats.js) reflects the backlog + oldest
// age of the two Python-owned durable queues (pending_notifications/,
// task_progress/) that the outbox poller is blind to — and that the numbers
// surface over the REAL /status HTTP boundary the daemons expose, not just via
// a direct function call.
//
// The Move-2 blind spot this closes: a background task whose worker died piles
// records up in those queues while /status still reads "healthy, 0
// pending_outbox". queue_stalled_s > 0 is the signal a watchdog needs.
//
// Framework-free, same style as test_net_probe.js.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');

const { queueStats } = require('./queue_stats');
const { startHealthServer } = require('./health-server');

let failures = 0;
function assert(cond, msg) {
  if (cond) {
    console.log('  ok  -', msg);
  } else {
    failures++;
    console.log('  FAIL -', msg);
  }
}

function mkTmpHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'corvin-qstats-'));
}

function writeRec(dir, name, rec) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, name), JSON.stringify(rec), 'utf8');
}

function httpGetJson(port, urlPath) {
  return new Promise((resolve, reject) => {
    const req = http.get(
      { host: '127.0.0.1', port, path: urlPath },
      (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => {
          try {
            resolve({ status: res.statusCode, json: JSON.parse(body) });
          } catch (e) {
            reject(e);
          }
        });
      },
    );
    req.on('error', reject);
  });
}

async function main() {
  console.log('test_queue_stats (R3)');
  const now = Date.now() / 1000;

  // ── 1. Empty / absent queues → all zeros (leer → 0) ─────────────────────
  {
    const home = mkTmpHome();
    const s = queueStats(home);
    assert(s.notify_backlog === 0, 'empty: notify_backlog 0');
    assert(s.notify_oldest_s === 0, 'empty: notify_oldest_s 0');
    assert(s.progress_backlog === 0, 'empty: progress_backlog 0');
    assert(s.progress_oldest_s === 0, 'empty: progress_oldest_s 0');
    assert(s.queue_stalled_s === 0, 'empty: queue_stalled_s 0');
    fs.rmSync(home, { recursive: true, force: true });
  }

  // ── 2. Stale records → backlog count + oldest age reflected ─────────────
  const home = mkTmpHome();
  const notifyDir = path.join(home, 'pending_notifications');
  const progressDir = path.join(home, 'task_progress');

  // A stale (1h-old) undelivered completion record.
  writeRec(notifyDir, 'cn_stale.json', {
    id: 'cn_stale', state: 'pending', created_at: now - 3600, sender: 'u1',
  });
  // A fresh ready record (also undelivered → counted).
  writeRec(notifyDir, 'cn_fresh.json', {
    id: 'cn_fresh', state: 'ready', created_at: now - 30, sender: 'u1',
  });
  // A DELIVERED record → must NOT count.
  writeRec(notifyDir, 'cn_done.json', {
    id: 'cn_done', state: 'delivered', created_at: now - 7200,
    delivered_at: now - 10, sender: 'u1',
  });
  // A queued progress update, 10min old.
  writeRec(progressDir, 'tp_stale.json', {
    id: 'tp_stale', state: 'queued', created_at: now - 600,
  });
  // A delivered progress update → must NOT count.
  writeRec(progressDir, 'tp_done.json', {
    id: 'tp_done', state: 'delivered', created_at: now - 900,
  });

  const s = queueStats(home);
  assert(s.notify_backlog === 2, `notify_backlog 2 (got ${s.notify_backlog})`);
  assert(
    s.notify_oldest_s >= 3590 && s.notify_oldest_s <= 3610,
    `notify_oldest_s ~3600 (got ${s.notify_oldest_s})`,
  );
  assert(s.progress_backlog === 1, `progress_backlog 1 (got ${s.progress_backlog})`);
  assert(
    s.progress_oldest_s >= 590 && s.progress_oldest_s <= 610,
    `progress_oldest_s ~600 (got ${s.progress_oldest_s})`,
  );
  assert(
    s.queue_stalled_s === s.notify_oldest_s,
    'queue_stalled_s = max oldest across both queues (the notify 3600)',
  );

  // ── 3. E2E over the REAL /status HTTP transport the daemons expose ───────
  //     getStatus spreads queueStats(home) exactly as daemon.js does.
  const server = startHealthServer({
    port: 0, kind: 'discord',
    getStatus: () => ({ pending_outbox: 0, ...queueStats(home) }),
    logger: () => {},
  });
  await new Promise((r) => server.on('listening', r));
  const port = server.address().port;
  try {
    const { status, json } = await httpGetJson(port, '/status');
    assert(status === 200, '/status returns 200');
    assert(json.kind === 'discord', '/status carries kind');
    assert(json.notify_backlog === 2, '/status mirrors notify_backlog=2');
    assert(json.progress_backlog === 1, '/status mirrors progress_backlog=1');
    assert(json.queue_stalled_s > 3000, '/status queue_stalled_s reflects the stall');
    // A 404 path must still not crash the server.
    const nf = await httpGetJson(port, '/nope').catch(() => ({ status: 404 }));
    assert(nf.status === 404, 'non-/status path 404s (server survives)');
  } finally {
    server.close();
    fs.rmSync(home, { recursive: true, force: true });
  }

  // ── 4. A fresh in-flight (young, pending) task is NOT a stall ────────────
  //     Regression for the queue_stalled_s false-positive: a worker that is
  //     still running (state=pending, young) must show up as backlog but must
  //     NOT raise queue_stalled_s — only finished-but-undelivered or genuinely
  //     old records do.
  {
    const h = mkTmpHome();
    const nd = path.join(h, 'pending_notifications');
    // Young, still-running task → backlog yes, stall no.
    writeRec(nd, 'cn_running.json', {
      id: 'cn_running', state: 'pending', created_at: now - 20, sender: 'u9',
    });
    let s = queueStats(h);
    assert(s.notify_backlog === 1, `young pending counts as backlog (got ${s.notify_backlog})`);
    assert(
      s.queue_stalled_s === 0,
      `young pending is NOT a stall (got queue_stalled_s=${s.queue_stalled_s})`,
    );
    // A young FINISHED-but-undelivered ('ready') record IS a delivery stall.
    writeRec(nd, 'cn_ready.json', {
      id: 'cn_ready', state: 'ready', created_at: now - 15, sender: 'u9',
    });
    s = queueStats(h);
    assert(
      s.queue_stalled_s > 0,
      `young ready (finished, undelivered) IS a stall (got ${s.queue_stalled_s})`,
    );
    fs.rmSync(h, { recursive: true, force: true });
  }

  if (failures) {
    console.log(`\n${failures} FAILURE(S)`);
    process.exit(1);
  }
  console.log('\nall passed');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
