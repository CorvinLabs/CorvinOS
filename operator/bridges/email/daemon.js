#!/usr/bin/env node
// daemon.js — Email frontend, drop-in next to whatsapp/telegram/discord/slack.
// Same shared inbox/outbox JSON contract. Inbound messages tagged
// channel:"email". Adapter routes the reply back to the sender via
// chat_id (= the From-address, normalised to lowercase).
//
// Inbound : IMAP IDLE / poll → mailparser → write to bridges/shared/inbox/
//           - text body and HTML→text fallback
//           - attachments saved into ./attachments/<msgId>/<safe-name>
//             and forwarded as image_path / audio_path / document_path
// Outbound: bridges/shared/outbox → SMTP via nodemailer. Plain text body
//           + any voice_path/image_path/document_path/video_path attached.
//
// Config (settings.json):
//   imap_host, imap_port, imap_secure, imap_user, imap_password, imap_mailbox
//   smtp_host, smtp_port, smtp_secure, smtp_user, smtp_password
//   from_address, from_name, subject_prefix, whitelist, rate_limit_per_hour
//
// Use an app-specific password (Gmail / iCloud / Outlook all support it).

const fs   = require('fs');
const path = require('path');
const crypto = require('crypto');

require('../shared/js/bridge_state').exitIfDisabled('email');

const { ImapFlow } = require('imapflow');
const { simpleParser } = require('mailparser');
const nodemailer = require('nodemailer');

const { makeLogger }            = require('../shared/js/logger');
const { makeSettingsAccessor }  = require('../shared/js/settings');
const { makeAuth }              = require('../shared/js/auth');
const { startHealthServer }     = require('../shared/js/health-server');
const { startEventLoopWatchdog } = require('../shared/js/event-loop-watchdog');
const { makeAnnouncer }         = require('../shared/js/local-announce');
const { newMsgId }              = require('../shared/js/msg-id');
const { writeInboxAtomic }      = require('../shared/js/inbox_write');
const inChatCmds                = require('../shared/js/in_chat_commands');
const { bridgeSettingsPath }    = require('../shared/js/bridge_paths');
const { countPending }          = require('../shared/js/outbox');
const {
  normalizeExecutionContext, shouldRenderContext,
  formatEngineId, formatDelegationMode, formatDuration, formatTokens,
} = require('../shared/js/execution_context_renderer');

const ROOT = __dirname;
const PLUGIN_ROOT = path.resolve(ROOT, '..', '..');
const SHARED = path.resolve(ROOT, '..', 'shared');
const INBOX  = path.join(SHARED, 'inbox');
const OUTBOX = path.join(SHARED, 'outbox');
const ATTACH = path.join(ROOT, 'attachments');
// ADR-0008 §8.3: settings live in <corvin_home>/bridges/email/.
const SETTINGS_FILE = (ch => {
  const can = bridgeSettingsPath(ch);
  const leg = path.join(ROOT, 'settings.json');
  if (!fs.existsSync(can) && fs.existsSync(leg)) {
    try { fs.mkdirSync(path.dirname(can), { recursive: true }); fs.copyFileSync(leg, can); } catch {}
  }
  return fs.existsSync(can) ? can : leg;
})('email');
const CHANNEL = 'email';
for (const d of [INBOX, OUTBOX, ATTACH]) fs.mkdirSync(d, { recursive: true });

const HTTP_PORT = parseInt(process.env.EMAIL_HTTP_PORT || '7895', 10);

const log = makeLogger('email');
const { loadSettings, currentSettings } = makeSettingsAccessor(SETTINGS_FILE, log);
const settings = loadSettings();
const { rateAllow, authOk, classify } = makeAuth({
  settingsFile: SETTINGS_FILE, currentSettings, loadSettings, logger: log,
  channel: CHANNEL,
  // PENTEST-3c: email is a *public* address. An empty whitelist must NOT make
  // the entire internet an owner — fail closed. The owner still claims access
  // via `/auth <pin>`; `dev_mode: true` in settings.json re-opens the legacy
  // behaviour for local testing only.
  denyOnEmptyWhitelist: true,
});
const announce = makeAnnouncer({
  pluginRoot: PLUGIN_ROOT, channelLabel: 'Email', currentSettings, logger: log,
});

