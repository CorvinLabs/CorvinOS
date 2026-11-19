// outbox_paths.js — which local files an outbox envelope may attach.
//
// An outbox envelope names local files (`voice_path`, `image_path`,
// `document_path`, `video_path`, `voice_attachment_path`) and every daemon
// uploaded whatever path it found there. Anything able to drop a JSON file
// into the outbox could therefore make the bridge send ANY file the daemon
// user can read — settings.json with the bot token, the audit chain, an SSH
// key — to a chat (review 2026-11-19, R3-NEW-41).
//
// The adapter writes attachments in exactly two places:
//   - voice notes into the outbox directory itself (synthesize_voice_note),
//   - files a worker produced under its session workdir
//     (<corvin_home>/tenants/<tid>/sessions/..., legacy <corvin_home>/sessions/...).
// A path is accepted only when its REAL path (symlinks resolved) is a regular
// file below one of those roots and no path component is a secret-bearing
// name. Operators with a non-default layout add roots through
// CORVIN_OUTBOX_ATTACHMENT_ROOTS (path-list, os.pathsep separated).
//
// Boundary, stated: this is defence against a planted or malformed envelope,
// not against a process running as the daemon's own OS user that can read
// the files directly.

'use strict';

const fs = require('fs');
const path = require('path');

const ATTACHMENT_KEYS = Object.freeze([
  'voice_path', 'audio_path', 'image_path', 'document_path', 'video_path',
  'voice_attachment_path',
]);

// Never attachable, wherever they live.
const DENIED_COMPONENT_RE = new RegExp([
  '^settings(\\..*)?\\.json$', '^\\.env(\\..*)?$', '^audit.*\\.jsonl$',
  '^ledger\\.jsonl$', '^session_ledger$', '^\\.corvin-ledger$', '^cel_anchors$',
  '^vault$', '^\\.ssh$', '^auth$', '^credentials(\\..*)?$', '^\\.credentials\\.json$',
  '\\.(pem|key|p12|pfx)$', '^id_(rsa|ed25519|ecdsa|dsa)(\\.pub)?$',
].join('|'), 'i');

function _real(p) {
  try { return fs.realpathSync(p); } catch { return null; }
}

function _corvinHome() {
  try { return require('./bridge_paths').corvinHome(); } catch { return null; }
}

/**
 * Real paths of the directories attachments may come from.
 * @param {string} outboxDir       the daemon's outbox directory
 * @param {string[]} [extraRoots]  additional roots (tests, special layouts)
 */
function attachmentRoots(outboxDir, extraRoots) {
  const roots = [];
  const add = (p) => { const r = p && _real(p); if (r && !roots.includes(r)) roots.push(r); };
  add(outboxDir);
  const home = _corvinHome();
  if (home) {
    add(path.join(home, 'sessions'));
    const tenants = path.join(home, 'tenants');
    let names = [];
    try { names = fs.readdirSync(tenants); } catch { names = []; }
    for (const t of names) add(path.join(tenants, t, 'sessions'));
  }
  const env = process.env.CORVIN_OUTBOX_ATTACHMENT_ROOTS || '';
  for (const p of env.split(path.delimiter)) if (p.trim()) add(p.trim());
  for (const p of (extraRoots || [])) add(p);
  return roots;
}

function _isBelow(root, real) {
  const rel = path.relative(root, real);
  return rel !== '' && !rel.startsWith('..') && !path.isAbsolute(rel);
}

/**
 * @returns {string|null} the path to use (as given) when it is attachable,
 *                        null when it must not be sent.
 */
function safeAttachmentPath(p, outboxDir, extraRoots) {
  if (typeof p !== 'string' || p.length === 0 || !path.isAbsolute(p)) return null;
  const real = _real(p);
  if (!real) return null;
  let st;
  try { st = fs.statSync(real); } catch { return null; }
  if (!st.isFile()) return null;
  if (real.split(path.sep).some((c) => c && DENIED_COMPONENT_RE.test(c))) return null;
  for (const root of attachmentRoots(outboxDir, extraRoots)) {
    if (_isBelow(root, real)) return p;
  }
  return null;
}

/**
 * Remove every attachment path from `payload` (in place) that must not be
 * sent. A path to a file that does not exist is left alone — the daemons
 * already skip those with fs.existsSync, and a not-yet-flushed voice note
 * must not be turned into a permanent drop here.
 * @returns {string[]} the keys that were removed
 */
function sanitizeAttachmentPaths(payload, outboxDir, { logger, extraRoots } = {}) {
  const removed = [];
  if (!payload || typeof payload !== 'object') return removed;
  for (const key of ATTACHMENT_KEYS) {
    const v = payload[key];
    if (v === undefined || v === null || v === '') continue;
    if (typeof v === 'string' && path.isAbsolute(v) && !fs.existsSync(v)) continue;
    if (safeAttachmentPath(v, outboxDir, extraRoots) === null) {
      delete payload[key];
      removed.push(key);
      // Key only: the refused path is attacker-chosen and may itself be sensitive.
      if (logger) logger(`outbox: refused ${key} outside the allowed attachment roots`);
    }
  }
  return removed;
}

module.exports = {
  ATTACHMENT_KEYS, attachmentRoots, safeAttachmentPath, sanitizeAttachmentPaths,
};
