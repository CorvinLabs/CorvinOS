// test_host_guard.js — review SRF-1: the daemons' loopback HTTP servers refuse a
// DNS-rebound Host. Drives the REAL shared health server over loopback HTTP with a
// crafted Host header, checks the audit hook fires, and pins that the WhatsApp
// daemon (whose /qr.png hands out the pairing QR) runs the guard before any route.
//
// The WhatsApp daemon itself is not spawned here: in --mock mode it still writes its
// inbox/outbox/auth under the checkout's bridges tree (the live bridge's queues), so
// its wiring is pinned by source instead and the guard is exercised over HTTP
// through health-server.js, which every other daemon uses.

'use strict';

const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');

const hg = require('./host_guard');

function get(port, pathName, host) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: pathName, method: 'GET',
                               headers: host === undefined ? {} : { Host: host } }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('error', reject);
    req.end();
  });
}

(async () => {
  // Parsing rules (same as corvin_console/host_guard.py).
  assert.ok(hg.hostAllowed('localhost:7891'));
  assert.ok(hg.hostAllowed('127.0.0.1:7891'));
  assert.ok(hg.hostAllowed('[::1]:7891'));
  assert.ok(hg.hostAllowed('app.localhost'));
  assert.ok(hg.hostAllowed(undefined));
  assert.ok(!hg.hostAllowed('evil.example:7891'));
  assert.ok(!hg.hostAllowed('localhost.evil.example'));
  process.env.CORVIN_ALLOWED_HOSTS = 'corvin.example.com,.lan';
  assert.ok(hg.hostAllowed('corvin.example.com'));
  assert.ok(hg.hostAllowed('box.lan:7891'));
  delete process.env.CORVIN_ALLOWED_HOSTS;

  // RC-10: one parse table for Python and JS (host_guard_cases.json).
  const os = require('os');
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'hg-js-'));
  process.env.CORVIN_HOME = tmpHome; // no tenant YAML yet: the yaml source adds nothing
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'host_guard_cases.json'), 'utf8'));
  assert.ok(cases.entries.length > 10 && cases.hosts.cases.length > 10, 'positive control: table loaded');
  for (const [raw, expected] of cases.entries) {
    assert.deepStrictEqual(hg.parseEntry(raw), expected, `parseEntry(${JSON.stringify(raw)})`);
  }
  process.env.CORVIN_ALLOWED_HOSTS = cases.hosts.allowlist;
  const logged = [];
  for (const [host, ok] of cases.hosts.cases) {
    assert.strictEqual(hg.hostAllowed(host, { logger: (m) => logged.push(m) }), ok,
      `hostAllowed(${JSON.stringify(host)})`);
  }
  // "." and ".x.lan:1" are ignored and logged once each, however often parsed
  assert.strictEqual(logged.filter((m) => m.includes('ignoring')).length, 2, logged.join('\n'));
  delete process.env.CORVIN_ALLOWED_HOSTS;

  // RC-10: spec.console.allowed_hosts of the _default tenant is read too.
  const tdir = path.join(tmpHome, 'tenants', '_default', 'global');
  fs.mkdirSync(tdir, { recursive: true });
  fs.writeFileSync(path.join(tdir, 'tenant.corvin.yaml'),
    'spec:\n  console:\n    allowed_hosts:\n      - nas.home.arpa\n      - .corvin.lan\n', { mode: 0o600 });
  assert.ok(hg.hostAllowed('nas.home.arpa:7891'), 'yaml exact entry');
  assert.ok(hg.hostAllowed('box.corvin.lan'), 'yaml suffix entry');
  assert.ok(!hg.hostAllowed('evil.example'));

  // RC-2: the throttle is per host AND global, and counts what it suppressed.
  hg.resetAuditThrottle();
  let written = 0;
  for (let i = 0; i < 300; i++) {
    if (hg.admitRecord(`host_not_allowed|r${i}`, 1000) !== null) written += 1;
  }
  assert.strictEqual(written, hg.AUDIT_MAX_PER_WINDOW, 'rotating hosts are capped globally');
  assert.strictEqual(hg.admitRecord('host_not_allowed|late', 1000 + 61000), 300 - hg.AUDIT_MAX_PER_WINDOW,
    'the next record carries suppressed_count');
  hg.resetAuditThrottle();
  hg.admitRecord('host_not_allowed|victim', 5000);
  for (let i = 0; i < hg.AUDIT_KEYS_MAX + 10; i++) hg.admitRecord(`host_not_allowed|h${i}`, 5001);
  assert.strictEqual(hg.admitRecord('host_not_allowed|victim', 5002), null,
    'LRU eviction must not reset the global throttle');
  hg.resetAuditThrottle();

  // The real shared health server, over HTTP.
  const audited = [];
  const auditMod = require('./audit');
  const realAudit = auditMod.auditEvent;
  auditMod.auditEvent = (type, opts) => audited.push({ type, opts });
  const { startHealthServer } = require('./health-server');
  const srv = startHealthServer({ port: 0, kind: 'testchan', getStatus: () => ({ ok: 1 }) });
  await new Promise((r) => srv.once('listening', r));
  const port = srv.address().port;
  try {
    const bad = await get(port, '/status', 'evil.example:' + port);
    assert.strictEqual(bad.status, 400, bad.body);
    assert.ok(!bad.body.includes('testchan'));
    assert.ok(bad.body.includes('CORVIN_ALLOWED_HOSTS') && bad.body.includes('spec.console.allowed_hosts'),
      'the refusal names the setting');
    assert.strictEqual(audited.length, 1);
    assert.strictEqual(audited[0].type, 'http.host_rejected');
    assert.strictEqual(audited[0].opts.details.reason, 'host_not_allowed');
    assert.strictEqual(audited[0].opts.details.suppressed_count, 0);
    assert.ok(!JSON.stringify(audited[0]).includes('evil.example'), 'host value must not be stored');
    // throttled: a second refusal of the same host within a minute is not re-recorded
    await get(port, '/status', 'evil.example:' + port);
    assert.strictEqual(audited.length, 1);

    const good = await get(port, '/status', '127.0.0.1:' + port);
    assert.strictEqual(good.status, 200, good.body);
    assert.strictEqual(JSON.parse(good.body).kind, 'testchan');
  } finally {
    auditMod.auditEvent = realAudit;
    srv.close();
  }

  // The WhatsApp daemon guards its HTTP API (incl. /pair and /qr.png) first.
  const wa = fs.readFileSync(path.join(__dirname, '..', '..', 'whatsapp', 'daemon.js'), 'utf8');
  const start = wa.indexOf('const httpServer = http.createServer(');
  assert.ok(start > 0, 'positive control: the WA HTTP server was found');
  const guard = wa.indexOf('guardHost(req, res', start);
  const pair = wa.indexOf("req.url === '/pair'", start);
  const qr = wa.indexOf("req.url.startsWith('/qr.png')", start);
  assert.ok(guard > start && guard < pair && guard < qr, 'guard must run before /pair and /qr.png');

  fs.rmSync(tmpHome, { recursive: true, force: true });
  console.log('test_host_guard: ok');
})().catch((e) => { console.error(e); process.exit(1); });