// Credential resolution order: channel-specific env → settings.json →
// GMAIL_APP_PASSWORD (F-B7, 2026-09-07). The Gmail app password is the ONE
// secret most operators already export for the rest of CorvinOS (a2a_worker /
// ACS pass it through); honouring it here means the password need not be
// copied into settings.json at all. Env-only secrets never touch disk.
const GMAIL_APP_PASSWORD = process.env.GMAIL_APP_PASSWORD || '';
const IMAP_USER = process.env.EMAIL_IMAP_USER || settings.imap_user || process.env.GMAIL_USER;
const IMAP_PASS = process.env.EMAIL_IMAP_PASSWORD || settings.imap_password || GMAIL_APP_PASSWORD;
const SMTP_USER = process.env.EMAIL_SMTP_USER || settings.smtp_user || IMAP_USER;
const SMTP_PASS = process.env.EMAIL_SMTP_PASSWORD || settings.smtp_password || GMAIL_APP_PASSWORD;

if (!IMAP_USER || !IMAP_PASS || !SMTP_USER || !SMTP_PASS) {
  log('FATAL: imap_user / imap_password / smtp_user / smtp_password all required');
  log('       (env vars EMAIL_IMAP_USER etc., GMAIL_APP_PASSWORD, or settings.json)');
  process.exit(1);
}

function writeInbox(payload) {
  const id = newMsgId();
  // Atomic tmp+rename (F-B3) — the adapter's 1 Hz `*.json` poll must never see
  // a half-written envelope; see shared/js/inbox_write.js.
  writeInboxAtomic(INBOX, id, { id, channel: CHANNEL, ...payload });
  const kind = payload.audio_path ? 'voice'
             : payload.image_path ? 'image'
             : payload.document_path ? 'document'
             : payload.video_path ? 'video' : 'text';
  log(`inbox: ${id} from=${addrFp(payload.from)} kind=${kind}`);
  announce(payload, kind);
  return id;
}

// F-B10 (2026-09-07): log lines never carry a sender/recipient address —
// an email address is PII (GDPR Art. 4(1)) and voice.log is long-lived and
// often pasted into bug reports. A short one-way fingerprint is enough to
// correlate lines of the same conversation.
function addrFp(addr) {
  return crypto.createHash('sha256').update(String(addr || '').toLowerCase()).digest('hex').slice(0, 12);
}

// F-B6 (2026-09-07): attachment filenames come from the sender. `.` / `..` /
// '' collapsed to a directory or an unwritable path and threw inside
// handleParsed BEFORE the \Seen flag was set — the same mail was re-parsed
// on every poll, forever. Sanitise to a safe basename, never empty, bounded.
const ATTACH_NAME_MAX = 120;
function safeAttachmentName(filename) {
  let base = String(filename || '').split(/[\\/]/).pop() || '';
  base = base.replace(/[^a-zA-Z0-9._-]/g, '_').replace(/^\.+/, '');
  if (!base || base === '.' || base === '..') base = 'file';
  if (base.length > ATTACH_NAME_MAX) {
    const dot = base.lastIndexOf('.');
    const ext = dot > 0 && base.length - dot <= 16 ? base.slice(dot) : '';
    base = base.slice(0, ATTACH_NAME_MAX - ext.length) + ext;
  }
  return base;
}

