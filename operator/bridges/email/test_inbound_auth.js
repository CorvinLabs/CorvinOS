#!/usr/bin/env node
// PENTEST-3a regression: the email bridge must NOT trust a bare RFC5322 `From`
// header. It requires the receiving provider's `Authentication-Results` header
// to show DMARC alignment (or an aligned DKIM pass) before the From address may
// act as an authenticated principal. Fail-closed on missing / unaligned auth.
//
// daemon.js self-boots on require (IMAP connect + process.exit), so it is not
// import-safe. As in test_disclosure_ordering.js we extract the REAL helper
// functions from the daemon.js source and execute them against injected mocks
// — this exercises the actual shipped code, not a copy.
//
// Run: node operator/bridges/email/test_inbound_auth.js

'use strict';

const fs   = require('fs');
const path = require('path');
const { simpleParser } = require('mailparser');

const SRC = fs.readFileSync(path.join(__dirname, 'daemon.js'), 'utf8');

let pass = 0, fail = 0;
function t(label, ok, detail = '') {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ' — ' + detail : ''}`);
  if (ok) pass++; else fail++;
}

// ── brace-match a top-level `function NAME(...) { … }` out of the source ──────
function extractFn(src, name) {
  const decl = `function ${name}(`;
  const start = src.indexOf(decl);
  if (start === -1) throw new Error(`function ${name} not found`);
  const braceOpen = src.indexOf('{', start);
  let depth = 0;
  for (let i = braceOpen; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

const fnSrc = ['domainOf', 'domainsAligned', 'topAuthResultsLine', 'inboundAuthPasses']
  .map((n) => extractFn(SRC, n)).join('\n\n');

t('helper functions present in daemon.js', /function inboundAuthPasses/.test(fnSrc));

// Build the extracted helpers bound to an injectable currentSettings().
function makeHelpers(settings) {
  const factory = new Function(
    'currentSettings',
    `${fnSrc}\n; return { domainOf, domainsAligned, topAuthResultsLine, inboundAuthPasses };`,
  );
  return factory(() => settings);
}

function rawMail(headerLines, from = 'Owner <owner@example.com>') {
  return [...headerLines, `From: ${from}`, 'To: bot@bot.tld', 'Subject: hi', '', 'body', '']
    .join('\r\n');
}

(async () => {
  const H = makeHelpers({});

  // 1. dmarc=pass → authorized
  console.log('\n[dmarc=pass → authorized]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dmarc=pass header.from=example.com; spf=pass',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true', r.ok === true, r.reason);
  }

  // 2. no Authentication-Results header at all → fail-closed
  console.log('\n[no Authentication-Results → drop]');
  {
    const p = await simpleParser(rawMail([]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false', r.ok === false, r.reason);
    t('reason names missing header', r.reason === 'no-authentication-results');
  }

  // 3. dmarc=fail → drop (spoof)
  console.log('\n[dmarc=fail → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dmarc=fail header.from=example.com; spf=fail; dkim=fail',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false', r.ok === false, r.reason);
  }

  // 4. THE ATTACK: attacker appends their OWN forged dmarc=pass line below the
  //    provider's real dmarc=fail line. Only the top (provider) line is trusted.
  console.log('\n[forged appended Authentication-Results → still drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dmarc=fail header.from=example.com',
      'Authentication-Results: attacker.local; dmarc=pass header.from=example.com',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('forged line ignored, ok=false', r.ok === false, r.reason);
  }

  // 5. dkim=pass with ALIGNED d= domain → authorized. (authserv-id is a
  //    well-known receiver so the built-in allowlist admits the top line; the
  //    d= alignment against the From domain is what's under test here.)
  console.log('\n[dkim=pass aligned d= → authorized]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.i=@example.com header.s=s1 header.d=example.com; spf=pass',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true', r.ok === true, r.reason);
  }

  // 6. dkim=pass with UNALIGNED d= domain → drop (e.g. mailing-list resign)
  console.log('\n[dkim=pass unaligned d= → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.d=attacker.tld; spf=pass',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false', r.ok === false, r.reason);
  }

  // 7. aligned sub-domain DKIM signer → authorized (relaxed alignment)
  console.log('\n[dkim=pass sub-domain d= → authorized]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.d=mail.example.com',
    ], 'Owner <owner@example.com>'));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true (relaxed alignment)', r.ok === true, r.reason);
  }

  // 8. optional authserv-id pin mismatch → drop even on dmarc=pass
  console.log('\n[auth_results_authserv_id pin mismatch → drop]');
  {
    const Hp = makeHelpers({ auth_results_authserv_id: 'mx.mycorp.com' });
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dmarc=pass header.from=example.com',
    ]));
    const r = Hp.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false on authserv-id mismatch', r.ok === false, r.reason);
  }

  // 9. THE NON-STAMPING-PROVIDER ATTACK: a self-hosted/non-stamping IMAP
  //    provider stamps NO Authentication-Results line, so the attacker's
  //    injected line is the SOLE (top) line. Its authserv-id is unknown and no
  //    pin is set → must be rejected (fail-closed → sender must PIN /auth).
  console.log('\n[injected sole AR line, unknown authserv-id → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: attacker.local; dmarc=pass header.from=example.com',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false (unknown authserv-id not trusted)', r.ok === false, r.reason);
  }

  // 10. dev_mode restores the legacy open behaviour for local testing: an
  //     unknown/self-hosted authserv-id with dmarc=pass is accepted.
  console.log('\n[dev_mode: unknown authserv-id + dmarc=pass → allowed]');
  {
    const Hd = makeHelpers({ dev_mode: true });
    const p = await simpleParser(rawMail([
      'Authentication-Results: my-selfhosted.local; dmarc=pass header.from=example.com',
    ]));
    const r = Hd.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true in dev_mode', r.ok === true, r.reason);
  }

  // 11. self-hosted operator who pins their OWN authserv-id → that id is
  //     trusted (closes the gap without dev_mode's blanket open behaviour).
  console.log('\n[pinned self-hosted authserv-id → allowed]');
  {
    const Hp = makeHelpers({ auth_results_authserv_id: 'mail.mycorp.internal' });
    const p = await simpleParser(rawMail([
      'Authentication-Results: mail.mycorp.internal; dmarc=pass header.from=example.com',
    ]));
    const r = Hp.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true when top authserv-id matches pin', r.ok === true, r.reason);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})();
