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
    assert.strictEqual(audited.length, 1);
    assert.strictEqual(audited[0].type, 'http.host_rejected');
    assert.strictEqual(audited[0].opts.details.reason, 'host_not_allowed');
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

  console.log('test_host_guard: ok');
})().catch((e) => { console.error(e); process.exit(1); });