// F-B6: processed-UID state. The daemon used to rely on the \Seen flag alone
// as its "already handled" memory, which (a) marked rejected/spoofed mail as
// read for the human owner too and (b) re-looped on any throw before the
// flag was set. The state file is the daemon's own memory: a UID is recorded
// after a DECISION was reached (accepted, rejected, or failed), keyed by
// UIDVALIDITY so a mailbox reset starts over. \Seen is now set ONLY for mail
// the bridge actually accepted (inbox write / in-chat reply) — rejected mail
// stays unread in the owner's mailbox, where a human can still look at it.
const IMAP_STATE_FILE = path.join(path.dirname(SETTINGS_FILE), 'imap_state.json');
const IMAP_STATE_MAX_UIDS = 5000;
let imapState = { uidvalidity: null, uids: [] };
let imapStateSet = new Set();
function loadImapState() {
  try {
    const raw = JSON.parse(fs.readFileSync(IMAP_STATE_FILE, 'utf8'));
    if (raw && Array.isArray(raw.uids)) {
      imapState = { uidvalidity: raw.uidvalidity ?? null, uids: raw.uids.map(Number) };
      imapStateSet = new Set(imapState.uids);
    }
  } catch { /* first run */ }
}
function saveImapState() {
  try {
    if (imapState.uids.length > IMAP_STATE_MAX_UIDS) {
      imapState.uids = imapState.uids.slice(-IMAP_STATE_MAX_UIDS);
      imapStateSet = new Set(imapState.uids);
    }
    const tmp = IMAP_STATE_FILE + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(imapState), { mode: 0o600 });
    fs.renameSync(tmp, IMAP_STATE_FILE);
  } catch (e) { log(`imap-state: save failed: ${e.message}`); }
}
function resetImapStateIfMailboxChanged(uidValidity) {
  if (uidValidity == null) return;
  if (imapState.uidvalidity !== null && imapState.uidvalidity !== uidValidity) {
    log(`imap-state: UIDVALIDITY changed ${imapState.uidvalidity} → ${uidValidity}; resetting processed set`);
    imapState = { uidvalidity: uidValidity, uids: [] };
    imapStateSet = new Set();
  } else if (imapState.uidvalidity === null) {
    imapState.uidvalidity = uidValidity;
  }
}
function markUidProcessed(uid) {
  const n = Number(uid);
  if (imapStateSet.has(n)) return;
  imapStateSet.add(n);
  imapState.uids.push(n);
  saveImapState();
}
loadImapState();

function classifyAttachment(filename, contentType) {
  const fn = (filename || '').toLowerCase();
  const ct = (contentType || '').toLowerCase();
  if (ct.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp)$/.test(fn)) return 'image';
  if (ct.startsWith('audio/') || /\.(ogg|mp3|m4a|wav|opus)$/.test(fn))   return 'audio';
  if (ct.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/.test(fn))       return 'video';
  return 'document';
}

function normalizeAddress(a) {
  return (a || '').toLowerCase().trim();
}

// ─── Inbound message authentication (PENTEST-3a) ────────────────────────────
// The RFC5322 `From:` header is trivially forgeable, yet the daemon derives
// identity, routing key AND authorization from it. An attacker who emails
// `From: owner@victim.tld` (a whitelisted address) would otherwise inherit the
// owner's session / memory / consent / quota.
//
// Defence: before trusting `From`, require that the *receiving* MTA/IMAP
// provider (Gmail, iCloud, Outlook, …) stamped an `Authentication-Results`
// header proving the message passed DMARC — or passed DKIM with the signing
// `d=` domain aligned to the From domain. This is fail-CLOSED: if the header
// is absent or shows no aligned pass, the sender is treated as unauthenticated
// and the message is dropped.
//
// Anti-forgery: an attacker can *inject* their own `Authentication-Results`
// line into the raw message. The receiving provider prepends its own line at
// the very top and strips inbound lines bearing its own authserv-id, so ONLY
// the first (top-most) Authentication-Results header — the one added by our
// provider — is trusted. Any attacker-appended lines sit below it and are
// ignored.
//
// Non-stamping-provider gap: the "top line is the provider's" guarantee holds
// ONLY when the receiving IMAP provider actually stamps its own AR line. A
// self-hosted / non-stamping provider stamps NONE, so an attacker who injects a
// single `Authentication-Results: x; dmarc=pass` line makes it the sole (hence
// top) line and inbound-auth would wrongly pass. Defence (fail-closed): the top
// line's authserv-id must be provably the receiver's — it must equal the
// operator-pinned `auth_results_authserv_id`, or (when unset) match a built-in
// allowlist of well-known provider authserv-ids. An unrecognised authserv-id is
// NOT trusted (sender must use the PIN /auth flow). `dev_mode:true` restores the
// legacy open behaviour for local testing.

