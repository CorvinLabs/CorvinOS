// auth.js — Whitelist + Rate-Limit + PIN-Auth, channel-agnostisch.
//
// Faktorisiert die ~30 Zeilen authOk/rateAllow, die in jedem daemon
// nahezu identisch wiederholt waren.

const fs = require('fs');
const crypto = require('crypto');

// PIN brute-force lockout (review 2026-11-19, R3-NEW-12). `/auth <pin>` used
// to be guessable without any throttle. After PIN_MAX_FAILURES wrong PINs
// inside PIN_FAILURE_WINDOW_MS a sender is locked out for PIN_LOCKOUT_MS;
// while locked, the PIN is not compared at all (a correct guess is refused
// too) and the lockout is audited at most once per lockout window.
const PIN_MAX_FAILURES = 5;
const PIN_FAILURE_WINDOW_MS = 15 * 60 * 1000;
const PIN_LOCKOUT_MS = 15 * 60 * 1000;
// Bound on the per-sender bookkeeping maps (rate limit + PIN failures). An
// unknown sender is attacker-chosen input, so an unbounded map is a memory
// leak anyone can drive (R3-NEW-14).
const MAX_TRACKED_SENDERS = 10000;
const DEFAULT_RATE_PER_HOUR = 60;

// Constant-time string comparison. Both sides are hashed first so the
// comparison length never depends on the secret's length.
function safeEqual(a, b) {
  const ha = crypto.createHash('sha256').update(String(a), 'utf8').digest();
  const hb = crypto.createHash('sha256').update(String(b), 'utf8').digest();
  return crypto.timingSafeEqual(ha, hb);
}

// Audit (optional). When voice-audit is missing or the chain library
// is unavailable, auditEventSync is a no-op — auth never blocks on it.
let _auditSync = null;
try {
  _auditSync = require('./audit').auditEventSync;
} catch {
  _auditSync = null;
}
function _audit(eventType, opts) {
  if (typeof _auditSync !== 'function') return;
  try { _auditSync(eventType, opts); } catch { /* fire and forget */ }
}

let _audience = null;
function audienceFor(settingsFile, chatKey) {
  if (!chatKey) return 'owner';
  if (!_audience) {
    try { _audience = require('./in_chat_commands').getAudience; }
    catch { _audience = () => 'owner'; }
  }
  try { return _audience(settingsFile, String(chatKey)); }
  catch { return 'owner'; }
}

// Keep a bookkeeping map bounded: drop the oldest entries (Map iteration
// order is insertion order; a touched key is re-inserted, so this is LRU).
function boundMap(map, max = MAX_TRACKED_SENDERS) {
  while (map.size > max) {
    map.delete(map.keys().next().value);
  }
}

/**
 * PIN brute-force guard, shared by makeAuth() and the WhatsApp daemon's own
 * authOk(). Usage, in this order:
 *   if (guard.isLocked(uid, chatKey)) deny;      // BEFORE comparing the PIN
 *   if (safeEqual(given, pin)) { guard.clear(uid); accept; }
 *   guard.fail(uid); deny;
 * `onLockout(uid, chatKey)` fires at most once per lockout window.
 */
function makePinGuard({ now, onLockout } = {}) {
  const clock = typeof now === 'function' ? now : Date.now;
  // uid -> { count, first, lockedUntil, notified }
  const fails = new Map();

  function isLocked(uid, chatKey) {
    const t = clock();
    const st = fails.get(uid);
    if (!st) return false;
    if (st.lockedUntil && t < st.lockedUntil) {
      if (!st.notified) {
        st.notified = true;
        if (typeof onLockout === 'function') {
          try { onLockout(uid, chatKey); } catch { /* never block auth */ }
        }
      }
      return true;
    }
    if (st.lockedUntil || t - st.first >= PIN_FAILURE_WINDOW_MS) {
      fails.delete(uid);  // lockout served / failure window expired
    }
    return false;
  }

  function fail(uid) {
    const t = clock();
    let st = fails.get(uid);
    fails.delete(uid);
    if (!st || t - st.first >= PIN_FAILURE_WINDOW_MS) {
      st = { count: 0, first: t, lockedUntil: 0, notified: false };
    }
    st.count += 1;
    if (st.count >= PIN_MAX_FAILURES) st.lockedUntil = t + PIN_LOCKOUT_MS;
    fails.set(uid, st);
    boundMap(fails);
  }

  function clear(uid) { fails.delete(uid); }

  return { isLocked, fail, clear, size: () => fails.size };
}

