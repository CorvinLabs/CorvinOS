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
 * @returns {{stop: function}}          — handle mit stop() zum Cleanup
 */
function startOutboxPoller({
  outboxDir, channel, sendFn, preCheck, logger, intervalMs = 500,
  deadLetterDir = null, isPermanent = null, maxAttempts = 10,
}) {
  let running = false;
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
      if (preCheck && !preCheck()) return;
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
        if (logger) logger(`outbox: bad JSON in ${f}: ${e.message}`);
        try { fs.unlinkSync(fpath); } catch {}
        continue;
      }
      // Strict: missing `channel` is a writer bug — drop instead of
      // silently routing to a default channel (the old `|| 'whatsapp'`
      // fallback could deliver Telegram-bound messages to a WhatsApp
      // account if channel was forgotten somewhere).
      if (!payload.channel) {
        if (logger) logger(`outbox: missing 'channel' field in ${f}, dropping`);
        try { fs.unlinkSync(fpath); } catch {}
        continue;
      }
      if (payload.channel !== channel) continue;
      try {
        await sendFn(payload, fpath);
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
    if (running) return;
    running = true;
    Promise.resolve().then(tick)
      .catch((e) => { if (logger) logger(`outbox tick error: ${e.message}`); })
      .finally(() => { running = false; });
  }, intervalMs);

  return { stop: () => clearInterval(handle) };
}

module.exports = { startOutboxPoller };