function domainOf(addr) {
  const s = String(addr || '').toLowerCase().replace(/[>\s]+$/, '').trim();
  const at = s.lastIndexOf('@');
  return at >= 0 ? s.slice(at + 1).trim() : '';
}

// Relaxed DMARC-style alignment without a public-suffix list: exact match, or
// one domain is a sub-domain of the other (covers sub-domain DKIM signers).
function domainsAligned(a, b) {
  a = (a || '').toLowerCase(); b = (b || '').toLowerCase();
  if (!a || !b) return false;
  if (a === b) return true;
  return a.endsWith('.' + b) || b.endsWith('.' + a);
}

// The top-most Authentication-Results line (provider-added). headerLines keeps
// every occurrence in wire order; the receiving provider's line is first.
function topAuthResultsLine(parsed) {
  for (const h of (parsed.headerLines || [])) {
    if (h && String(h.key).toLowerCase() === 'authentication-results') {
      return String(h.line || '');
    }
  }
  return null;
}

function inboundAuthPasses(parsed, fromAddr) {
  const fromDomain = domainOf(fromAddr);
  if (!fromDomain) return { ok: false, reason: 'no-from-domain' };

  const raw = topAuthResultsLine(parsed);
  if (!raw) return { ok: false, reason: 'no-authentication-results' };
  const line = raw.toLowerCase();

  // Invariant: trust the top AR line ONLY if its authserv-id is provably the
  // receiver's — otherwise a non-stamping IMAP provider lets an attacker inject
  // the sole AR line and forge dmarc=pass. authserv-id is the first token after
  // "authentication-results:".
  const m = line.match(/^authentication-results:\s*([^;\s]+)/);
  const authservId = m ? m[1] : '';
  const cs = currentSettings();
  const pinned = (cs.auth_results_authserv_id || '').toString().toLowerCase().trim();
  if (pinned) {
    if (authservId !== pinned) {
      return { ok: false, reason: `authserv-id ${authservId || '(none)'} != pinned ${pinned}` };
    }
  } else if (cs.dev_mode !== true) {
    // No operator pin: fall back to a built-in allowlist of well-known
    // receiving-provider authserv-ids (match = exact host or sub-domain). An
    // authserv-id outside it is NOT a provable receiver → fail-closed.
    const KNOWN_RECEIVERS = [
      'google.com', 'gmail.com',
      'icloud.com', 'me.com', 'apple.com',
      'outlook.com', 'protection.outlook.com', 'hotmail.com', 'office365.com',
      'yahoo.com', 'yahoodns.net',
      'mimecast.com', 'proofpoint.com', 'pphosted.com',
    ];
    const idOk = !!authservId
      && KNOWN_RECEIVERS.some((d) => authservId === d || authservId.endsWith('.' + d));
    if (!idOk) {
      return {
        ok: false,
        reason: `authserv-id ${authservId || '(none)'} not a known receiver `
              + `(set auth_results_authserv_id to your provider's id)`,
      };
    }
  }

  // Per-clause evaluation (adversarial review 2026-09-07, F-B1). An AR line is
  // a `;`-separated list of independent method clauses (RFC 8601 §2.2), e.g.
  //   `dkim=fail header.d=example.com; dkim=pass header.d=attacker.tld; dmarc=fail`
  // A whole-line regex conflated them: `dkim=pass` matched anywhere, then
  // EVERY `header.d=` on the line became an alignment candidate — so the
  // From-aligned `d=` of a FAILED signature paired with an unrelated `pass`
  // and forged an authenticated owner. `header.d` / `header.i` are therefore
  // read ONLY from the clause that itself says `dkim=pass`, and a `dmarc=`
  // verdict other than `pass` closes the gate regardless of any DKIM clause
  // (the receiver already evaluated alignment; we never second-guess a fail).
  const clauses = parseAuthResultsClauses(line);
  const dmarc = clauses.filter((c) => c.method === 'dmarc');
  if (dmarc.some((c) => c.result !== 'pass')) {
    return { ok: false, reason: `dmarc=${dmarc.find((c) => c.result !== 'pass').result}` };
  }
  if (dmarc.length > 0) return { ok: true, reason: 'dmarc=pass' };

  // Fallback (no DMARC verdict at all): a dkim=pass clause whose OWN signing
  // domain is aligned to the From domain.
  let sawDkimPass = false;
  for (const c of clauses) {
    if (c.method !== 'dkim' || c.result !== 'pass') continue;
    sawDkimPass = true;
    for (const d of c.domains) {
      if (domainsAligned(fromDomain, d)) return { ok: true, reason: `dkim=pass d=${d}` };
    }
  }
  if (sawDkimPass) return { ok: false, reason: 'dkim=pass but d= not aligned to From' };

  return { ok: false, reason: 'no-aligned-pass' };
}

