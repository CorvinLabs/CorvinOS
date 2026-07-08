// auth.js — Whitelist + Rate-Limit + PIN-Auth, channel-agnostisch.
//
// Faktorisiert die ~30 Zeilen authOk/rateAllow, die in jedem daemon
// nahezu identisch wiederholt waren.

const fs = require('fs');

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
 */
function makeAuth({ settingsFile, currentSettings, loadSettings, logger, normalize, channel, denyOnEmptyWhitelist }) {
  const nrm = normalize || ((x) => String(x));
  const rateMap = new Map();
  const ch = channel || '';
  const denyEmpty = !!denyOnEmptyWhitelist;

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
   *   'read_only'  — user is on the read_only list (and NOT on whitelist),
   *                  may be in the chat but must NOT trigger the bot.
   *   'unknown'    — user is on neither list; legacy whitelist_deny applies.
   *
   * Resolution order: whitelist beats read_only (so an operator who appears
   * on both lists keeps owner privileges). Empty whitelist => DEV mode,
   * everyone classifies as 'owner' (mirrors authOk's legacy fail-open).
   * audience='all' on the chat profile also classifies the sender as
   * 'owner' (chat is open to anyone), bypassing read_only on that chat.
   */
  function classify(uid, chatKey) {
    const cs = currentSettings();
    const wl = (cs.whitelist || []).map(nrm);
    if (wl.length === 0) return emptyWhitelistIsOwner(cs) ? 'owner' : 'unknown';
    const u = nrm(uid);
    if (wl.includes(u)) return 'owner';
    if (chatKey && audienceFor(settingsFile, chatKey) === 'all') return 'owner';
    const ro = (cs.read_only || []).map(nrm);
    if (ro.includes(u)) return 'read_only';
    return 'unknown';
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
    const snippet = (text || '').toString().slice(0, 200);
    _audit('bridge.read_only_drop', {
      channel: ch, user: u, chatKey: chatKey || '',
      details: {
        first_drop: firstDrop,
        snippet,
        truncated: (text || '').toString().length > 200,
      },
    });
    return { isReadOnly: true, firstDrop };
  }

  function rateAllow(uid, perHour) {
    const now = Date.now();
    const HOUR = 3600 * 1000;
    const arr = (rateMap.get(uid) || []).filter((t) => now - t < HOUR);
    if (arr.length >= perHour) {
      rateMap.set(uid, arr);
      _audit('bridge.rate_limit_exceeded', {
        channel: ch, user: nrm(uid),
        details: { per_hour: perHour, observed: arr.length },
      });
      return false;
    }
    arr.push(now);
    rateMap.set(uid, arr);
    return true;
  }

  function authOk(uid, text, chatKey) {
    const cs = currentSettings();
    const wl = (cs.whitelist || []).map(nrm);
    if (wl.length === 0 && emptyWhitelistIsOwner(cs)) {
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
      if (given === cs.pin) {
        const cur = loadSettings();
        cur.whitelist = (cur.whitelist || []).concat([u]);
        // Direct write — kein saveSettings() weil der Caller eventuell
        // ein anderes Settings-Objekt mutated. Atomic-Pattern hier inline.
        const tmp = settingsFile + '.tmp';
        fs.writeFileSync(tmp, JSON.stringify(cur, null, 2));
        fs.renameSync(tmp, settingsFile);
        if (logger) logger(`auth: PIN ok, added ${u}`);
        _audit('bridge.login', {
          channel: ch, user: u, chatKey: chatKey || '',
          details: { method: 'pin' },
        });
        return true;
      }
      // wrong PIN — fall through to deny
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

  return { rateAllow, authOk, readOnlyOk, classify };
}

module.exports = { makeAuth };
