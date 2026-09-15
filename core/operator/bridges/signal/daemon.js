#!/usr/bin/env node
// daemon.js — Signal bridge via signal-cli REST API.
//
// Architecture:
//   Signal network → signal-cli (self-hosted) → REST API → this daemon
//   → shared/inbox   (inbound messages)
//   shared/outbox → this daemon → REST API → signal-cli → Signal network
//
// Requires signal-cli-rest-api running locally:
//   https://github.com/bbernhard/signal-cli-rest-api
//   Default: http://localhost:8080
//
// Auth model: phone numbers (E.164) in the whitelist.
//
// Credentials (env or settings.json):
//   SIGNAL_NUMBER      — your registered Signal phone number (+49…)
//   SIGNAL_API_URL     — signal-cli REST API base URL (default http://localhost:8080)
//
// Node ≥18 required (built-in fetch used — no npm dependencies).

'use strict';

const fs   = require('fs');
const path = require('path');

// ── Shared bridge runtime ─────────────────────────────────────────────────────
require('../shared/js/bridge_state').exitIfDisabled('signal');
const { bridgeSettingsPath } = require('../shared/js/bridge_paths');
const { makeLogger }           = require('../shared/js/logger');
const { makeSettingsAccessor } = require('../shared/js/settings');
const { makeAuth }             = require('../shared/js/auth');
const { startOutboxPoller, countPending } = require('../shared/js/outbox');
const { startHealthServer }    = require('../shared/js/health-server');
const { makeAnnouncer }        = require('../shared/js/local-announce');

const { makeHandler, normPhone } = require('./handler');

// ── Paths ─────────────────────────────────────────────────────────────────────
const ROOT          = __dirname;
const PLUGIN_ROOT   = path.resolve(ROOT, '..', '..');
const SHARED        = path.resolve(ROOT, '..', 'shared');
const INBOX         = path.join(SHARED, 'inbox');
const OUTBOX        = path.join(SHARED, 'outbox');
// ADR-0008 §8.3: settings live in <corvin_home>/bridges/signal/.
// Auto-migrate from legacy in-repo location on first boot.
const SETTINGS_FILE = (c => {
  const can = bridgeSettingsPath(c);
  const leg = path.join(ROOT, 'settings.json');
  if (!fs.existsSync(can) && fs.existsSync(leg)) {
    try { fs.mkdirSync(path.dirname(can), { recursive: true }); fs.copyFileSync(leg, can); } catch {}
  }
  return fs.existsSync(can) ? can : leg;
})('signal');
const CHANNEL       = 'signal';
const HEALTH_PORT   = parseInt(process.env.SIGNAL_HEALTH_PORT || '7896', 10);
const POLL_MS       = parseInt(process.env.SIGNAL_POLL_MS     || '500',  10);

for (const d of [INBOX, OUTBOX]) fs.mkdirSync(d, { recursive: true });

// ── Boot ──────────────────────────────────────────────────────────────────────
const log = makeLogger('signal');
const { loadSettings, currentSettings } = makeSettingsAccessor(SETTINGS_FILE, log);
const settings = loadSettings();

const SIGNAL_NUMBER  = normPhone(process.env.SIGNAL_NUMBER  || settings.signal_number  || '');
const SIGNAL_API_URL = (process.env.SIGNAL_API_URL || settings.signal_api_url || 'http://localhost:8080').replace(/\/$/, '');

if (!SIGNAL_NUMBER) {
  log('FATAL: SIGNAL_NUMBER required (env var or settings.json: signal_number)');
  process.exit(1);
}

// ── signal-cli REST API client ────────────────────────────────────────────────

async function apiGet(urlPath) {
  const res = await fetch(`${SIGNAL_API_URL}${urlPath}`, { method: 'GET' });
  if (!res.ok) throw new Error(`signal-cli GET ${urlPath} → HTTP ${res.status}`);
  return res.json();
}

async function apiPost(urlPath, body) {
  const res = await fetch(`${SIGNAL_API_URL}${urlPath}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(`signal-cli POST ${urlPath} → HTTP ${res.status}: ${txt}`);
  }
  return res.status === 204 ? null : res.json().catch(() => null);
}

async function apiDelete(urlPath, body) {
  const res = await fetch(`${SIGNAL_API_URL}${urlPath}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(`signal-cli DELETE ${urlPath} → HTTP ${res.status}: ${txt}`);
  }
  return res.status === 204 ? null : res.json().catch(() => null);
}

async function receiveMessages() {
  // signal-cli REST API v1: GET /v1/receive/<number>
  // Returns array of envelope objects.
  const envelopes = await apiGet(`/v1/receive/${encodeURIComponent(SIGNAL_NUMBER)}`);
  return Array.isArray(envelopes) ? envelopes : [];
}

async function sendMessage(recipient, message, opts = {}) {
  // signal-cli REST API v2: POST /v2/send. Passing `edit_timestamp` (the
  // `timestamp` returned by a previous send) makes this an in-place edit of
  // that earlier message instead of a new one — used for the sticky
  // progress mechanism (shared/js/sticky_progress.js). Returns the parsed
  // response ({ timestamp }) so the caller can remember it for a later edit
  // or remote-delete.
  const body = {
    message:    String(message),
    number:     SIGNAL_NUMBER,
    recipients: [String(recipient)],
  };
  if (opts.editTimestamp) body.edit_timestamp = Number(opts.editTimestamp);
  return apiPost('/v2/send', body);
}

