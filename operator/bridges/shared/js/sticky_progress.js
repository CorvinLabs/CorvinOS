// sticky_progress.js — shared "sticky progress message" + finalize-guard
// primitive, factored out of the Discord daemon so every messenger bridge
// gets the same dedup/finalization semantics instead of re-inventing it
// (or, historically, not having it at all).
//
// Problem this solves (see adapter.py's `_emit_status`, ~L9319): the adapter
// writes `_progress` / `_heartbeat` outbox payloads while a turn is still
// running, in addition to the final real-reply payload. Naively forwarding
// every one of those as a brand-new channel message spams the chat with a
// message-per-tool-call and — worse — the outbox dir is processed in
// alphabetical order, so `{msg_id}_00.json` (the real reply) can sort BEFORE
// `{msg_id}_hb.json` / `{msg_id}_sNN.json` (heartbeat/progress). Without a
// guard, a late progress/heartbeat file can land in the chat AFTER the real
// answer already shipped, reading as "the agent is talking to itself".
//
// This module provides the two pieces every daemon needs to avoid that,
// independent of the platform's actual send/edit/delete API:
//
//   1. A "sticky slot" per chat: the first _progress/_heartbeat payload for
//      a turn opens the slot (send once, remember a platform-specific
//      "ref" — message object, message id, timestamp, whatever the caller
//      needs to edit/delete later); every subsequent _progress payload for
//      the same chat is expected to EDIT that same message in place instead
//      of sending a new one. The caller does the actual edit/send/delete
//      I/O — this module only tracks the ref.
//   2. A "finalized" TTL-map keyed by msg_id: once the real reply for a
//      turn has been delivered, any _progress/_heartbeat file we still see
//      for that same msg_id is stale and must be dropped silently.
//
// No platform I/O happens in here on purpose — Discord edits a Message
// object, Telegram/Slack edit by (chat_id, message_id)/(channel, ts),
// WhatsApp (Baileys) edits via a message key, Signal edits via
// edit_timestamp, Teams edits via TurnContext.updateActivity(activityId).
// Each daemon supplies its own edit/delete callbacks; this module is just
// the bookkeeping both of them share.

'use strict';

/**
 * @param {object}  [cfg]
 * @param {number}  [cfg.ttlMs=60000]      — how long a finalized msg_id is
 *                                           remembered before the stale-drop
 *                                           guard forgets about it.
 * @param {number}  [cfg.sweepMs=30000]    — cleanup-sweep interval for the
 *                                           finalized-map TTL eviction.
 * @returns {{
 *   isFinalized: function(string): boolean,
 *   markFinalized: function(string): void,
 *   hasProgress: function(string): boolean,
 *   getProgress: function(string): *,
 *   setProgress: function(string, *): void,
 *   clearProgress: function(string): void,
 *   stop: function(): void,
 * }}
 */
function makeStickyProgress({ ttlMs = 60_000, sweepMs = 30_000 } = {}) {
  // chatKey → arbitrary platform ref (Message object, {messageId}, {ts}, …).
  const progressByChat = new Map();
  // msg_id → finalizedAt-ts. TTL-bounded so long-running daemons don't leak.
  const finalizedMsgIds = new Map();

  function isFinalized(msgId) {
    if (!msgId) return false;
    const ts = finalizedMsgIds.get(String(msgId));
    return !!ts && (Date.now() - ts) < ttlMs;
  }
  function markFinalized(msgId) {
    if (!msgId) return;
    finalizedMsgIds.set(String(msgId), Date.now());
  }
  function hasProgress(chatKey) {
    return progressByChat.has(chatKey);
  }
  function getProgress(chatKey) {
    return progressByChat.get(chatKey);
  }
  function setProgress(chatKey, ref) {
    progressByChat.set(chatKey, ref);
  }
  function clearProgress(chatKey) {
    progressByChat.delete(chatKey);
  }

  const sweepHandle = setInterval(() => {
    const cutoff = Date.now() - ttlMs;
    for (const [k, ts] of finalizedMsgIds.entries()) {
      if (ts < cutoff) finalizedMsgIds.delete(k);
    }
  }, sweepMs);
  // Don't keep the process alive just for this sweep (matches the previous
  // Discord-only `.unref?.()` behavior); tests can still call stop().
  sweepHandle.unref?.();

  function stop() {
    clearInterval(sweepHandle);
  }

  return {
    isFinalized, markFinalized,
    hasProgress, getProgress, setProgress, clearProgress,
    stop,
  };
}

module.exports = { makeStickyProgress };