// Split an (already lower-cased) Authentication-Results line into method
// clauses. The first `;`-segment is the authserv-id (+ optional version) and
// carries no verdict. Each following segment is `<method>=<result> [props]`;
// `domains` holds the signing-domain candidates of THAT clause only:
// `header.d=<dom>` and the domain part of `header.i=[local@]<dom>` (the
// old regex captured the local-part of `header.i=user@dom`, not the domain).
function parseAuthResultsClauses(line) {
  const body = String(line || '').replace(/^authentication-results:\s*/, '');
  const segs = body.split(';').slice(1);
  const out = [];
  for (const seg of segs) {
    const m = seg.trim().match(/^([a-z0-9_-]+)\s*=\s*([a-z0-9_-]+)/);
    if (!m) continue;
    const domains = [];
    for (const d of seg.matchAll(/header\.d\s*=\s*([a-z0-9._-]+)/g)) domains.push(d[1]);
    for (const i of seg.matchAll(/header\.i\s*=\s*(?:[^@\s;]*@)?([a-z0-9._-]+)/g)) domains.push(i[1]);
    out.push({ method: m[1], result: m[2], domains });
  }
  return out;
}

// ─── IMAP inbound ───────────────────────────────────────────────────────────

const cs = currentSettings();
const imap = new ImapFlow({
  host: cs.imap_host || 'imap.gmail.com',
  port: cs.imap_port || 993,
  secure: cs.imap_secure !== false,
  auth: { user: IMAP_USER, pass: IMAP_PASS },
  logger: false,
});

let imapReady = false;
let connectedAddress = null;

async function pollOnce() {
  if (!imapReady) return;
  const mailbox = (currentSettings().imap_mailbox || 'INBOX');
  const lock = await imap.getMailboxLock(mailbox);
  try {
    resetImapStateIfMailboxChanged(imap.mailbox && imap.mailbox.uidValidity != null
      ? Number(imap.mailbox.uidValidity) : null);
    const unseenAll = await imap.search({ seen: false }, { uid: true });
    const unseen = (unseenAll || []).filter((u) => !imapStateSet.has(Number(u)));
    if (unseen.length === 0) return;
    log(`imap: ${unseen.length} new message(s) in ${mailbox}`);
    for (const uid of unseen) {
      try {
        const { content } = await imap.download(uid, undefined, { uid: true });
        const parsed = await simpleParser(content);
        await handleParsed(parsed, uid);
      } catch (e) {
        log(`imap: failed to handle uid ${uid}: ${e.message}`);
      } finally {
        // A decision was reached (accepted / rejected / threw) — never look at
        // this UID again, whatever the \Seen flag says (F-B6).
        markUidProcessed(uid);
      }
    }
  } finally {
    lock.release();
  }
}

// \Seen is set ONLY for mail the bridge accepted (F-B6) — see IMAP_STATE_FILE.
async function markAccepted(uid) {
  try { await imap.messageFlagsAdd(uid, ['\\Seen'], { uid: true }); }
  catch (e) { log(`imap: \\Seen flag failed for uid ${uid}: ${e.message}`); }
}

