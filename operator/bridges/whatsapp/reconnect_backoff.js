// reconnect_backoff.js — pure reconnect-delay calculation for daemon.js.
//
// Extracted into its own module (2026-07-30) so it can be unit tested
// without a live WhatsApp connection — an earlier pass added a detailed
// comment describing exponential backoff directly in daemon.js but never
// actually wired it into the reconnect call (found by an adversarial
// re-review), and the gap went unnoticed exactly because there was no test
// exercising this specific calculation. See daemon.js's own history: a
// fixed 1s retry, forever, on every non-logout close (including a
// persistent "unknown" reason) hammered WhatsApp's servers indefinitely —
// a real ban-risk DoS against WhatsApp's own infrastructure.

const BASE_MS = 1000;
const CAP_MS = 60000;
const FAST_RECONNECT_MS = 1000; // code 515 (restartRequired) — expected mid-pairing close

/**
 * Computes the delay before the next reconnect attempt.
 *
 * @param {number|undefined} reason - Baileys DisconnectReason status code.
 * @param {number} attemptsBefore - reconnect attempts so far THIS
 *   disconnected streak (0 on the first attempt after a fresh disconnect).
 * @param {function} [randomFn] - injectable RNG for deterministic tests
 *   (defaults to Math.random).
 * @returns {{delayMs: number, attemptsAfter: number}} delayMs to wait, and
 *   the attempt counter to carry into the NEXT call. Code 515 does not
 *   increment the counter — it's not a failure, just the normal
 *   mid-pairing handshake, and must stay fast every time it recurs.
 */
function computeReconnectDelay(reason, attemptsBefore, randomFn) {
  const rand = randomFn || Math.random;
  if (reason === 515) {
    return { delayMs: FAST_RECONNECT_MS, attemptsAfter: attemptsBefore };
  }
  const attemptsAfter = attemptsBefore + 1;
  const base = Math.min(BASE_MS * (2 ** (attemptsAfter - 1)), CAP_MS);
  const jitter = Math.floor(rand() * Math.min(BASE_MS, base));
  return { delayMs: base + jitter, attemptsAfter };
}

module.exports = { computeReconnectDelay, BASE_MS, CAP_MS, FAST_RECONNECT_MS };
