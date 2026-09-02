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

// A record counts toward `queue_stalled_s` only when it is genuinely STUCK, not
// merely in-flight: a still-running (pending, not yet finished) task is normal
// and must not raise a stall. So a record is "stalled" only if it is either
// FINISHED-but-not-delivered (its work is done, delivery is what's wedged) OR
// older than this threshold (undelivered for suspiciously long regardless of
// state). Below this age an ordinary pending task is just working.
const STALL_THRESHOLD_S = 300; // 5 min

// pending_notifications record is undelivered while state != "delivered".
// task_progress record is undelivered while state == "queued".
// `isPending` gates the backlog + oldest-age (visibility); `isStalled` gates the
// separate stall signal (see STALL_THRESHOLD_S).
function _scan(dir, { glob, isPending, isStalled }) {
  let backlog = 0;
  let oldest = 0; // largest age in seconds among undelivered records
  let stalled = 0; // largest age among records that count as a stall
  let entries;
  try {
    entries = fs.readdirSync(dir);
  } catch {
    return { backlog: 0, oldest_s: 0, stalled_s: 0 }; // dir absent → empty queue
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
    if (isStalled(rec, age) && age > stalled) stalled = age;
  }
  return {
    backlog,
    oldest_s: Math.max(0, Math.round(oldest)),
    stalled_s: Math.max(0, Math.round(stalled)),
  };
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
      // A completion record is stalled if its work is DONE ('ready') but not
      // delivered, or if it has sat undelivered past the threshold. A fresh
      // 'pending' record (worker still running) is NOT a stall.
      isStalled: (r, age) => r.state === 'ready' || age > STALL_THRESHOLD_S,
    });
    const progress = _scan(path.join(root, 'task_progress'), {
      glob: /^tp_.*\.json$/,
      isPending: (r) => r.state === 'queued',
      // A 'queued' progress update is content already produced and waiting to be
      // delivered — so it's a stall only once it has waited past the threshold.
      isStalled: (r, age) => age > STALL_THRESHOLD_S,
    });
    return {
      notify_backlog: notify.backlog,
      notify_oldest_s: notify.oldest_s,
      progress_backlog: progress.backlog,
      progress_oldest_s: progress.oldest_s,
      // Single Move-2 signal: the oldest STALLED item anywhere upstream (see
      // STALL_THRESHOLD_S). > 0 and growing = a stall the outbox poller cannot
      // see. A merely in-flight (young, pending) task does NOT raise this.
      queue_stalled_s: Math.max(notify.stalled_s, progress.stalled_s),
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