async function handleParsed(parsed, uid) {
  const fromAddr = normalizeAddress(parsed.from?.value?.[0]?.address);
  if (!fromAddr) {
    log('imap: no From address, skipping');
    return;
  }

  // PENTEST-3a: never trust a bare `From` header. Require provider-stamped
  // DMARC/DKIM alignment before this address may act as an authenticated
  // principal (owner / whitelist / PIN claim). Fail-closed: drop spoofable mail.
  const inboundAuth = inboundAuthPasses(parsed, fromAddr);
  if (!inboundAuth.ok) {
    log(`auth: inbound authentication failed for from=${addrFp(fromAddr)} (${inboundAuth.reason}); dropping unverified From (possible spoof)` +
        (inboundAuth.reason && String(inboundAuth.reason).includes('authserv') ?
         ` — your IMAP provider is not in the built-in receiver list; set "auth_results_authserv_id" in settings.json to your provider's Authentication-Results authserv-id to accept its DMARC/DKIM stamps` : ''));
    return;
  }

  const subject = (parsed.subject || '').trim();
  const bodyText = (parsed.text || '').trim()
    || (parsed.html ? parsed.html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim() : '');
  const text = subject
    ? (bodyText ? `${subject}\n\n${bodyText}` : subject)
    : bodyText;

  // PENTEST-3b: throttle FAILED auth attempts BEFORE the PIN compare so a
  // brute-force of `/auth <pin>` cannot run unlimited. A sender who is not yet
  // an owner (unknown, or attempting a PIN claim) consumes rate budget up-front,
  // keyed by the DMARC-verified source address. Established owners are unaffected
  // and keep their normal post-auth budget below.
  if (classify(fromAddr, fromAddr) !== 'owner') {
    if (!rateAllow(fromAddr, currentSettings().rate_limit_per_hour || 30)) {
      log(`rate: blocked pre-auth from=${addrFp(fromAddr)} (throttling brute-force)`);
        return;
    }
  }

  if (!authOk(fromAddr, text, fromAddr)) {
    log(`auth: rejected from=${addrFp(fromAddr)}`);
    return;
  }
  // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure on first encounter.
  // Email has no read-only path, so this is the only disclosure point.
  // Shown once per sender address.
  if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: fromAddr, uid: fromAddr })) {
    const card = inChatCmds.disclosureCardText({
      channel: CHANNEL, ownerLabel: '(owner)',
      hasObserverTranscript: false,
      lang: 'en',
    });
    if (card) {
      // EU AI Act Art. 50: mark the disclosure "seen" ONLY after the card has
      // actually been sent. A transient send failure must NOT persist
      // has_seen=true — otherwise the card would never be re-shown and the
      // sender would never be disclosed to (fails toward NON-disclosure).
      try {
        await sendReply(fromAddr, subject, card, []);
        inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: fromAddr, uid: fromAddr, action: 'pending' });
        log(`disclosure shown addr=${addrFp(fromAddr)}`);
      } catch (e) {
        log(`disclosure send failed addr=${addrFp(fromAddr)}: ${e && e.message || e} (not marking seen; will retry next turn)`);
      }
    }
  }
  if (!rateAllow(fromAddr, currentSettings().rate_limit_per_hour || 30)) {
    log(`rate: blocked from=${addrFp(fromAddr)}`);
    return;
  }

  // /help, /personas, /persona, /schedule, … — the same in-chat dispatcher.
  const cwk = inChatCmds.dispatch({
    text, channel: CHANNEL, chatKey: fromAddr,
    isOwner: true, settingsFile: SETTINGS_FILE,
  });
  if (cwk) {
    await sendReply(fromAddr, subject, cwk.reply, []);
    log(`in-chat-cmd ${cwk.kind} -> from=${addrFp(fromAddr)}`);
    await markAccepted(uid);
    return;
  }

  // /stop /cancel: SIGTERM the currently running claude subprocess (or
  // cancel() a subprocess-less engine) for this chat, same as every other
  // channel daemon. Previously missing here entirely — an email user had
  // no way to abort a stuck/long-running turn (adversarial review finding).
  {
    const cmdLower = (text || '').trim().toLowerCase();
    if (cmdLower === '/stop' || cmdLower === '/cancel' || cmdLower === '/abbruch' || cmdLower === '/halt') {
      log(`cancel cmd from from=${addrFp(fromAddr)}`);
      writeInbox({ from: fromAddr, chat_id: fromAddr, _cancel: true, ts: Date.now(),
                   reply_subject: subject });
      await markAccepted(uid);
      return;
    }
  }

  const base = { from: fromAddr, chat_id: fromAddr, ts: Date.now(),
                 reply_subject: subject };

  // Attachments → split into image / audio / video / document. Email can
  // carry several attachments per message; the adapter handles one media
  // kind at a time, so we pick the first non-trivial one and drop the body
  // as caption. Multiple attachments are queued as separate inbox files.
  const atts = (parsed.attachments || []).filter(a => a.contentDisposition !== 'inline');
  if (atts.length > 0) {
    const id = newMsgId();
    const bag = path.join(ATTACH, id);
    fs.mkdirSync(bag, { recursive: true });
    for (const a of atts) {
      const safe = safeAttachmentName(a.filename);
      const dest = path.join(bag, safe);
      fs.writeFileSync(dest, a.content);
      const kind = classifyAttachment(a.filename, a.contentType);
      const pl = { ...base, caption: text };
      if (kind === 'image')        pl.image_path = dest;
      else if (kind === 'audio')   pl.audio_path = dest;
      else if (kind === 'video')   pl.video_path = dest;
      else { pl.document_path = dest; pl.document_name = a.filename; pl.mimetype = a.contentType; }
      writeInbox(pl);
    }
    await markAccepted(uid);
    return;
  }

  if (text.trim()) {
    writeInbox({ ...base, text });
    await markAccepted(uid);
  }
}

