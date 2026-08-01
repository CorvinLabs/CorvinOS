'use strict';
// Event-loop freeze watchdog — cross-platform bridge auto-recovery.
//
// The reported failure (2026-08-01, Windows): a bridge daemon's event loop
// wedges — the process stays alive, its /status port is open but unresponsive,
// no Discord events are processed, and NOTHING restarts it. On Linux/macOS the
// external watchdog.sh (60 s systemd timer) catches an unresponsive /status and
// restarts. On Windows the Scheduled Task only restarts on process EXIT, so a
// hang (no exit) never recovers.
//
// This closes that gap on BOTH platforms with NO external dependency: a
// worker_threads Worker (which keeps ticking on its OWN thread even when the
// MAIN event loop is fully blocked) watches a heartbeat the main thread posts
// every `beatMs`. If the main loop hasn't beaten for > `thresholdMs`, the worker
// — which shares the process PID — force-terminates the process, and the
// platform's existing restart-on-exit supervisor (systemd Restart=on-failure /
// Windows Scheduled Task) relaunches it.
//
// This deliberately does NOT use "seconds since last Discord event" (idle is not
// frozen). The heartbeat is event-LOOP driven, so a quiet-but-healthy bot never
// trips it. The threshold is generous (60 s default) — any real >60 s main-thread
// block in a bridge is itself a bug, not a slow send.

const WORKER_SRC = `
'use strict';
const { parentPort, workerData } = require('worker_threads');
const { thresholdMs, checkMs } = workerData;
let last = Date.now();
let killing = false;
parentPort.on('message', () => { last = Date.now(); });
setInterval(() => {
  const age = Date.now() - last;
  if (age > thresholdMs && !killing) {
    killing = true;
    try {
      process.stderr.write('[event-loop-watchdog] main loop stalled ' +
        Math.round(age / 1000) + 's (> ' + Math.round(thresholdMs / 1000) +
        's) — SIGKILL for supervisor restart\\n');
    } catch (e) {}
    // SIGKILL, NOT SIGTERM. A wedged loop can't run a graceful handler anyway,
    // and on Linux systemd Restart=on-failure treats a SIGTERM exit as CLEAN
    // (no restart) while SIGKILL → exit 137 → failure → restart. On Windows both
    // map to TerminateProcess → the Scheduled Task's restart-on-exit fires. The
    // outbox is persistent, so a hard kill loses no delivered work (it replays).
    try { process.kill(process.pid, 'SIGKILL'); } catch (e) {}
  }
}, checkMs).unref();
`;

/**
 * Arm the event-loop freeze watchdog.
 * @param {object} [opts]
 * @param {number} [opts.thresholdMs] stall threshold before restart (default 60000, env CORVIN_BRIDGE_WATCHDOG_MS)
 * @param {number} [opts.beatMs]      heartbeat interval (default 2000)
 * @param {number} [opts.checkMs]     worker check interval (default 5000)
 * @param {function} [opts.logger]    log(msg)
 * @returns {{stop: function}|null}   handle, or null if worker_threads unavailable
 */
function startEventLoopWatchdog(opts = {}) {
  const envMs = Number(process.env.CORVIN_BRIDGE_WATCHDOG_MS);
  const thresholdMs = opts.thresholdMs || (Number.isFinite(envMs) && envMs > 0 ? envMs : 60000);
  const beatMs = opts.beatMs || 2000;
  const checkMs = opts.checkMs || 5000;
  const log = opts.logger || (() => {});

  // Explicit opt-out for constrained hosts.
  if (process.env.CORVIN_BRIDGE_WATCHDOG === '0') {
    log('[event-loop-watchdog] disabled via CORVIN_BRIDGE_WATCHDOG=0');
    return null;
  }

  let worker;
  try {
    const { Worker } = require('worker_threads');
    worker = new Worker(WORKER_SRC, { eval: true, workerData: { thresholdMs, checkMs } });
    worker.on('error', (e) => { try { log('[event-loop-watchdog] worker error: ' + e.message); } catch (x) {} });
    worker.unref(); // never keep the process alive just for the watchdog
  } catch (e) {
    log('[event-loop-watchdog] not started (worker_threads unavailable): ' + e.message);
    return null;
  }

  const beat = setInterval(() => {
    try { worker.postMessage(Date.now()); } catch (e) {}
  }, beatMs);
  beat.unref(); // the heartbeat must not keep the process alive on its own

  log('[event-loop-watchdog] armed (threshold ' + Math.round(thresholdMs / 1000) + 's)');
  return {
    stop() {
      clearInterval(beat);
      try { worker.terminate(); } catch (e) {}
    },
  };
}

module.exports = { startEventLoopWatchdog };
