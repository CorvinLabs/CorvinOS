// host_guard.js — DNS-rebinding guard for the bridge daemons' loopback HTTP servers.
//
// Review SRF-1 (2026-11-05). "Bound to 127.0.0.1" is not an access control against a
// browser: a page on evil.example rebinds its name to 127.0.0.1, the request then
// arrives from a loopback peer with `Host: evil.example:<port>`, and the page is
// same-origin with the daemon. For the WhatsApp daemon that handed the live pairing
// QR (/qr.png) to a remote party, which could link ITS account as the bridge's device.
//
// Twin of core/console/corvin_console/host_guard.py. A request is answered only when
// its Host is a loopback name (localhost, *.localhost), an IP literal (rebinding
// needs a NAME), or on the operator allowlist read from the same two sources as the
// console: env CORVIN_ALLOWED_HOSTS and spec.console.allowed_hosts in the _default
// tenant's tenant.corvin.yaml (read through the bridge Python, mtime-cached, only
// when a Host is not already allowed). Entries parse by the same table as Python
// (host_guard_cases.json): a name (a port on it is dropped) or a ".suffix" (never
// with a port); empty entries, "." and a suffix with a port are ignored and logged
// once. The daemons bind loopback, so the console's deployment names (CORVIN_DOMAIN,
// the Tailscale name, the bind host) are deliberately NOT derived here.
// A request with no Host header (HTTP/1.0, non-browser) passes: browsers always
// send one. A refusal answers 400 naming the two settings and is recorded as
// `http.host_rejected`: at most once a minute per host AND at most
// AUDIT_MAX_PER_WINDOW records a minute overall, each carrying `suppressed_count`
// (refusals not recorded since the previous record); only a host fingerprint.

'use strict';

const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const path = require('path');
const { spawnSync } = require('child_process');

const AUDIT_EVENT = 'http.host_rejected';
const THROTTLE_MS = 60 * 1000;
const AUDIT_MAX_PER_WINDOW = 20;
const AUDIT_KEYS_MAX = 4096;
const HINT = 'this Host is not one this install answers to; add it to CORVIN_ALLOWED_HOSTS '
  + 'or spec.console.allowed_hosts in tenant.corvin.yaml';
const REFUSAL_BODY = JSON.stringify({ detail: 'host not allowed', hint: HINT });

// key -> last record time; Map iteration order = insertion order = LRU order.
const _lastAudit = new Map();
const _window = { start: -Infinity, count: 0, suppressed: 0 };
const _ignoredLogged = new Set();

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

function isIpLiteral(h) {
  return net.isIP(String(h).split('%')[0]) !== 0;
}

function isLoopbackHost(value) {
  const h = splitHost(value);
  if (!h) return false;
  if (h === 'localhost' || h.endsWith('.localhost')) return true;
  const ip = h.split('%')[0];
  const kind = net.isIP(ip);
  if (kind === 4) return ip.startsWith('127.');
  if (kind === 6) {
    if (ip === '::1' || /^0*:0*:0*:0*:0*:0*:0*:0*1$/.test(ip)) return true;
    const m = ip.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
    return Boolean(m && m[1].startsWith('127.'));
  }
  return false;
}

/** One allowlist entry -> ['exact', host] / ['suffix', '.domain'] / null (ignored). */
function parseEntry(raw) {
  const s = String(raw === undefined || raw === null ? '' : raw).trim().toLowerCase();
  if (!s || s.includes('/') || /\s/.test(s)) return null;
  if (s.startsWith('.')) {
    if (s.includes(':')) return null;
    const body = s.endsWith('.') ? s.slice(1, -1) : s.slice(1);
    if (!body || body.split('.').includes('')) return null;
    return ['suffix', '.' + body];
  }
  let h = splitHost(s);
  if (h.endsWith('.')) h = h.slice(0, -1);
  if (!h || h.split('.').includes('')) return null;
  return ['exact', h];
}

function parseEntries(raws, source, logger) {
  const exact = new Set();
  const suffix = new Set();
  for (const raw of raws) {
    const parsed = parseEntry(raw);
    if (parsed === null) {
      const text = String(raw === undefined || raw === null ? '' : raw);
      if (!text.trim()) continue;
      if (!_ignoredLogged.has(text)) {
        _ignoredLogged.add(text);
        if (logger) logger(`host_guard: ignoring ${source} entry ${JSON.stringify(text.slice(0, 80))} (empty, '.', a suffix with a port, or malformed)`);
      }
      continue;
    }
    (parsed[0] === 'exact' ? exact : suffix).add(parsed[1]);
  }
  return { exact, suffix };
}

// ── spec.console.allowed_hosts (tenant.corvin.yaml) ─────────────────────────

const _yamlCache = { key: null, hosts: [] };
const _READ_YAML_PY = [
  'import json, sys, yaml',
  'doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}',
  'spec = doc.get("spec") if isinstance(doc, dict) else None',
  'con = spec.get("console") if isinstance(spec, dict) else None',
  'raw = con.get("allowed_hosts") if isinstance(con, dict) else None',
  'print(json.dumps([str(x) for x in raw] if isinstance(raw, list) else []))',
].join('\n');

