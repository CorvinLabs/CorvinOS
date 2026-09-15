#!/usr/bin/env node
// daemon.js — Microsoft Teams bridge via Bot Framework SDK (Multi-Tenant).
//
// Architecture:
//   Teams Cloud → HTTPS webhook → CloudAdapter → handler.js → shared/inbox
//   shared/outbox → startOutboxPoller → sendTeams() → adapter.continueConversation
//
// Auth model: Multi-Tenant Azure App Registration (one central Corvin app).
// Whitelist: UPN / e-mail addresses (user@company.com), readable and GDPR-auditable.
//
// Credentials (env or settings.json):
//   TEAMS_APP_ID       — Microsoft App ID (Azure App Registration)
//   TEAMS_APP_PASSWORD — Client Secret
//   TEAMS_APP_TENANT   — 'common' for multi-tenant (default) or a specific tenant GUID
//
// Hosting: Caddy reverse-proxy → https://teams.corvin.ai → localhost:3978
//   Port configurable via TEAMS_HTTP_PORT (default 3978).
//
// ConversationRef persistence: in-memory Map. On restart the user must send one
// message to re-register; proactive sends before that are queued-and-dropped.

'use strict';

const fs   = require('fs');
const path = require('path');

// ── Bot Framework ─────────────────────────────────────────────────────────────
const {
  CloudAdapter,
  ConfigurationBotFrameworkAuthentication,
  TurnContext,
} = require('botbuilder');

// ── Express ───────────────────────────────────────────────────────────────────
const express = require('express');

// ── Shared bridge runtime ─────────────────────────────────────────────────────
require('../shared/js/bridge_state').exitIfDisabled('teams');
const { bridgeSettingsPath } = require('../shared/js/bridge_paths');
const { makeLogger }           = require('../shared/js/logger');
const { makeSettingsAccessor } = require('../shared/js/settings');
const { makeAuth }             = require('../shared/js/auth');
const { startOutboxPoller, countPending } = require('../shared/js/outbox');
const { startHealthServer }    = require('../shared/js/health-server');
const { startEventLoopWatchdog } = require('../shared/js/event-loop-watchdog');
const { makeAnnouncer }        = require('../shared/js/local-announce');

// ── Teams-specific ────────────────────────────────────────────────────────────
const { makeHandler } = require('./handler');

// ── Paths ─────────────────────────────────────────────────────────────────────
const ROOT        = __dirname;
const PLUGIN_ROOT = path.resolve(ROOT, '..', '..');
const SHARED      = path.resolve(ROOT, '..', 'shared');
const INBOX       = path.join(SHARED, 'inbox');
const OUTBOX      = path.join(SHARED, 'outbox');
// ADR-0008 §8.3: settings live in <corvin_home>/bridges/teams/.
// Auto-migrate from legacy in-repo location on first boot.
const SETTINGS_FILE = (c => {
  const can = bridgeSettingsPath(c);
  const leg = path.join(ROOT, 'settings.json');
  if (!fs.existsSync(can) && fs.existsSync(leg)) {
    try { fs.mkdirSync(path.dirname(can), { recursive: true }); fs.copyFileSync(leg, can); } catch {}
  }
  return fs.existsSync(can) ? can : leg;
})('teams');
const CHANNEL     = 'teams';
const HTTP_PORT   = parseInt(process.env.TEAMS_HTTP_PORT || '3978', 10);
// 7897: 7895 collided with the email daemon's health port — both silently
// fought over the bind when teams + email ran on the same host.
const HEALTH_PORT = parseInt(process.env.TEAMS_HEALTH_PORT || '7897', 10);

for (const d of [INBOX, OUTBOX]) fs.mkdirSync(d, { recursive: true });

// ── Boot ──────────────────────────────────────────────────────────────────────
const log = makeLogger('teams');
const { loadSettings, currentSettings } = makeSettingsAccessor(SETTINGS_FILE, log);
const settings = loadSettings();

const TEAMS_APP_ID       = process.env.TEAMS_APP_ID       || settings.microsoft_app_id       || '';
const TEAMS_APP_PASSWORD = process.env.TEAMS_APP_PASSWORD || settings.microsoft_app_password || '';
const TEAMS_APP_TENANT   = process.env.TEAMS_APP_TENANT   || settings.microsoft_app_tenant   || 'common';

if (!TEAMS_APP_ID || !TEAMS_APP_PASSWORD) {
  log('FATAL: TEAMS_APP_ID and TEAMS_APP_PASSWORD both required');
  log('       (env vars or settings.json: microsoft_app_id + microsoft_app_password)');
  process.exit(1);
}

// ── Bot Framework adapter ─────────────────────────────────────────────────────
const botAuth = new ConfigurationBotFrameworkAuthentication({
  MicrosoftAppId:       TEAMS_APP_ID,
  MicrosoftAppPassword: TEAMS_APP_PASSWORD,
  MicrosoftAppType:     'MultiTenant',
  MicrosoftAppTenantId: TEAMS_APP_TENANT,
});
const adapter = new CloudAdapter(botAuth);

adapter.onTurnError = async (ctx, err) => {
  log(`onTurnError: ${err.message}`);
  try { await ctx.sendActivity('An internal error occurred. Please try again.'); } catch {}
};

