// outbox.js — generischer Outbox-Polling-Loop.
//
// Vor dem Refactor hatte jeder daemon seine eigene processOutbox() mit dem
// gleichen Boilerplate (read dir → parse JSON → channel filter → send →
// unlink). Hier zentralisiert, inklusive des in Phase 1 gefixten
// Strict-Channel-Checks: payload.channel MUSS set sein UND === channel
// — kein silent default auf 'whatsapp'.

const fs = require('fs');
const path = require('path');

/**
 * @param {object} cfg
 * @param {string} cfg.outboxDir        — path zum gemeinsamen outbox/-directory
 * @param {string} cfg.channel          — eigener Channel-Name (z.B. 'telegram')
 * @param {function} cfg.sendFn         — async (payload, fpath) => void
 *                                        Bei success: returnt normal, file wed gedeletes.
 *                                        Bei Throw: file bleibt, Tick versucht es erneut.
 * @param {function} [cfg.preCheck]     — sync () => boolean. Wenn false, Tick bricht ab
 *                                        bevor das next File angefasst wed (z.B.
 *                                        WhatsApp-Socket nicht ready).
 * @param {function} [cfg.logger]
 * @param {number}   [cfg.intervalMs=500]
 * @param {string} [cfg.deadLetterDir]  — opt-in. Wenn gesetzt, werden dauerhaft
 *                                        unzustellbare Envelopes dorthin verschoben
 *                                        statt endlos retried. Ohne diese Option
 *                                        bleibt das alte Verhalten (infinite retry)
 *                                        unverändert — kein stiller Wechsel für
 *                                        Bridges, die das nicht konfiguriert haben.
 * @param {function} [cfg.isPermanent]  — sync (err) => boolean. Channel-spezifische
 *                                        Klassifikation: true → sofort dead-lettern,
 *                                        kein Retry (z.B. Discord 50035 Invalid Form
 *                                        Body — retrien kann das nie heilen).
 * @param {number}   [cfg.maxAttempts=10] — Fallback für unklassifizierte Fehler:
 *                                        nach so vielen Fehlversuchen dead-lettern.
 *                                        Nur wirksam wenn deadLetterDir gesetzt ist.
 * @param {number} [cfg.sendTimeoutMs=120000] — Hard-Deadline um EINEN sendFn-Call.
 *                                        Läuft sie ab, wirft der Poller statt weiter
 *                                        zu warten; der Envelope bleibt liegen und
 *                                        durchläuft die normale Retry-/Dead-Letter-
 *                                        Logik. 0 deaktiviert den Timeout (altes
 *                                        Verhalten). Siehe Deadlock-Kommentar unten.
 * @param {number} [cfg.stallWarnMs=300000] — Ab dieser Tick-Laufzeit loggt der Poller
 *                                        "tick stalled" (einmal pro LOG_DEDUP_MS)
 *                                        statt stumm zu bleiben.
 * @param {number} [cfg.stallResetMs=900000] — Backstop: dauert ein Tick SO lange,
 *                                        wird das running-Flag zwangsweise gelöst,
 *                                        damit der Poller weiterläuft. 0 = nie.
 * @returns {{stop: function, stats: function}} — handle mit stop() zum Cleanup und
 *                                        stats() für Health-Endpoints
 */
