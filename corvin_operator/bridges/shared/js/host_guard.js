// host_guard.js — DNS-rebinding guard for the bridge daemons' loopback HTTP servers.
//
// Review SRF-1 (2026-11-05). "Bound to 127.0.0.1" is not an access control against a
// browser: a page on evil.example rebinds its name to 127.0.0.1, the request then
// arrives from a loopback peer with `Host: evil.example:<port>`, and the page is
// same-origin with the daemon. For the WhatsApp daemon that handed the live pairing
// QR (/qr.png) to a remote party, which could link ITS account as the bridge's device.
//
// Same rule as the console/gateway (core/console/corvin_console/host_guard.py):
// a request is answered only when its Host is a loopback name (localhost,
// *.localhost), an IP literal (rebinding needs a NAME), or listed in
// CORVIN_ALLOWED_HOSTS (comma separated, a leading "." matches a domain suffix).
// A request with no Host header (HTTP/1.0, non-browser) passes: browsers always
// send one. A refusal answers 400 and is recorded as `http.host_rejected`
// (throttled to one record per reason+host per minute; only a host fingerprint).

'use strict';

const crypto = require('crypto');
const net = require('net');

const AUDIT_EVENT = 'http.host_rejected';
const THROTTLE_MS = 60 * 1000;
const _lastAudit = new Map();

function splitHost(value) {
  if (!value) return '';
  const v = String(value).trim().toLowerCase();
  if (v.startsWith('[')) {
    const end = v.indexOf(']');
    return end > 0 ? v.slice(1, end) : '';
  }
  if ((v.match(/:/g) || []).length > 1) return v; // bare IPv6
  const i = v.lastIndexOf(':');
  return i >= 0 ? v.slice(0, i) : v;
}

function isLoopbackName(h) {
  return h === 'localhost' || h.endsWith('.localhost');
}

function allowedHosts() {
  return String(process.env.CORVIN_ALLOWED_HOSTS || '')
    .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
}

function hostAllowed(hostHeader) {
  if (hostHeader === undefined || hostHeader === null || hostHeader === '') return true;
  const h = splitHost(hostHeader);
  if (!h) return false;
  if (isLoopbackName(h)) return true;
  if (net.isIP(h.split('%')[0])) return true;
  for (const pat of allowedHosts()) {
    if (pat.startsWith('.')) {
      if (h.endsWith(pat) || h === pat.slice(1)) return true;
    } else if (h === splitHost(pat)) {
      return true;
    }
  }
  return false;
}

function fp(value) {
  return crypto.createHash('sha256').update(String(value || ''), 'utf8').digest('hex').slice(0, 16);
}

function pathClass(url) {
  const segs = String(url || '/').split('?')[0].split('/').filter(Boolean).slice(0, 2);
  return ('/' + segs.join('/')).slice(0, 64);
}

/**
 * Returns true when the request may proceed. Otherwise answers 400, records the
 * refusal through `audit` (fn(eventType, opts)) and returns false.
 */
function guardRequest(req, res, { channel, audit, logger } = {}) {
  const host = req.headers && req.headers.host;
  if (hostAllowed(host)) return true;
  const hostFp = fp(splitHost(host));
  const key = `host_not_allowed|${hostFp}`;
  const now = Date.now();
  const last = _lastAudit.get(key);
  if (last === undefined || now - last >= THROTTLE_MS) {
    _lastAudit.set(key, now);
    if (_lastAudit.size > 4096) _lastAudit.clear();
    if (logger) logger(`refused http request: host_not_allowed (path=${pathClass(req.url)})`);
    if (typeof audit === 'function') {
      try {
        audit(AUDIT_EVENT, {
          channel,
          details: {
            surface: `${channel || 'bridge'}_daemon`, reason: 'host_not_allowed',
            scope_type: 'http', path_class: pathClass(req.url), host_fp: hostFp,
          },
        });
      } catch (e) {
        if (logger) logger(`host_guard: audit failed: ${e.message}`);
      }
    }
  }
  res.statusCode = 400;
  res.setHeader('Content-Type', 'application/json');
  res.end('{"detail":"host not allowed"}');
  return false;
}

module.exports = { hostAllowed, guardRequest, splitHost, AUDIT_EVENT };