// ─── SMTP outbound ──────────────────────────────────────────────────────────

const smtp = nodemailer.createTransport({
  host: cs.smtp_host || 'smtp.gmail.com',
  port: cs.smtp_port || 465,
  secure: cs.smtp_secure !== false,
  auth: { user: SMTP_USER, pass: SMTP_PASS },
});

async function sendReply(toAddr, replySubject, body, attachments) {
  const c = currentSettings();
  const prefix = c.subject_prefix !== undefined ? c.subject_prefix : '[Claude] ';
  const subject = replySubject
    ? (replySubject.startsWith('Re: ') ? replySubject
       : `Re: ${prefix}${replySubject.replace(/^\[Claude\]\s*/i, '')}`)
    : `${prefix}reply`;
  const from = c.from_name
    ? `"${c.from_name}" <${c.from_address || SMTP_USER}>`
    : (c.from_address || SMTP_USER);
  await smtp.sendMail({
    from, to: toAddr, subject, text: body,
    attachments: (attachments || []).map(p => ({ path: p })),
  });
  log(`smtp: replied to to=${addrFp(toAddr)} (${attachments?.length || 0} attachment(s))`);
}

/**
 * Render execution context as plain text for email.
 * @param {object} context - Normalized execution context
 * @returns {string} - Text footer to append to email body
 */
function renderExecutionContextEmail(context) {
  if (!context) return '';

  const ctx = normalizeExecutionContext(context);
  const lines = [
    '',  // blank line
    '────────────────────',
    `Execution Context:`,
    `Engine: ${formatEngineId(ctx.engine_id)}`,
    `Model: ${ctx.model_name}`,
    `Delegation: ${formatDelegationMode(ctx.delegation_mode)}`,
    `Duration: ${formatDuration(ctx.duration_ms)}`,
  ];

  // Add tokens if available
  if (ctx.tokens_input !== null || ctx.tokens_output !== null) {
    lines.push(`Tokens: in=${formatTokens(ctx.tokens_input)}, out=${formatTokens(ctx.tokens_output)}`);
  }

  // Add tools if > 0
  if (ctx.tool_calls_count > 0) {
    lines.push(`Tools: ${ctx.tool_calls_count}`);
  }

  lines.push('────────────────────');

  return lines.join('\n');
}