function startOutboxPoller({
  outboxDir, channel, sendFn, preCheck, logger, intervalMs = 500,
  deadLetterDir = null, isPermanent = null, maxAttempts = 10,
  sendTimeoutMs = 120_000, stallWarnMs = 300_000, stallResetMs = 900_000,
}) {
  let running = false;
  // Wall-clock start of the currently running tick (0 = idle). Load-bearing
  // for the stall detector below: without it a tick that never settles left
  // `running === true` forever and every subsequent interval fired straight
  // into `if (running) return` — the poller was permanently dead while the
  // process stayed alive, the gateway stayed connected and /status still
  // answered. Nothing was logged, so no watchdog could see it and replies
  // piled up in the outbox unnoticed (incident 2026-07-26).
  let runningSince = 0;
  let lastStallLog = 0;
  let lastTickEnd = Date.now();
  // preCheck stall tracking. A tick whose preCheck() returns false settles
  // immediately — `running`/`runningSince` above never see it as stalled,
  // because the tick was never mid-flight, it just declined to do anything.
  // That is exactly the gap that let a Discord daemon report `paired: true,
  // poller_stalled_s: 0` for 90 minutes while `client.isReady()` stayed false
  // and every tick returned instantly on the first file without a single log
  // line (incident 2026-07-27). Tracked separately from runningSince/stall
  // detection above, which only covers a tick that hangs mid-await.
  let precheckFailSince = 0;
  let lastPrecheckLog = 0;
  // Sent-once guard: track files that were successfully sent but whose
  // unlink() failed. On the next tick we delete them instead of re-sending,
  // which would duplicate voice notes / messages.
  const _sentOnce = new Set();
  // Send-failure log dedup: with a 500 ms tick, a persistent failure (e.g.
  // daemon offline during a network outage) logs the same line twice per
  // second per file — 1000+ journal lines in minutes (incident 2026-07-10).
  // Log a given file's failure only when the message changes or once per
  // LOG_DEDUP_MS. Entries are dropped once the file leaves the outbox.
  const LOG_DEDUP_MS = 60_000;
  const _lastFailLog = new Map(); // fpath → {msg, ts}
  function logSendFailure(fpath, f, msg) {
    if (!logger) return;
    const prev = _lastFailLog.get(fpath);
    const now = Date.now();
    if (prev && prev.msg === msg && now - prev.ts < LOG_DEDUP_MS) return;
    _lastFailLog.set(fpath, { msg, ts: now });
    logger(`outbox: send failed for ${f}: ${msg}`);
  }

  // Dead-letter bookkeeping. Without this a permanently undeliverable envelope
  // (deleted channel, malformed chat_id) is retried every tick forever: 328 such
  // files accumulated in the Discord outbox and made a full poll pass take ~65 s,
  // delaying every real reply behind the poison backlog (incident 2026-07-25).
  const _attempts = new Map(); // fpath → failure count

  // Hard deadline around a single sendFn call. A send that never settles is
  // the one failure mode the retry/dead-letter machinery cannot survive: the
  // envelope is neither delivered nor failed, so no attempt is ever counted
  // and the whole for-loop below stops mid-flight.
  //
  // Trade-off, deliberate: the underlying send is NOT cancellable, so a call
  // that eventually succeeds *after* the timeout can produce a duplicate
  // message when the envelope is retried. That is why the default is a
  // generous 120 s — well beyond any healthy Discord/Telegram send, so only a
  // genuine hang trips it. A rare duplicate beats a silently dead poller that
  // drops every subsequent reply.
  function sendWithTimeout(payload, fpath) {
    if (!sendTimeoutMs || sendTimeoutMs <= 0) return sendFn(payload, fpath);
    let timer = null;
    const deadline = new Promise((_resolve, reject) => {
      timer = setTimeout(() => {
        const err = new Error(
          `send timed out after ${Math.round(sendTimeoutMs / 1000)}s`);
        // Distinct marker so channel-specific isPermanent() classifiers
        // (which match numeric API error codes) never mistake a timeout for
        // a permanent error — a hang is transient by nature and should ride
        // the normal attempts budget.
        err.code = 'OUTBOX_SEND_TIMEOUT';
        reject(err);
      }, sendTimeoutMs);
      if (typeof timer.unref === 'function') timer.unref();
    });
    return Promise.race([sendFn(payload, fpath), deadline])
      .finally(() => { if (timer) clearTimeout(timer); });
  }

  function deadLetter(fpath, f, reason, errMsg, attempts) {
    try {
      fs.mkdirSync(deadLetterDir, { recursive: true });
      const target = path.join(deadLetterDir, f);
      try {
        fs.renameSync(fpath, target);
      } catch {
        // rename() fails across filesystem boundaries — fall back to copy+unlink
        // so a bind-mounted outbox still drains instead of retrying forever.
        fs.copyFileSync(fpath, target);
        fs.unlinkSync(fpath);
      }
      // Sidecar with the diagnosis: the envelope itself stays byte-identical so
      // it can be re-queued by moving it back once the cause is fixed.
      try {
        fs.writeFileSync(`${target}.reason.json`, JSON.stringify({
          reason, error: errMsg, attempts, channel,
          dead_lettered_at: new Date().toISOString(),
        }, null, 2));
      } catch { /* sidecar is diagnostics-only — never block the move */ }
      if (logger) {
        logger(`outbox: dead-lettered ${f} after ${attempts} attempt(s) ` +
               `(${reason}): ${errMsg}`);
      }
      return true;
    } catch (e) {
      if (logger) logger(`outbox: dead-letter move failed for ${f}: ${e.message}`);
      return false;
    }
  }

  async function tick() {
    let files;
    try {
      files = fs.readdirSync(outboxDir).filter((f) => f.endsWith('.json')).sort();
    } catch {
      return;
    }
    for (const f of files) {
      if (preCheck && !preCheck()) {
        if (!precheckFailSince) precheckFailSince = Date.now();
        const stalledMs = Date.now() - precheckFailSince;
        if (stallWarnMs > 0 && stalledMs > stallWarnMs) {
          const now = Date.now();
          if (logger && now - lastPrecheckLog >= LOG_DEDUP_MS) {
            lastPrecheckLog = now;
            logger(`outbox: preCheck has been blocking delivery for ` +
                   `${Math.round(stalledMs / 1000)}s — ${files.length} file(s) ` +
                   `queued but nothing is being sent`);
          }
        }
        return;
      }
      precheckFailSince = 0;
      const fpath = path.join(outboxDir, f);
      // Already sent in a previous tick but unlink() failed — retry the
      // unlink only; do NOT re-send. Only clear the guard when the file is
      // actually gone so a second failed unlink doesn't reopen the send path.
      if (_sentOnce.has(fpath)) {
        let retryUnlinked = false;
        try { fs.unlinkSync(fpath); retryUnlinked = true; } catch {}
        if (retryUnlinked) _sentOnce.delete(fpath);
        continue;
      }
      let payload;
      try {
        payload = JSON.parse(fs.readFileSync(fpath, 'utf8'));
      } catch (e) {
        // Dead-letter, do NOT unlink: this file is a FINISHED ANSWER the engine
        // already produced and paid for. Unparseable JSON is usually a truncated
        // write (crash mid-write, disk full), and the text is often still in there
        // — recoverable by hand from the dead-letter dir, unrecoverable once
        // deleted. Falls back to unlink only when there is no dead-letter dir,
        // because leaving it in place would retry the same parse every tick.
        if (deadLetterDir) {
          if (!deadLetter(fpath, f, 'unparseable envelope', e.message, 1)) {
            try { fs.unlinkSync(fpath); } catch {}
          }
        } else {
          if (logger) logger(`outbox: bad JSON in ${f}, dropping: ${e.message}`);
          try { fs.unlinkSync(fpath); } catch {}
        }
        continue;
      }
      // Strict: missing `channel` is a writer bug — drop instead of
      // silently routing to a default channel (the old `|| 'whatsapp'`
      // fallback could deliver Telegram-bound messages to a WhatsApp
      // account if channel was forgotten somewhere).
      if (!payload.channel) {
        // Same reasoning as bad JSON: a writer bug must not cost the user their
        // answer. Dead-letter it so the envelope can be re-queued once the missing
        // field is added, instead of deleting the only copy.
        if (deadLetterDir) {
          if (!deadLetter(fpath, f, "missing 'channel' field", 'writer bug', 1)) {
            try { fs.unlinkSync(fpath); } catch {}
          }
        } else {
          if (logger) logger(`outbox: missing 'channel' field in ${f}, dropping`);
          try { fs.unlinkSync(fpath); } catch {}
        }
        continue;
      }
      if (payload.channel !== channel) continue;
      try {
        const _t0 = Date.now();
        await sendWithTimeout(payload, fpath);
        // Log the SUCCESS, not only the failures. Without this line a delivered
        // message and a silently dropped one look exactly the same in the journal:
        // verifying a single Discord round-trip on 2026-07-26 meant querying the
        // Discord REST API afterwards, because the daemon had gone quiet for two
        // hours and there was no way to tell delivery from a silent drop. This repo
        // has been burned twice by that ambiguity (the 2026-07-25 silent drop and
        // the 2026-07-26 wedged poller). One line per delivered message is cheap —
        // the channels are rate-limited to tens of messages an hour.
        if (logger) {
          logger(`outbox: sent ${f} to ${channel} in ${Date.now() - _t0}ms`);
        }
        _sentOnce.add(fpath);         // mark before unlink so a failed unlink is detected next tick
        let unlinked = false;
        try { fs.unlinkSync(fpath); unlinked = true; } catch {}
        if (unlinked) _sentOnce.delete(fpath); // only clear guard when file is actually gone
        // If unlink failed the file stays in the outbox; _sentOnce keeps the
        // entry so the next tick knows the message was already sent and only
        // retries the unlink (no re-send → prevents duplicate Discord messages).
        _lastFailLog.delete(fpath);
        _attempts.delete(fpath);
      } catch (e) {
        logSendFailure(fpath, f, e.message);
        const attempts = (_attempts.get(fpath) || 0) + 1;
        _attempts.set(fpath, attempts);
        // File bleibt → nextr Tick versucht es erneut, es sei denn der Fehler ist
        // dauerhaft (oder das Retry-Budget ist aufgebraucht) und eine
        // Dead-Letter-Ablage ist konfiguriert.
        if (deadLetterDir) {
          let permanent = false;
          try { permanent = isPermanent ? isPermanent(e) === true : false; } catch { permanent = false; }
          if (permanent || attempts >= maxAttempts) {
            const reason = permanent ? 'permanent send error' : `${maxAttempts} attempts exhausted`;
            if (deadLetter(fpath, f, reason, e.message, attempts)) {
              _lastFailLog.delete(fpath);
              _attempts.delete(fpath);
            }
          }
        }
      }
    }
    // Keep the bookkeeping maps bounded: drop entries for files no longer present.
    if (_lastFailLog.size || _attempts.size) {
      const present = new Set(files.map((f) => path.join(outboxDir, f)));
      for (const k of _lastFailLog.keys()) if (!present.has(k)) _lastFailLog.delete(k);
      for (const k of _attempts.keys()) if (!present.has(k)) _attempts.delete(k);
    }
  }

  const handle = setInterval(() => {
    if (running) {
      // A tick that outlives stallWarnMs is a bug, not a slow send — say so.
      // This is the log line whose absence made the 2026-07-26 outage
      // invisible: the poller was dead for 38 minutes without a single
      // journal entry while replies queued up behind it.
      const stalledMs = Date.now() - runningSince;
      if (stallWarnMs > 0 && stalledMs > stallWarnMs) {
        const now = Date.now();
        if (logger && now - lastStallLog >= LOG_DEDUP_MS) {
          lastStallLog = now;
          logger(`outbox: tick stalled for ${Math.round(stalledMs / 1000)}s ` +
                 `— nothing is being delivered`);
        }
        // Backstop for a hang that escapes sendWithTimeout (anything awaited
        // outside sendFn). Releasing the flag lets the next tick proceed; the
        // orphaned tick keeps running but can no longer wedge the poller shut.
        // The _sentOnce guard still prevents a re-send of anything the
        // orphaned tick already delivered.
        if (stallResetMs > 0 && stalledMs > stallResetMs) {
          if (logger) {
            logger(`outbox: force-releasing stalled tick after ` +
                   `${Math.round(stalledMs / 1000)}s — resuming delivery`);
          }
          running = false;
          runningSince = 0;
        }
      }
      return;
    }
    running = true;
    runningSince = Date.now();
    Promise.resolve().then(tick)
      .catch((e) => { if (logger) logger(`outbox tick error: ${e.message}`); })
      .finally(() => { running = false; runningSince = 0; lastTickEnd = Date.now(); });
  }, intervalMs);

  return {
    stop: () => clearInterval(handle),
    // Health surface: lets a daemon's /status expose "the poller is wedged"
    // so an external watchdog can act on it. A live process with an open
    // gateway socket is NOT proof that anything is being delivered.
    stats: () => ({
      running,
      stalled_s: running && runningSince
        ? Math.round((Date.now() - runningSince) / 1000)
        : 0,
      idle_s: Math.round((Date.now() - lastTickEnd) / 1000),
      // > 0 means preCheck (e.g. `client.isReady()`) has been refusing
      // delivery for that many seconds — the blind spot `stalled_s` cannot
      // see (incident 2026-07-27). 0 means preCheck is passing or unused.
      precheck_stalled_s: precheckFailSince
        ? Math.round((Date.now() - precheckFailSince) / 1000)
        : 0,
    }),
  };
}

/**
 * Count envelopes in the SHARED outbox directory belonging to `channel`.
 *
 * The directory is shared by every bridge daemon, so a naive
 * `readdirSync(outboxDir).length` counts every channel's backlog as if it
 * were the caller's own — on 2026-07-27 whatsapp, discord and email all
 * reported `pending_outbox: 5` in their /status while 100% of the 5 files
 * were `channel: "discord"`, wasting incident-response time checking two
 * bridges that were never affected. Unparseable/channel-less files are not
 * counted for any channel — they show up in the dead-letter path instead.
 *
 * @param {string} outboxDir
 * @param {string} channel
 * @returns {number}
 */
function countPending(outboxDir, channel) {
  let files;
  try {
    files = fs.readdirSync(outboxDir).filter((f) => f.endsWith('.json'));
  } catch {
    return 0;
  }
  let n = 0;
  for (const f of files) {
    try {
      const payload = JSON.parse(fs.readFileSync(path.join(outboxDir, f), 'utf8'));
      if (payload.channel === channel) n++;
    } catch {
      // unparseable — not attributable to any channel
    }
  }
  return n;
}

module.exports = { startOutboxPoller, countPending };