// ── Auth ──────────────────────────────────────────────────────────────────────
const auth = makeAuth({
  settingsFile: SETTINGS_FILE, currentSettings, loadSettings, logger: log,
  channel: CHANNEL,
  // 2026-08-02: without this, an empty whitelist meant EVERY sender was
  // owner, forever, with no lock — the same gap Discord/WhatsApp/Email each
  // fixed with their own bespoke mechanism. This is the shared equivalent
  // (see auth.js's lockFirstSender doc): first sender claims the whitelist,
  // persisted, everyone after is denied.
  lockFirstSender: true,
});

// ── Announcer (local-announce: desktop notification on inbound msg) ───────────
const announce = makeAnnouncer({
  pluginRoot: PLUGIN_ROOT, channelLabel: 'Teams', currentSettings, logger: log,
});

// ── ConversationRef store (chatKey → ConversationReference) ──────────────────
// Needed for proactive replies (adapter.continueConversation).
// Limited to 1000 entries to prevent memory leak; oldest entries evicted first.
const conversationRefs = new Map();
const MAX_CONVERSATION_REFS = 1000;

function setConversationRef(chatId, ref) {
  conversationRefs.set(chatId, ref);
  if (conversationRefs.size > MAX_CONVERSATION_REFS) {
    const first = conversationRefs.keys().next().value;
    conversationRefs.delete(first);
  }
}

// ── Handler ───────────────────────────────────────────────────────────────────
const { handleActivity, sendTeams } = makeHandler({
  inboxDir: INBOX,
  settingsFile: SETTINGS_FILE,
  currentSettings,
  auth,
  logger: log,
  conversationRefs,
});

// ── Express app ───────────────────────────────────────────────────────────────
const app = express();

// Bot Framework requires the raw body for JWT signature verification.
app.use((req, res, next) => {
  let data = '';
  req.setEncoding('utf8');
  req.on('data', (chunk) => { data += chunk; });
  req.on('end', () => { req.rawBody = data; next(); });
});

app.post('/api/messages', async (req, res) => {
  await adapter.process(req, res, async (ctx) => {
    // Store ConversationReference on every turn so we can reply proactively.
    const ref = TurnContext.getConversationReference(ctx.activity);
    if (ctx.activity.conversation?.id) {
      setConversationRef(ctx.activity.conversation.id, ref);
    }

    // conversationUpdate: bot added to a chat → send disclosure card.
    if (ctx.activity.type === 'conversationUpdate') {
      const added = ctx.activity.membersAdded || [];
      const isBotAdded = added.some((m) => m.id === ctx.activity.recipient?.id);
      if (isBotAdded) {
        await ctx.sendActivity(
          '👋 Hi! I\'m **Corvin** — an AI assistant.\n\n' +
          'I\'m an AI agent. You can opt out any time with `/leave`.\n\n' +
          'Send `/join` to register, or just say hello if the owner has already added you.',
        );
      }
      return;
    }

    // Rate limiting per sender.
    const sender = ctx.activity.from?.userPrincipalName || ctx.activity.from?.id || '';
    if (sender && !auth.rateAllow(sender)) {
      log(`rate limit exceeded for ${sender}`);
      return;
    }

    const msgId = await handleActivity(ctx);

    if (msgId) {
      // Announce inbound to the local desktop bridge (optional).
      try { announce({ from: ctx.activity.from?.userPrincipalName || '', chat_id: ctx.activity.conversation?.id }, 'text'); }
      catch {}
    }
  });
});

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
  sendFn:    (payload, _fpath) => sendTeams(payload, adapter, TEAMS_APP_ID),
  logger:    log,
});

// ── Health server ─────────────────────────────────────────────────────────────
startEventLoopWatchdog({ logger: log });
startHealthServer({
  port: HEALTH_PORT, kind: 'teams', logger: log,
  getStatus: () => ({
    paired:          conversationRefs.size > 0,
    active_chats:    conversationRefs.size,
    whitelist_size:  (currentSettings().whitelist || []).length,
    app_id:          TEAMS_APP_ID ? TEAMS_APP_ID.slice(0, 8) + '…' : 'not set',
    pending_outbox: countPending(OUTBOX, CHANNEL),
    // See the comment on `const outboxPoller` above — these two are the only
    // signals a watchdog has that delivery has stopped while everything else
    // still looks healthy.
    poller_stalled_s:   outboxPoller.stats().stalled_s,
    precheck_stalled_s: outboxPoller.stats().precheck_stalled_s,
  }),
});

// ── HTTP server ───────────────────────────────────────────────────────────────
const server = app.listen(HTTP_PORT, '0.0.0.0', () => {
  log(`Listening on http://0.0.0.0:${HTTP_PORT}/api/messages`);
  log(`Multi-tenant App ID: ${TEAMS_APP_ID ? TEAMS_APP_ID.slice(0, 8) + '…' : 'NOT SET'}`);
});

// ── Graceful shutdown ─────────────────────────────────────────────────────────
process.on('unhandledRejection', (r) => log(`unhandledRejection: ${r && r.message || r}`));
process.on('SIGINT',  () => { log('shutting down'); server.close(); process.exit(0); });
process.on('SIGTERM', () => { log('shutting down'); server.close(); process.exit(0); });