/**
 * @param {object} cfg
 * @param {string} cfg.settingsFile        — path zu bridges/<channel>/settings.json
 * @param {function} cfg.currentSettings   — Hot-Reload-Accessor
 * @param {function} cfg.loadSettings      — Boot-Snapshot-Loader (nicht-cached)
 * @param {function} [cfg.logger]          — log-function
 * @param {function} [cfg.normalize]       — optionaler ID-Normalizer (WhatsApp-JID)
 * @param {string}   [cfg.channel]         — channel name for audit events
 *                                            (e.g. 'discord'); defaults to ''
 * @param {boolean}  [cfg.denyOnEmptyWhitelist] — PENTEST-3c: fail-CLOSED when
 *                                            the whitelist is empty. On public
 *                                            channels (email) an empty
 *                                            whitelist would otherwise make the
 *                                            whole internet an owner. When set,
 *                                            an empty whitelist denies everyone
 *                                            (the owner still claims access via
 *                                            the PIN `/auth <pin>` path, which
 *                                            keeps working below). A `dev_mode:
 *                                            true` setting re-opens the legacy
 *                                            empty-whitelist=owner behaviour for
 *                                            local testing only.
 * @param {boolean}  [cfg.lockFirstSender] — 2026-08-02: for DM-style channels
 *                                            (not fully public like email, but
 *                                            not yet safe to leave open forever
 *                                            either — Telegram/Slack/Teams).
 *                                            The legacy `denyOnEmptyWhitelist:
 *                                            false` default keeps EVERY sender
 *                                            authorized indefinitely as long as
 *                                            the operator never populates
 *                                            `whitelist` — Discord and WhatsApp
 *                                            had (and fixed) this exact gap
 *                                            with bespoke per-bridge ownership
 *                                            locks. This is the shared
 *                                            equivalent: when set, the FIRST
 *                                            sender on an empty whitelist is
 *                                            persisted into `whitelist` (same
 *                                            write path as the PIN-auth claim
 *                                            below), so every later call sees
 *                                            a non-empty whitelist and denies
 *                                            anyone not in it — converging
 *                                            through the existing whitelist
 *                                            check rather than a second,
 *                                            parallel state machine. Mutually
 *                                            exclusive with denyOnEmptyWhitelist
 *                                            (fail-closed already means no
 *                                            first-sender claim is possible).
 */