async function remoteDeleteMessage(recipient, timestamp) {
  // signal-cli REST API: DELETE /v1/remote-delete/<number>. Deletes (for
  // everyone) a message this account previously sent, identified by its
  // send timestamp — used to remove the sticky progress message once the
  // real reply is ready to ship.
  return apiDelete(`/v1/remote-delete/${encodeURIComponent(SIGNAL_NUMBER)}`, {
    recipient: String(recipient),
    timestamp: Number(timestamp),
  });
}

// ── Auth ──────────────────────────────────────────────────────────────────────
const auth = makeAuth({
  settingsFile: SETTINGS_FILE, currentSettings, loadSettings, logger: log,
  normalize: normPhone,
  channel: CHANNEL,
  // 2026-08-02: without this, an empty whitelist meant EVERY sender was
  // owner, forever, with no lock — the same gap Discord/WhatsApp/Email each
  // fixed with their own bespoke mechanism. This is the shared equivalent
  // (see auth.js's lockFirstSender doc): first sender claims the whitelist,
  // persisted, everyone after is denied.
  lockFirstSender: true,
});

// ── Local desktop announce ────────────────────────────────────────────────────
const announce = makeAnnouncer({
  pluginRoot: PLUGIN_ROOT, channelLabel: 'Signal', currentSettings, logger: log,
});

// ── Handler ───────────────────────────────────────────────────────────────────
const { handleEnvelope, processOutboxPayload } = makeHandler({
  inboxDir:        INBOX,
  settingsFile:    SETTINGS_FILE,
  currentSettings,
  auth,
  logger:          log,
  sendSignal:      sendMessage,
  deleteSignal:    remoteDeleteMessage,
});

// ── Inbound polling loop ──────────────────────────────────────────────────────
let pollRunning = false;
let paired      = false;
let lastPollErr = null;

async function pollTick() {
  let envelopes;
  try {
    envelopes = await receiveMessages();
    paired      = true;
    lastPollErr = null;
  } catch (e) {
    lastPollErr = e.message;
    if (!paired) log(`signal-cli unreachable: ${e.message}`);
    return;
  }

  for (const env of envelopes) {
    try {
      const sender = env.envelope?.source || '';
      if (!auth.rateAllow(sender)) {
        log(`rate limit exceeded for ${sender}`);
        continue;
      }
      const msgId = await handleEnvelope(env);
      if (msgId) {
        try { announce({ from: sender, chat_id: sender }, 'text'); } catch {}
      }
    } catch (e) {
      log(`envelope handler error: ${e.message}`);
    }
  }
}

const pollHandle = setInterval(() => {
  if (pollRunning) return;
  pollRunning = true;
  pollTick().catch((e) => log(`poll tick error: ${e.message}`))
            .finally(() => { pollRunning = false; });
}, POLL_MS);

// ── Outbox poller ─────────────────────────────────────────────────────────────
// Delivery liveness (incidents 2026-07-26 + 2026-07-27). `paired` and an open
// socket say nothing about whether the outbox is draining: a wedged tick
// (`poller_stalled_s`) delivered nothing for 38 minutes on Discord, and a
// preCheck that keeps refusing (`precheck_stalled_s`) held 5 replies for 90
// minutes without tripping the first field. Both counters lived in the shared
// poller but only Discord's health endpoint read them, so the other four
// poller-backed bridges had the same blind spot with no way to see it.
const outboxPoller = startOutboxPoller({
  outboxDir: OUTBOX,
  channel:   CHANNEL,
  sendFn:    (payload, _fpath) => processOutboxPayload(payload),
  logger:    log,
});

// ── Health server ─────────────────────────────────────────────────────────────
startHealthServer({
  port: HEALTH_PORT, kind: 'signal', logger: log,
  getStatus: () => ({
    paired,
    signal_number:  SIGNAL_NUMBER ? SIGNAL_NUMBER.slice(0, 4) + '…' : 'not set',
    api_url:        SIGNAL_API_URL,
    last_poll_err:  lastPollErr || null,
    whitelist_size: (currentSettings().whitelist || []).length,
    pending_outbox: countPending(OUTBOX, CHANNEL),
    // See the comment on `const outboxPoller` above — these two are the only
    // signals a watchdog has that delivery has stopped while everything else
    // still looks healthy.
    poller_stalled_s:   outboxPoller.stats().stalled_s,
    precheck_stalled_s: outboxPoller.stats().precheck_stalled_s,
  }),
});

log(`Signal bridge started — number=${SIGNAL_NUMBER.slice(0, 4)}… api=${SIGNAL_API_URL}`);

// ── Graceful shutdown ─────────────────────────────────────────────────────────
process.on('unhandledRejection', (r) => log(`unhandledRejection: ${r && r.message || r}`));
process.on('SIGINT',  () => { clearInterval(pollHandle); log('shutting down'); process.exit(0); });
process.on('SIGTERM', () => { clearInterval(pollHandle); log('shutting down'); process.exit(0); });
