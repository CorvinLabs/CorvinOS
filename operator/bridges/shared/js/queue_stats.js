// queue_stats.js — upstream durable-queue liveness for the bridge /status.
//
// THE GAP this closes: /status reported `pending_outbox` and poller liveness,
// but the two DURABLE upstream queues that feed the outbox — the Python-owned
// completion queue (CORVIN_HOME/pending_notifications) and the intermediate
// progress queue (CORVIN_HOME/task_progress) — were invisible. A background
// task whose worker died leaves records piling up there while /status still
// reads "healthy, 0 pending_outbox", because nothing ever wrote them to the
// outbox. This exposes the backlog + oldest age of each queue plus a single
// `queue_stalled_s` (the age of the oldest undelivered item across both) so a
// stall is observable instead of a Move-2 blind spot.
//
// Pure Node fs, sync, best-effort: any error yields zeros/null, never throws —
// a /status handler must never be able to crash on a queue stat.

const fs = require('fs');
const path = require('path');
const { corvinHome } = require('./bridge_paths');

// pending_notifications record is undelivered while state != "delivered".
// task_progress record is undelivered while state == "queued".
function _scan(dir, { glob, isPending }) {
  let backlog = 0;
  let oldest = 0; // largest age in seconds among undelivered records
  let entries;
  try {
    entries = fs.readdirSync(dir);
  } catch {
    return { backlog: 0, oldest_s: 0 }; // dir absent → empty queue
  }
  const now = Date.now() / 1000;
  for (const name of entries) {
    if (!name.endsWith('.json')) continue;
    if (glob && !glob.test(name)) continue;
    const full = path.join(dir, name);
    let rec;
    try {
      rec = JSON.parse(fs.readFileSync(full, 'utf8'));
    } catch {
      continue; // malformed/partial record — skip
    }
    if (!rec || typeof rec !== 'object') continue;
    if (!isPending(rec)) continue;
    backlog += 1;
    // Prefer the record's own created_at; fall back to file mtime.
    let created = Number(rec.created_at);
    if (!Number.isFinite(created) || created <= 0) {
      try {
        created = fs.statSync(full).mtimeMs / 1000;
      } catch {
        created = now;
      }
    }
    const age = now - created;
    if (age > oldest) oldest = age;
  }
  return { backlog, oldest_s: Math.max(0, Math.round(oldest)) };
}

/**
 * Liveness of the two durable upstream queues under CORVIN_HOME.
 * @param {string} [home] — override CORVIN_HOME (tests); defaults to corvinHome()
 * @returns {{notify_backlog:number, notify_oldest_s:number,
 *            progress_backlog:number, progress_oldest_s:number,
 *            queue_stalled_s:number}}
 */
function queueStats(home) {
  try {
    const root = home || corvinHome();
    const notify = _scan(path.join(root, 'pending_notifications'), {
      glob: null,
      isPending: (r) => r.state !== 'delivered',
    });
    const progress = _scan(path.join(root, 'task_progress'), {
      glob: /^tp_.*\.json$/,
      isPending: (r) => r.state === 'queued',
    });
    return {
      notify_backlog: notify.backlog,
      notify_oldest_s: notify.oldest_s,
      progress_backlog: progress.backlog,
      progress_oldest_s: progress.oldest_s,
      // Single Move-2 signal: the oldest undelivered item anywhere upstream.
      // > 0 and growing = a stall the outbox poller cannot see.
      queue_stalled_s: Math.max(notify.oldest_s, progress.oldest_s),
    };
  } catch {
    return {
      notify_backlog: 0,
      notify_oldest_s: 0,
      progress_backlog: 0,
      progress_oldest_s: 0,
      queue_stalled_s: 0,
    };
  }
}

module.exports = { queueStats };