function makeAuth({ settingsFile, currentSettings, loadSettings, logger, normalize, channel, denyOnEmptyWhitelist, lockFirstSender, now }) {
  const nrm = normalize || ((x) => String(x));
  const rateMap = new Map();
  // Injectable clock (tests); defaults to the wall clock.
  const clock = typeof now === 'function' ? now : Date.now;
  const pinGuard = makePinGuard({
    now: clock,
    onLockout: (u, chatKey) => {
      if (logger) logger(`auth: PIN lockout for ${u}`);
      _audit('bridge.pin_failure', {
        channel: channel || '', user: u, chatKey: chatKey || '',
        details: { reason: 'lockout' },
      });
    },
  });
  const ch = channel || '';
  const denyEmpty = !!denyOnEmptyWhitelist;
  const lockFirst = !!lockFirstSender && !denyEmpty;

  // PENTEST-3c: is an empty whitelist allowed to mean "everyone is owner"?
  // Legacy DEV/first-run behaviour keeps this true. Fail-closed channels flip
  // it to false unless the operator explicitly opts into `dev_mode`.
  function emptyWhitelistIsOwner(cs) {
    if (!denyEmpty) return true;          // legacy channels: first-run claim
    return cs && cs.dev_mode === true;    // fail-closed: only with explicit dev_mode
  }
  // First-drop tracker per (chatKey, uid) — keeps the silent-drop loud only
  // once per daemon-life. Restarts re-arm the polite "🔒 read-only" ACK; that
  // is acceptable because daemon restarts are rare and the ACK is harmless.
  const _roSeen = new Set();

  /**
   * Tri-state classification helper. Returns one of:
   *   'owner'      — user is on the whitelist, full access.
   *   'guest'      — user is NOT on the whitelist but the chat is open
   *                  (`audience=all`, `/all on`): may talk to the bot, has
   *                  NO owner surface (/vault, /all, /debug, /invite, ...).
   *   'read_only'  — user is on the read_only list (and NOT on whitelist),
   *                  may be in the chat but must NOT trigger the bot.
   *   'unknown'    — user is on neither list; legacy whitelist_deny applies.
   *
   * Resolution order: whitelist beats read_only (so an operator who appears
   * on both lists keeps owner privileges). Empty whitelist => DEV mode,
   * everyone classifies as 'owner' (mirrors authOk's legacy fail-open).
   * audience='all' on the chat profile classifies a non-whitelisted sender
   * as 'guest' (chat is open to anyone), bypassing read_only on that chat.
   * It never grants 'owner': until 2026-11-19 it did, and every participant
   * of a `/all on` chat reached /vault and the rest of the owner surface.
   */
  function classify(uid, chatKey) {
    const cs = currentSettings();
    const wl = (cs.whitelist || []).map(nrm);
    if (wl.length === 0) return emptyWhitelistIsOwner(cs) ? 'owner' : 'unknown';
    const u = nrm(uid);
    if (wl.includes(u)) return 'owner';
    if (chatKey && audienceFor(settingsFile, chatKey) === 'all') return 'guest';
    const ro = (cs.read_only || []).map(nrm);
    if (ro.includes(u)) return 'read_only';
    return 'unknown';
  }

  /**
   * Owner check for the command surface. Whitelist membership ONLY — the
   * per-chat audience is deliberately not consulted (chatKey = null), so a
   * guest of an open chat is never an owner. Daemons pass this as `isOwner`
   * to every command dispatch; `authOk` only answers "may this sender reach
   * the bot at all".
   */
  function isOwner(uid) {
    return classify(uid, null) === 'owner';
  }

  /**
   * Read-only gate. Call BEFORE authOk. When the sender is on the
   * `read_only` list (and NOT also on `whitelist`), this returns
   * { isReadOnly: true, firstDrop: <bool> } and emits a
   * `bridge.read_only_drop` audit event. The daemon should then send a
   * polite one-time ACK on `firstDrop` and short-circuit the message
   * without writing to the inbox.
   *
   * Returns { isReadOnly: false } for everyone else (owner OR unknown);
   * the daemon proceeds to its normal authOk path.
   */
  function readOnlyOk(uid, text, chatKey) {
    const role = classify(uid, chatKey);
    if (role !== 'read_only') return { isReadOnly: false };
    const u = nrm(uid);
    const seenKey = `${chatKey || ''}::${u}`;
    const firstDrop = !_roSeen.has(seenKey);
    if (firstDrop) _roSeen.add(seenKey);
    if (logger) logger(`auth: read_only drop ${u} chat=${chatKey || ''} first=${firstDrop}`);
    // F-B10 (2026-09-07): the audit record carries the message LENGTH only —
    // never a snippet. A read-only sender's text is user content (GDPR Art. 5
    // data minimisation) and the hash chain is permanent.
    _audit('bridge.read_only_drop', {
      channel: ch, user: u, chatKey: chatKey || '',
      details: {
        first_drop: firstDrop,
        text_len: (text || '').toString().length,
      },
    });
    return { isReadOnly: true, firstDrop };
  }

  function rateAllow(uid, perHour) {
    const now = clock();
    const HOUR = 3600 * 1000;
    // A missing / non-numeric limit used to compare `n >= undefined` (never
    // true) while still booking a timestamp: no limit, but a growing map.
    if (!(Number.isFinite(perHour) && perHour > 0)) perHour = DEFAULT_RATE_PER_HOUR;
    const arr = (rateMap.get(uid) || []).filter((t) => now - t < HOUR);
    rateMap.delete(uid);  // re-insert below => most recently used is last
    if (arr.length >= perHour) {
      rateMap.set(uid, arr);
      boundMap(rateMap);
      _audit('bridge.rate_limit_exceeded', {
        channel: ch, user: nrm(uid),
        details: { per_hour: perHour, observed: arr.length },
      });
      return false;
    }
    arr.push(now);
    rateMap.set(uid, arr);
    boundMap(rateMap);
    return true;
  }

  function authOk(uid, text, chatKey) {
    const cs = currentSettings();
    const wl = (cs.whitelist || []).map(nrm);
    if (wl.length === 0 && emptyWhitelistIsOwner(cs)) {
      const u0 = nrm(uid);
      if (lockFirst) {
        // First sender on an empty whitelist claims it, permanently (until an
        // operator edits whitelist by hand) — persisted via the SAME
        // read-modify-write the PIN-auth claim below uses, so every later
        // call sees a non-empty whitelist and the existing `wl.includes(u)`
        // check below denies anyone else. No separate state machine, no
        // in-memory-only flag that resets on restart.
        const cur = loadSettings();
        cur.whitelist = (cur.whitelist || []).concat([u0]);
        const tmp = settingsFile + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(cur, null, 2), { mode: 0o600 });  // F-B7: credentials file
        fs.renameSync(tmp, settingsFile);
        if (logger) logger(`auth: empty whitelist, locking to first sender ${u0}`);
        _audit('bridge.login', {
          channel: ch, user: u0, chatKey: chatKey || '',
          details: { method: 'first_sender_lock' },
        });
        return true;
      }
      // Legacy first-run / DEV: empty whitelist means "no owner claimed yet",
      // so the first sender is trusted. Fail-closed channels (email) skip this
      // and fall through — an empty whitelist there denies everyone except a
      // valid PIN claim below.
      if (logger) logger(`auth: DEV mode, accepting ${uid}`);
      return true;
    }
    const u = nrm(uid);
    if (wl.includes(u)) return true;
    // Per-chat audience override: if the owner has flipped this chat to
    // `audience=all` via /all on, anyone in that chat may reach the bot.
    // Bot-self / external-bot filters in the daemon still apply.
    if (chatKey && audienceFor(settingsFile, chatKey) === 'all') {
      if (logger) logger(`auth: audience=all in ${chatKey}, accepting ${u}`);
      return true;
    }
    // PIN-Auth: User schickt "/auth <pin>" → wenn Match, in Whitelist aufnehmen.
    if (cs.pin && text && text.trim().startsWith('/auth ')) {
      const given = text.trim().slice(6).trim();
      // Lockout BEFORE the comparison: a locked-out sender gets no oracle,
      // not even for the correct PIN, and costs no further audit record.
      if (pinGuard.isLocked(u, chatKey)) return false;
      if (safeEqual(given, cs.pin)) {
        pinGuard.clear(u);
        const cur = loadSettings();
        cur.whitelist = (cur.whitelist || []).concat([u]);
        // Direct write — kein saveSettings() weil der Caller eventuell
        // ein anderes Settings-Objekt mutated. Atomic-Pattern hier inline.
        const tmp = settingsFile + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(cur, null, 2), { mode: 0o600 });  // F-B7: credentials file
        fs.renameSync(tmp, settingsFile);
        if (logger) logger(`auth: PIN ok, added ${u}`);
        _audit('bridge.login', {
          channel: ch, user: u, chatKey: chatKey || '',
          details: { method: 'pin' },
        });
        return true;
      }
      // wrong PIN — count it, then deny
      pinGuard.fail(u);
      _audit('bridge.pin_failure', {
        channel: ch, user: u, chatKey: chatKey || '',
        details: {},
      });
      if (logger) logger(`auth: PIN rejected for ${u}`);
      return false;
    }
    if (logger) logger(`auth: rejected ${u}`);
    _audit('bridge.whitelist_deny', {
      channel: ch, user: u, chatKey: chatKey || '',
      details: { reason: 'not in whitelist' },
    });
    return false;
  }

  return { rateAllow, authOk, readOnlyOk, classify, isOwner, _rateMapSize: () => rateMap.size };
}

module.exports = {
  makeAuth, makePinGuard, safeEqual, boundMap,
  PIN_MAX_FAILURES, PIN_LOCKOUT_MS, PIN_FAILURE_WINDOW_MS, MAX_TRACKED_SENDERS,
};
