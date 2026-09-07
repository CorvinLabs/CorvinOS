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

// RECEIVER_FAMILIES is a module-level const the helpers close over (R2-B2);
// slice it out verbatim so the extracted functions see the shipped table.
const famStart = SRC.indexOf('const RECEIVER_FAMILIES');
const famEnd = SRC.indexOf('function expectedAuthservIds');
if (famStart === -1 || famEnd === -1) throw new Error('RECEIVER_FAMILIES table not found');
const fnSrc = SRC.slice(famStart, famEnd) + '\n' + [
  'domainOf', 'domainsAligned', 'topAuthResultsLine', 'stripAuthResultsCfws',
  'expectedAuthservIds', 'parseAuthResultsClauses', 'inboundAuthPasses',
].map((n) => extractFn(SRC, n)).join('\n\n');

t('helper functions present in daemon.js', /function inboundAuthPasses/.test(fnSrc));

// Build the extracted helpers bound to an injectable currentSettings().
function makeHelpers(settings) {
  const factory = new Function(
    'currentSettings',
    `${fnSrc}\n; return { domainOf, domainsAligned, topAuthResultsLine, stripAuthResultsCfws, expectedAuthservIds, parseAuthResultsClauses, inboundAuthPasses };`,
  );
  return factory(() => settings);
}

function rawMail(headerLines, from = 'Owner <owner@example.com>') {
  return [...headerLines, `From: ${from}`, 'To: bot@bot.tld', 'Subject: hi', '', 'body', '']
    .join('\r\n');
}

// R2-B2: the receiver identity is derived from the mailbox being read
// (imap_host), so every "well-known receiver" case below states its host.
const GMAIL = { imap_host: 'imap.gmail.com' };

