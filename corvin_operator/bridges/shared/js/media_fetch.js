// media_fetch.js — bounded download of an inbound attachment to a file.
//
// The daemons used to do `Buffer.from(await (await fetch(url)).arrayBuffer())`
// for every attachment: no status check (an HTML error page was stored as the
// "image"), no size limit (hundreds of MB buffered in RAM), and — on Slack —
// the bot token sent as a Bearer header to whatever URL the event carried
// (review 2026-11-19, R3-NEW-31 / R3-NEW-33).
//
// fetchToFile() enforces, in this order: https + host allowlist (BEFORE any
// header is sent), the declared size, the HTTP status, the Content-Length,
// and a hard byte cap while streaming. Redirects are followed manually so the
// allowlist and the "no credential to a foreign host" rule hold on every hop.
// A failed download leaves no partial file behind.

'use strict';

const fs = require('fs');
const path = require('path');

// Chat platforms cap uploads well below this; the adapter has no use for more.
const DEFAULT_MAX_BYTES = 50 * 1024 * 1024;
const DEFAULT_TIMEOUT_MS = 60 * 1000;
const MAX_REDIRECTS = 3;

// Slack serves private files from these hosts only.
const SLACK_FILE_HOSTS = Object.freeze(['files.slack.com', 'files-pri.slack.com', 'files-edge.slack.com']);
// Discord CDN hosts for message attachments.
const DISCORD_CDN_HOSTS = Object.freeze(['cdn.discordapp.com', 'media.discordapp.net']);
const TELEGRAM_FILE_HOSTS = Object.freeze(['api.telegram.org']);

function hostAllowed(url, allowedHosts) {
  let u;
  try { u = new URL(String(url)); } catch { return false; }
  if (u.protocol !== 'https:') return false;
  if (u.username || u.password) return false;
  if (!allowedHosts) return true;
  return allowedHosts.includes(u.hostname.toLowerCase());
}

// Lower-cased extension including the dot, '' when there is none. Null-safe:
// `att.name.lastIndexOf` threw on an attachment without a name, and a name
// without a dot used to yield its last character(s) via slice(-1).
function safeExt(name) {
  const n = typeof name === 'string' ? name : '';
  const i = n.lastIndexOf('.');
  if (i <= 0 || i === n.length - 1) return '';
  const ext = n.slice(i).toLowerCase();
  return /^\.[a-z0-9]{1,10}$/.test(ext) ? ext : '';
}

function _unlinkQuiet(p) { try { fs.unlinkSync(p); } catch { /* nothing to clean */ } }

/**
 * Download `url` to `dest`.
 * @param {string} url
 * @param {string} dest
 * @param {object} [opts]
 * @param {object}   [opts.headers]        sent ONLY to an allowed host
 * @param {string[]} [opts.allowedHosts]   exact hostnames; omit = any https host
 * @param {number}   [opts.maxBytes]
 * @param {number}   [opts.declaredSize]   size the platform announced, if any
 * @param {number}   [opts.timeoutMs]
 * @param {function} [opts.fetchImpl]      fetch replacement (tests)
 * @returns {Promise<number>} bytes written
 */
async function fetchToFile(url, dest, opts = {}) {
  const maxBytes = opts.maxBytes || DEFAULT_MAX_BYTES;
  const fetchImpl = opts.fetchImpl || fetch;
  if (Number.isFinite(opts.declaredSize) && opts.declaredSize > maxBytes) {
    throw new Error(`attachment too large (${opts.declaredSize} > ${maxBytes} bytes)`);
  }
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), opts.timeoutMs || DEFAULT_TIMEOUT_MS);
  const tmp = `${dest}.part`;
  let fd = null;
  try {
    let current = String(url);
    let res;
    for (let hop = 0; ; hop++) {
      if (!hostAllowed(current, opts.allowedHosts)) {
        throw new Error('attachment URL host not allowed');
      }
      res = await fetchImpl(current, {
        headers: opts.headers || {}, redirect: 'manual', signal: ac.signal,
      });
      if (res.status >= 300 && res.status < 400 && res.headers && res.headers.get('location')) {
        if (hop >= MAX_REDIRECTS) throw new Error('too many redirects');
        current = new URL(res.headers.get('location'), current).toString();
        continue;
      }
      break;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const declared = Number(res.headers && res.headers.get('content-length'));
    if (Number.isFinite(declared) && declared > maxBytes) {
      throw new Error(`attachment too large (${declared} > ${maxBytes} bytes)`);
    }
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fd = fs.openSync(tmp, 'w', 0o600);
    let total = 0;
    if (res.body && typeof res.body.getReader === 'function') {
      const reader = res.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > maxBytes) {
          try { await reader.cancel(); } catch { /* already closed */ }
          throw new Error(`attachment too large (> ${maxBytes} bytes)`);
        }
        fs.writeSync(fd, value);
      }
    } else {
      const buf = Buffer.from(await res.arrayBuffer());
      if (buf.length > maxBytes) throw new Error(`attachment too large (> ${maxBytes} bytes)`);
      fs.writeSync(fd, buf);
      total = buf.length;
    }
    fs.closeSync(fd); fd = null;
    fs.renameSync(tmp, dest);
    return total;
  } catch (e) {
    if (fd !== null) { try { fs.closeSync(fd); } catch { /* ignore */ } }
    _unlinkQuiet(tmp);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Pipe a readable stream to `dest`. Rejects on a stream error on EITHER side
 * (the Telegram voice path awaited only `finish` and hung forever when the
 * source stream failed), on the byte cap and on the timeout.
 * @returns {Promise<number>} bytes written
 */
function streamToFile(stream, dest, opts = {}) {
  const maxBytes = opts.maxBytes || DEFAULT_MAX_BYTES;
  const timeoutMs = opts.timeoutMs || DEFAULT_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    const tmp = `${dest}.part`;
    const out = fs.createWriteStream(tmp, { mode: 0o600 });
    let total = 0;
    let settled = false;
    const finish = (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (err) {
        try { if (typeof stream.destroy === 'function') stream.destroy(); } catch { /* ignore */ }
        // Remove the partial file only once the write stream has closed: its
        // open is asynchronous, and an unlink before it would be undone.
        const cleanup = () => { _unlinkQuiet(tmp); reject(err); };
        if (out.closed) cleanup();
        else { out.once('close', cleanup); out.destroy(); }
        return;
      }
      try { fs.renameSync(tmp, dest); resolve(total); }
      catch (e) { _unlinkQuiet(tmp); reject(e); }
    };
    const timer = setTimeout(() => finish(new Error('attachment download timed out')), timeoutMs);
    stream.on('data', (chunk) => {
      total += chunk.length;
      if (total > maxBytes) finish(new Error(`attachment too large (> ${maxBytes} bytes)`));
    });
    stream.on('error', (e) => finish(e instanceof Error ? e : new Error(String(e))));
    out.on('error', (e) => finish(e));
    out.on('finish', () => finish(null));
    stream.pipe(out);
  });
}

module.exports = {
  fetchToFile, streamToFile, hostAllowed, safeExt,
  DEFAULT_MAX_BYTES, SLACK_FILE_HOSTS, DISCORD_CDN_HOSTS, TELEGRAM_FILE_HOSTS,
};