// ─── Outbox processing (shared poller wires this in) ────────────────────────

async function processOutboxItem(payload, fpath) {
  const to = payload.chat_id || payload.to;
  if (!to) { log(`no chat_id in ${path.basename(fpath)}, skipping`); return; }
  // Compose attachment list from any file_path fields. The adapter ships them
  // as separate outbox entries, so usually only one is present at a time.
  const attachments = [];
  for (const k of ['voice_path', 'image_path', 'document_path', 'video_path']) {
    if (payload[k] && fs.existsSync(payload[k])) attachments.push(payload[k]);
  }
  let body = payload.text || (attachments.length > 0 ? '(see attachment)' : '');

  // Phase 4 K=2: Append execution context to email body if available and enabled
  if (shouldRenderContext(payload, currentSettings())) {
    try {
      const footer = renderExecutionContextEmail(payload.execution_context);
      if (footer) {
        body = body + footer;
        log(`execution context appended to email for ${to}`);
      }
    } catch (e) {
      log(`execution context footer failed: ${e && e.message || e}`);
    }
  }

  await sendReply(to, payload.reply_subject || '', body, attachments);
}

let outboxRunning = false;
setInterval(() => {
  if (outboxRunning) return;
  outboxRunning = true;
  Promise.resolve().then(async () => {
    let files = [];
    try { files = fs.readdirSync(OUTBOX).filter(f => f.endsWith('.json')).sort(); } catch { return; }
    for (const f of files) {
      const fpath = path.join(OUTBOX, f);
      let payload;
      try { payload = JSON.parse(fs.readFileSync(fpath, 'utf8')); }
      catch (e) { log(`bad json ${f}: ${e.message}`); fs.unlinkSync(fpath); continue; }
      if ((payload.channel || 'whatsapp') !== CHANNEL) continue;
      try {
        await processOutboxItem(payload, fpath);
        fs.unlinkSync(fpath);
      } catch (e) {
        log(`smtp send failed for ${f}: ${e.message}`);
      }
    }
  })
    .catch(e => log(`outbox tick error: ${e.message}`))
    .finally(() => { outboxRunning = false; });
}, 1000);

// ─── HTTP /status ───────────────────────────────────────────────────────────

startEventLoopWatchdog({ logger: log });
startHealthServer({
  port: HTTP_PORT, kind: CHANNEL, logger: log,
  getStatus: () => ({
    kind: 'email',
    paired: imapReady,
    address: connectedAddress,
    whitelist_size: (currentSettings().whitelist || []).length,
    pending_outbox: countPending(OUTBOX, CHANNEL),
  }),
});

// ─── boot ──────────────────────────────────────────────────────────────────

(async () => {
  try {
    await imap.connect();
    imapReady = true;
    connectedAddress = IMAP_USER;
    log(`imap connected as ${addrFp(IMAP_USER)}`);

    // Verify SMTP credentials early so the user gets a clear error if the
    // password is wrong, instead of a silent send-failure on the first reply.
    try {
      await smtp.verify();
      log(`smtp ready as ${addrFp(SMTP_USER)}`);
    } catch (e) {
      log(`WARNING: smtp.verify failed: ${e.message} (replies may not go through)`);
    }

    // Poll loop. IDLE would be nicer but every imapflow IDLE drops back to
    // poll on movement anyway, and a 30s poll is plenty for an email bridge.
    const intervalMs = (currentSettings().imap_poll_seconds || 30) * 1000;
    setInterval(() => {
      pollOnce().catch(e => log(`poll error: ${e.message}`));
    }, intervalMs);
    pollOnce().catch(() => {});  // initial run
  } catch (e) {
    log(`FATAL: imap connect failed: ${e.message}`);
    process.exit(1);
  }
})();

process.on('unhandledRejection', r => log(`unhandledRejection: ${r && r.message || r}`));
process.on('SIGINT',  () => { log('shutting down'); imap.logout().finally(() => process.exit(0)); });
process.on('SIGTERM', () => { log('shutting down'); imap.logout().finally(() => process.exit(0)); });