(async () => {
  const H = makeHelpers(GMAIL);

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
    const Hp = makeHelpers({ ...GMAIL, auth_results_authserv_id: 'mx.mycorp.com' });
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

  // 12. F-B1 (2026-09-07): TWO dkim clauses — the From-aligned d= belongs to a
  //     FAILED signature, the pass belongs to an unrelated domain. The old
  //     whole-line regex paired them and forged an owner. Per-clause parse
  //     must drop it.
  console.log('\n[dkim=fail d=aligned; dkim=pass d=unaligned → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=fail header.d=example.com header.s=s1; dkim=pass header.d=attacker.tld header.s=x',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false (aligned d= from a failed clause is not a pass)', r.ok === false, r.reason);
  }

  // 13. F-B1: dmarc=fail + aligned dkim=pass → the receiver's DMARC verdict
  //     wins; the DKIM fallback must NOT reopen the gate.
  console.log('\n[dmarc=fail + dkim=pass aligned → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.d=example.com; dmarc=fail (p=reject) header.from=example.com',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false on dmarc=fail despite dkim=pass', r.ok === false, r.reason);
    t('reason names dmarc verdict', /dmarc=fail/.test(r.reason), r.reason);
  }

  // 14. F-B1: header.i=user@dom — the domain (not the local-part) must be the
  //     alignment candidate. Old regex captured "attacker" as a domain here
  //     and "owner" for the legitimate case (never aligned → false negative).
  console.log('\n[header.i=local@domain → domain part is the candidate]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.i=owner@example.com header.s=s1',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true (domain of header.i aligned)', r.ok === true, r.reason);
    const p2 = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.i=example.com@attacker.tld',
    ]));
    const r2 = H.inboundAuthPasses(p2, 'owner@example.com');
    t('ok=false (local-part that looks like the From domain is ignored)', r2.ok === false, r2.reason);
  }

  // 15. F-B1: dkim=pass clause aligned, an unrelated dkim=fail clause (e.g. a
  //     list re-sign) must not poison it — still authorized.
  console.log('\n[dkim=pass aligned + dkim=fail unrelated → authorized]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.d=example.com; dkim=fail header.d=lists.tld',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true', r.ok === true, r.reason);
  }

  // ── R2-B1 (2026-09-07): RFC 5322 comments / quoted strings ─────────────────
  // 16. a CFWS comment carrying `header.d=<aligned>` on an UNALIGNED pass
  console.log('\n[R2-B1: comment carries header.d=aligned → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.d=evil.com (comment dkim=pass header.d=example.com)',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false (comment content is not a property)', r.ok === false, r.reason);
  }

  // 17. quoted-string local-part of header.i that spells `header.d=example.com`
  console.log('\n[R2-B1: header.i="header.d=example.com"@evil.com → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=pass header.i="header.d=example.com"@evil.com header.s=sel',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false (quoted local-part is not a property)', r.ok === false, r.reason);
    const clauses = H.parseAuthResultsClauses(H.topAuthResultsLine(p).toLowerCase());
    t('parsed domain is the real one (evil.com)',
      clauses.length === 1 && clauses[0].domains.join(',') === 'evil.com', JSON.stringify(clauses));
  }

  // 18. a comment containing `;` must not open a new (forged) clause
  console.log('\n[R2-B1: comment with ";" does not split a clause → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; dkim=fail header.d=example.com (x; dkim=pass header.d=example.com)',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false', r.ok === false, r.reason);
  }

  // 19. dkim=pass that exists ONLY inside a comment of another clause
  console.log('\n[R2-B1: dkim=pass only inside a comment → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.google.com; spf=fail ( dkim=pass header.d=example.com )',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false', r.ok === false, r.reason);
  }

  // 20. a leading comment must not become the authserv-id; a real, aligned
  //     pass with harmless comments elsewhere is still accepted (no false
  //     negatives from the stripper).
  console.log('\n[R2-B1: comments stripped, legitimate stamp still accepted]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: (receiver) mx.google.com (v1); dkim=pass (2048-bit key; unprotected) header.d=example.com header.s=s1; dmarc=pass (p=reject) header.from=example.com',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true', r.ok === true, r.reason);
    const stripped = H.stripAuthResultsCfws('a (b (c) "d") "q;x" e').replace(/\s+/g, ' ');
    t('stripper removes nested comments and quotes', stripped === 'a e', JSON.stringify(stripped));
  }

  // 21. property names are token-anchored: `xheader.d=` is not `header.d=`
  console.log('\n[R2-B1: xheader.d= is not header.d=]');
  {
    const clauses = H.parseAuthResultsClauses('authentication-results: mx.google.com; dkim=pass xheader.d=example.com header.d=evil.com');
    t('only the anchored property is a candidate',
      clauses.length === 1 && clauses[0].domains.join(',') === 'evil.com', JSON.stringify(clauses));
  }

  // ── R2-B2 (2026-09-07): receiver identity derived from imap_host ───────────
  // 22. no pin AND no known imap_host → fail-closed even for a "google" id
  console.log('\n[R2-B2: no pin, unknown/unset imap_host → drop despite mx.google.com]');
  {
    for (const settings of [{}, { imap_host: 'mail.selfhosted.example' }]) {
      const Hx = makeHelpers(settings);
      const p = await simpleParser(rawMail([
        'Authentication-Results: mx.google.com; dmarc=pass header.from=example.com',
      ]));
      const r = Hx.inboundAuthPasses(p, 'owner@example.com');
      t(`ok=false for settings=${JSON.stringify(settings)}`, r.ok === false, r.reason);
      t('reason tells the operator to pin', /auth_results_authserv_id/.test(r.reason), r.reason);
    }
  }

  // 23. cross-family forgery: reading Gmail, stamped "by" Outlook → drop
  console.log('\n[R2-B2: imap_host=gmail, authserv-id=outlook → drop]');
  {
    const p = await simpleParser(rawMail([
      'Authentication-Results: protection.outlook.com; dmarc=pass header.from=example.com',
    ]));
    const r = H.inboundAuthPasses(p, 'owner@example.com');
    t('ok=false (wrong receiver family)', r.ok === false, r.reason);
  }

  // 24. same family, different mailbox host → accepted (iCloud, Outlook)
  console.log('\n[R2-B2: family match → accepted]');
  {
    const cases = [
      ['imap.mail.me.com', 'Authentication-Results: icloud.com; dmarc=pass header.from=example.com'],
      ['outlook.office365.com', 'Authentication-Results: mx.protection.outlook.com; dmarc=pass header.from=example.com'],
    ];
    for (const [host, ar] of cases) {
      const Hx = makeHelpers({ imap_host: host });
      const p = await simpleParser(rawMail([ar]));
      const r = Hx.inboundAuthPasses(p, 'owner@example.com');
      t(`ok=true for imap_host=${host}`, r.ok === true, r.reason);
    }
    t('expectedAuthservIds(imap.gmail.com) is the Google family',
      (H.expectedAuthservIds('imap.gmail.com') || []).includes('google.com'));
    t('expectedAuthservIds(unknown) is null', H.expectedAuthservIds('mail.selfhosted.example') === null);
  }

  // 25. an explicit pin overrides the family (self-hosted behind a gateway)
  console.log('\n[R2-B2: pin wins over the family table]');
  {
    const Hp = makeHelpers({ imap_host: 'mail.selfhosted.example', auth_results_authserv_id: 'mx.mimecast.com' });
    const p = await simpleParser(rawMail([
      'Authentication-Results: mx.mimecast.com; dmarc=pass header.from=example.com',
    ]));
    const r = Hp.inboundAuthPasses(p, 'owner@example.com');
    t('ok=true when the top authserv-id matches the pin', r.ok === true, r.reason);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})();