function tenantYamlPath() {
  const { corvinHome } = require('./bridge_paths');
  return path.join(corvinHome(), 'tenants', '_default', 'global', 'tenant.corvin.yaml');
}

function yamlAllowedHosts(logger) {
  let st;
  const p = tenantYamlPath();
  try {
    st = fs.statSync(p);
  } catch (_) {
    return []; // no file: nothing extra
  }
  const key = `${p}|${st.mtimeMs}|${st.size}`;
  if (_yamlCache.key === key) return _yamlCache.hosts;
  let hosts = [];
  const { pythonBin } = require('./python_bin');
  const r = spawnSync(pythonBin(), ['-I', '-c', _READ_YAML_PY, p],
    { encoding: 'utf8', timeout: 5000, windowsHide: true });
  if (r.status === 0) {
    try {
      const parsed = JSON.parse(String(r.stdout || '[]'));
      if (Array.isArray(parsed)) hosts = parsed;
    } catch (e) {
      if (logger) logger(`host_guard: spec.console.allowed_hosts unreadable (${e.name}); ignoring it`);
    }
  } else if (logger) {
    logger('host_guard: spec.console.allowed_hosts unreadable (python exit '
      + `${r.status === null ? 'timeout' : r.status}); ignoring it`);
  }
  _yamlCache.key = key;
  _yamlCache.hosts = hosts;
  return hosts;
}

function operatorHosts(logger) {
  const a = parseEntries(String(process.env.CORVIN_ALLOWED_HOSTS || '').split(','),
    'CORVIN_ALLOWED_HOSTS', logger);
  const b = parseEntries(yamlAllowedHosts(logger), 'spec.console.allowed_hosts', logger);
  return { exact: new Set([...a.exact, ...b.exact]), suffix: new Set([...a.suffix, ...b.suffix]) };
}

function hostAllowed(hostHeader, { logger } = {}) {
  if (hostHeader === undefined || hostHeader === null || hostHeader === '') return true;
  const h = splitHost(hostHeader);
  if (!h) return false;
  if (isLoopbackHost(h) || isIpLiteral(h)) return true;
  const { exact, suffix } = operatorHosts(logger);
  if (exact.has(h)) return true;
  for (const suf of suffix) {
    if (h.endsWith(suf) || h === suf.slice(1)) return true;
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

/** Throttle decision: null = not recorded; else the suppressed_count to carry. */
function admitRecord(key, now = Date.now()) {
  const last = _lastAudit.get(key);
  if (last !== undefined && now - last < THROTTLE_MS) {
    _lastAudit.delete(key);
    _lastAudit.set(key, last); // LRU touch
    _window.suppressed += 1;
    return null;
  }
  if (now - _window.start >= THROTTLE_MS) {
    _window.start = now;
    _window.count = 0;
  }
  if (_window.count >= AUDIT_MAX_PER_WINDOW) {
    _window.suppressed += 1;
    return null;
  }
  _window.count += 1;
  _lastAudit.delete(key);
  _lastAudit.set(key, now);
  while (_lastAudit.size > AUDIT_KEYS_MAX) {
    _lastAudit.delete(_lastAudit.keys().next().value); // least recently seen host
  }
  const carried = _window.suppressed;
  _window.suppressed = 0;
  return carried;
}

function resetAuditThrottle() {
  _lastAudit.clear();
  _window.start = -Infinity;
  _window.count = 0;
  _window.suppressed = 0;
}

/**
 * Returns true when the request may proceed. Otherwise answers 400, records the
 * refusal through `audit` (fn(eventType, opts)) and returns false.
 */
function guardRequest(req, res, { channel, audit, logger } = {}) {
  const host = req.headers && req.headers.host;
  if (hostAllowed(host, { logger })) return true;
  const hostFp = fp(splitHost(host));
  const suppressed = admitRecord(`host_not_allowed|${hostFp}`);
  if (suppressed !== null) {
    if (logger) logger(`refused http request: host_not_allowed (path=${pathClass(req.url)}, ${suppressed} suppressed before)`);
    if (typeof audit === 'function') {
      try {
        audit(AUDIT_EVENT, {
          channel,
          details: {
            surface: `${channel || 'bridge'}_daemon`, reason: 'host_not_allowed',
            scope_type: 'http', path_class: pathClass(req.url), host_fp: hostFp,
            suppressed_count: suppressed,
          },
        });
      } catch (e) {
        if (logger) logger(`host_guard: audit failed: ${e.message}`);
      }
    }
  }
  res.statusCode = 400;
  res.setHeader('Content-Type', 'application/json');
  res.end(REFUSAL_BODY);
  return false;
}

module.exports = {
  hostAllowed, guardRequest, splitHost, parseEntry, isLoopbackHost, admitRecord,
  resetAuditThrottle, AUDIT_EVENT, AUDIT_MAX_PER_WINDOW, AUDIT_KEYS_MAX,
};
