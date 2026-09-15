#!/usr/bin/env node
// test_net_probe.js — unit tests for the local-network-outage detection
// helpers added after incident 2026-07-10 (hotspot drop misread as
// Discord-side failure by every resilience layer). Same framework-free
// style as test_bridge_state.js.

'use strict';

const { isNetworkError, networkUp } = require('./net_probe');

let failures = 0;
function assert(cond, msg) {
  if (cond) {
    console.log('  ok  -', msg);
  } else {
    failures++;
    console.log('  FAIL -', msg);
  }
}

async function main() {
  console.log('test_net_probe');

  // 1. Connection-level signatures from the actual incident logs.
  for (const msg of [
    'getaddrinfo ENOTFOUND discord.com',
    'getaddrinfo ENOTFOUND gateway-us-east1-d.discord.gg',
    'connect ECONNREFUSED 127.0.0.1:443',
    'connect ETIMEDOUT 162.159.128.233:443',
    'queryA EAI_AGAIN discord.com',
    'read ECONNRESET',
    'connect ENETUNREACH 1.2.3.4:443',
    'socket hang up',
    'Name or service not known',
    // Blackhole variant: AP associated, cellular uplink dead — DNS answers
    // from cache, TCP blackholes → undici timeout errors, no getaddrinfo.
    'Connect Timeout Error',
    'Headers Timeout Error',
    'UND_ERR_CONNECT_TIMEOUT',
    'This operation was aborted',
  ]) {
    assert(isNetworkError(msg), `network error: ${JSON.stringify(msg)}`);
  }

  // 2. HTTP-level / API failures must NOT match — they reached Discord and
  //    must keep the conservative backoff ladder (IDENTIFY budget).
  for (const msg of [
    'Expected token to be set for this request, but none was present',
    '503 Service Unavailable (Cloudflare)',
    'You are being rate limited.',
    'Invalid Form Body',
    'TOKEN_INVALID',
    'Missing Permissions',
    '',
    null,
    undefined,
  ]) {
    assert(!isNetworkError(msg), `not a network error: ${JSON.stringify(msg)}`);
  }

  // 3. networkUp with an injected resolver: success → true.
  assert(await networkUp({ lookup: async () => ({ address: '1.2.3.4' }) }) === true,
         'networkUp true when lookup resolves');

  // 4. Resolver rejection (DNS dead) → false.
  assert(await networkUp({ lookup: async () => { throw new Error('getaddrinfo ENOTFOUND discord.com'); } }) === false,
         'networkUp false when lookup rejects');

  // 5. Resolver hang → probe times out → false (and does not hang forever).
  const t0 = Date.now();
  const up = await networkUp({ timeoutMs: 100, lookup: () => new Promise(() => {}) });
  assert(up === false, 'networkUp false when lookup hangs (timeout)');
  assert(Date.now() - t0 < 3000, 'timeout fires promptly');

  if (failures > 0) {
    console.log(`FAILED — ${failures} assertion(s) failed.`);
    process.exit(1);
  }
  console.log('PASSED');
}

main().catch((e) => { console.error(e); process.exit(1); });
