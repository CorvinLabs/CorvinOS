#!/usr/bin/env node
// daemon.js — Discord-Bot frontend, drop-in replacement for the Telegram /
// WhatsApp daemons. Uses the same shared inbox/outbox in bridges/shared/.
// All inbound messages are tagged channel:"discord". Adapter routes the
// reply back via the chat_id (= Discord channel ID).
//
// Setup — a bot token is ALL that is required:
//   1. https://discord.com/developers/applications → New Application →
//      Bot tab → Reset Token → copy.
//   2. OAuth2 → URL Generator → scopes: bot. Permissions: Read Messages,
//      Send Messages, Attach Files, Read Message History. Copy URL, open
//      it in browser, invite bot to your server (or to a personal server).
//   3. Put token in settings.json -> discord_token.
//   4. Run via systemd or `node daemon.js`.
//
// Optional:
//   - "MESSAGE CONTENT INTENT" (Privileged Gateway Intents in the portal):
//     WITHOUT it the bot still reads DMs and @mentions in full — enable it
//     only if the bot should read ALL guild-channel text. The daemon
//     preflights the portal state via REST and requests the privileged
//     intent only when it is actually granted (intent_preflight.js), so a
//     fresh app with the toggle off boots cleanly in token-only mode.
//   - whitelist: empty = first sender becomes owner (DEV mode).

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// Enable/disable gate — exit-0 before loading discord.js if the channel
// has been turned off via the Bridges console (state.json).
require('../shared/js/bridge_state').exitIfDisabled('discord');

const { Client, GatewayIntentBits, IntentsBitField, Partials, AttachmentBuilder } = require('discord.js');

// ── Shared bridge runtime (Phase 2 refactor) ────────────────────────────────
const { makeLogger }            = require('../shared/js/logger');
const { makeSettingsAccessor }  = require('../shared/js/settings');
const { makeAuth }              = require('../shared/js/auth');
const { AutoOwnershipBridge }   = require('./auto_ownership');
const { startOutboxPoller, countPending } = require('../shared/js/outbox');
const { isNetworkError, networkUp } = require('../shared/js/net_probe');
const { startHealthServer }     = require('../shared/js/health-server');
const { makeAnnouncer }         = require('../shared/js/local-announce');
const { newMsgId }              = require('../shared/js/msg-id');
const { makeStickyProgress }    = require('../shared/js/sticky_progress');
const inChatCmds                = require('../shared/js/in_chat_commands');
const chatToggle                = require('../shared/js/chat_toggle');
const slashCommands             = require('./slash_commands');
const { bridgeSettingsPath }    = require('../shared/js/bridge_paths');
const { messageContentAvailable } = require('./intent_preflight');
const {
  normalizeExecutionContext, shouldRenderContext,
  getColorForMode, getEmojiForEngine, formatEngineId,
  formatDelegationMode, formatDuration, formatTokens,
} = require('../shared/js/execution_context_renderer');

const ROOT = __dirname;
const PLUGIN_ROOT = path.resolve(ROOT, '..', '..');
const SHARED = path.resolve(ROOT, '..', 'shared');

// say.py TTS helper — mirrors WhatsApp daemon implementation.
// Spawns operator/voice/scripts/say.py to produce an OGG-Opus voice-note.
// Returns the absolute file path on success, null on any silent skip.
const SAY_HELPER = path.resolve(PLUGIN_ROOT, 'voice', 'scripts', 'say.py');

function synthesizeVoiceNoteForText(text, lang = 'de', voice = 'shimmer', timeoutMs = 30000) {
  return new Promise((resolve) => {
    if (!text || !text.trim()) return resolve(null);
    const outPath = path.join(OUTBOX, `welcome_${Date.now()}.ogg`);
    let stdoutBuf = '';
    let stderrBuf = '';
    let resolved = false;
    const finish = (val) => { if (!resolved) { resolved = true; resolve(val); } };
    let child;
    try {
      const pyBin = process.env.PYTHON || 'python3';
      child = spawn(pyBin, [SAY_HELPER, outPath, text, lang, voice], {
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (e) {
      return finish(null);
    }
    const timer = setTimeout(() => {
      try { child.kill('SIGTERM'); } catch {}
      finish(null);
    }, timeoutMs);
    child.stdout.on('data', (b) => { stdoutBuf += b.toString(); });
    child.stderr.on('data', (b) => { stderrBuf += b.toString(); });
    child.on('error', () => { clearTimeout(timer); finish(null); });
    child.on('close', () => {
      clearTimeout(timer);
      const trimmed = stdoutBuf.trim();
      if (!trimmed || !fs.existsSync(trimmed)) return finish(null);
      finish(trimmed);
    });
  });
}
const INBOX  = path.join(SHARED, 'inbox');
const OUTBOX = path.join(SHARED, 'outbox');
// Permanently undeliverable envelopes are parked here instead of being retried
// forever. Lives inside outbox/ but is invisible to the poller, which only picks
// up *.json files. Re-queue by moving an envelope back one level up.
const DEAD_LETTER = path.join(OUTBOX, 'dead');
// ADR-0008 §8.3: settings live in <corvin_home>/bridges/discord/.
// Auto-migrate from legacy in-repo location on first boot.
const SETTINGS_FILE = (ch => {
  const can = bridgeSettingsPath(ch);
  const leg = path.join(ROOT, 'settings.json');
  if (!fs.existsSync(can) && fs.existsSync(leg)) {
    try { fs.mkdirSync(path.dirname(can), { recursive: true }); fs.copyFileSync(leg, can); } catch {}
  }
  return fs.existsSync(can) ? can : leg;
})('discord');
const CHANNEL = 'discord';
for (const d of [INBOX, OUTBOX]) fs.mkdirSync(d, { recursive: true });

const HTTP_PORT = parseInt(process.env.DISCORD_HTTP_PORT || '7893', 10);

const log = makeLogger('discord');
const { loadSettings, currentSettings, saveSettings } =
  makeSettingsAccessor(SETTINGS_FILE, log);
const settings = loadSettings(); // boot snapshot — held mutable for debug-list edits

// V-022: operator_name is required for EU AI Act Art. 50 disclosure quality.
if (!settings.operator_name) {
  settings.operator_name = 'CorvinOS (Discord Bridge)';
  saveSettings(settings);
}
const currentON = currentSettings().operator_name || settings.operator_name || '';
const OPERATOR_NAME = currentON.trim();
if (!OPERATOR_NAME || OPERATOR_NAME === '(owner)') {
  log('[security] V-022: operator_name not set in settings.json — disclosure card will show "(owner)" placeholder. Configure operator_name for Art. 50 compliance.');
}
const { rateAllow, authOk, readOnlyOk } = makeAuth({
  settingsFile: SETTINGS_FILE, currentSettings, loadSettings, logger: log,
  channel: CHANNEL,
});

// 2026-07-30: an empty whitelist previously made authOk() return true for
// EVERY sender, forever (auth.js's legacy fail-open — Discord never opted
// into the shared `denyOnEmptyWhitelist` hardening that the email bridge
// uses, since email's fully-public threat model doesn't apply here). But
// the tested, purpose-built AutoOwnershipBridge (this dir) — which locks
// ownership to the FIRST sender and denies everyone else — was never
// `require()`d anywhere, so its safety net was dead code: a fresh install's
// bot answered literally anyone who found the invite link, indefinitely.
// Wiring it in here keeps the zero-config "just start talking to it" setup
// (no whitelist to configure by hand) while closing the "everyone forever"
// gap: only the first sender is promoted, and that promotion is persisted
// to settings.json so it survives a restart.
const _autoOwnership = new AutoOwnershipBridge(log, settings);
function _isOwnerCheck(userId, text, channelId, addressed = true) {
  const cs = currentSettings();
  const wlEmpty = !cs.whitelist || cs.whitelist.length === 0;
  if (wlEmpty && cs.auto_owner !== false) {
    // `addressed` gates the one-shot auto-owner promotion to messages actually
    // aimed at the bot (DM or @mention) — see auto_ownership.js B1 fix.
    const access = _autoOwnership.determineAccess(userId, addressed);
    if (access.promoted) saveSettings(settings);
    return access.authorized;
  }
  return authOk(userId, text, channelId);
}

const READ_ONLY_ACK = '🔒 You are read-only in this chat — you can read along, but you cannot drive the bot. Ask the owner to add you to the whitelist if that is wrong.';

// Observer-transcript fan-out (Layer 16, Phase 2). When the chat profile
// has `observer_visibility: "transcript"`, a read-only sender's text is
// forwarded as a side-channel `_observer: true` envelope. The adapter
// appends it to a per-chat ring buffer and prepends the buffer to the
// next OWNER turn as ambient context. Read-only callers stay unable to
// trigger inference on their own; they just become *visible* to the LLM.
function maybeForwardAsObserver(uid, text, chatKey, base) {
  if (!text || !String(text).trim()) return false;
  let mode = 'off';
  try { mode = inChatCmds.getObserverVisibility(SETTINGS_FILE, String(chatKey)) || 'off'; }
  catch { mode = 'off'; }
  if (mode !== 'transcript') return false;
  try {
    writeInbox({ ...base, _observer: true, text: String(text).slice(0, 2000) });
  } catch (e) {
    log(`observer-forward failed: ${e && e.message}`);
    return false;
  }
  return true;
}
const announce = makeAnnouncer({
  pluginRoot: PLUGIN_ROOT, channelLabel: 'Discord', currentSettings, logger: log,
});

const TOKEN = process.env.DISCORD_TOKEN || settings.discord_token;
if (!TOKEN) {
  log('FATAL: DISCORD_TOKEN not set (env or settings.json discord_token)');
  process.exit(1);
}

// ─── Debug-Channel-Liste (channel-spezifisch — bleibt im daemon) ────────────
function isDebugChannel(chId) {
  return (currentSettings().debug_chats || []).map(String).includes(String(chId));
}
function enableDebugChannel(chId) {
  if (!Array.isArray(settings.debug_chats)) settings.debug_chats = [];
  const s = String(chId);
  if (!settings.debug_chats.map(String).includes(s)) {
    settings.debug_chats.push(s);
    saveSettings(settings);
  }
}
function disableDebugChannel(chId) {
  const s = String(chId);
  settings.debug_chats = (settings.debug_chats || [])
    .map(String).filter(j => j !== s);
  saveSettings(settings);
}

const activeChannels = new Map(); // channel-id → ts (last user activity)
// channel-id → user message object, so we can drop the ⏳ reaction once the
// real reply ships (heartbeats don't count). Keeps the user's chat clean of
// "still working" markers as soon as the answer is in.
const pendingReactions = new Map();
// channel-id → { msg: Message, msgId: string }: the current sticky progress
// message. _progress / _heartbeat payloads edit this message in-place instead
// of flooding the chat with individual tool-call updates. Cleared when the
// real reply arrives.
//
// Also tracks the "finalized" TTL-map: once a real reply has been delivered
// for a given turn, any further _progress / _heartbeat outbox files we still
// encounter for that same msg_id are stale (sort-order race between
// `_00.json` and `_sNN.json` / `_hb.json`) and we drop them silently. This
// bookkeeping is shared with every other messenger bridge — see
// shared/js/sticky_progress.js for the platform-agnostic guard semantics.
const sticky = makeStickyProgress({ ttlMs: 60_000 });

function writeInbox(payload) {
  const id = newMsgId();
  fs.writeFileSync(path.join(INBOX, `${id}.json`),
    JSON.stringify({ id, channel: CHANNEL, ...payload }, null, 2));
  const kind = payload.audio_path ? 'voice'
             : payload.image_path ? 'image'
             : payload.document_path ? 'document'
             : payload.video_path ? 'video' : 'text';
  log(`inbox: ${id} from=${payload.from} kind=${kind}`);
  announce(payload, kind);
  return id;
}

// ─── Discord client ─────────────────────────────────────────────────────────
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.DirectMessages,
  ],
  partials: [Partials.Channel, Partials.Message],
});

// Token-only mode: MessageContent is a PRIVILEGED intent — a fresh app has
// the portal toggle off and the gateway kills the IDENTIFY (4014 "used
// disallowed intents") if we request it anyway. loginWithBackoff() preflights
// the portal state and calls enterTokenOnlyMode() when the intent would be
// rejected; DMs and @mentions carry full content without it. The marker file
// survives restarts so a login that DID die on disallowed intents (preflight
// unreachable) comes back up in token-only mode on the supervisor restart.
let messageContentActive = true;
const MC_MARKER = path.join(path.dirname(SETTINGS_FILE), '.message_content_disallowed');

function enterTokenOnlyMode(reason) {
  if (!messageContentActive) return;
  messageContentActive = false;
  // Safe pre-login: WebSocketManager reads client.options.intents.bitfield at
  // connect() time, not at Client construction (discord.js v14).
  client.options.intents = new IntentsBitField([
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.DirectMessages,
  ]).freeze();
  log(`message-content intent OFF (${reason}) — token-only mode: DMs and @mentions ` +
      `work out of the box; enable "MESSAGE CONTENT INTENT" in the Developer Portal ` +
      `to read all guild-channel text`);
}

client.once('ready', async () => {
  log(`logged in as ${client.user.tag} (id=${client.user.id})`);
  // Layer 13b: register Discord application-commands so the client-side
  // picker stops blocking our /-prefixed commands with "isn't available
  // in this environment". Idempotent — set() replaces on every boot.
  await slashCommands.registerCommands(client, log);
});

client.on('error', e => log(`client error: ${e.message}`));

// ── interactionCreate: slash-commands picked from Discord's UI ──────────────
//
// The interaction path mirrors messageCreate, just with a structured
// command + options input instead of free text. We rebuild the equivalent
// text payload via slashCommands.interactionToText() and run the same
// dispatch chain (cancel → btw → in-chat-cmds → debug → plain inbox).
//
// Discord requires an ack within 3s — we deferReply ephemerally first,
// then editReply with whatever the dispatch chain produced. Adapter-side
// replies still come through the normal outbox → channel send path, so
// the user sees the actual answer in the channel as a regular message,
// the ephemeral ack is just "got it".
client.on('interactionCreate', async (interaction) => {
  try {
    if (!interaction.isChatInputCommand || !interaction.isChatInputCommand()) return;
    const userId = interaction.user.id;
    const channelId = interaction.channelId;
    const text = slashCommands.interactionToText(interaction);
    log(`interaction cmd=${interaction.commandName} from=${userId} ch=${channelId}`);

    {
      const ro = readOnlyOk(userId, text, String(channelId));
      if (ro.isReadOnly) {
        const base = { from: String(userId), chat_id: channelId, ts: Date.now() };
        // Layer-17 read-only consent / /share dispatcher (slash-command path).
        const cc = inChatCmds.dispatchReadOnlyConsent({
          text, channel: CHANNEL, chatKey: String(channelId), uid: String(userId),
          settingsFile: SETTINGS_FILE,
        });
        if (cc) {
          if (cc.admitShare && cc.sharePayload) {
            try {
              writeInbox({ ...base, _observer: true, _share: true,
                           text: String(cc.sharePayload).slice(0, 2000) });
            } catch (e) { log(`/share inbox-write failed: ${e.message}`); }
          }
          if (cc.reply) {
            try { await interaction.reply({ content: cc.reply, ephemeral: true }); } catch {}
          } else {
            try { await interaction.reply({ content: '✓', ephemeral: true }); } catch {}
          }
          log(`read-only consent (interaction): ${cc.kind} from=${userId} ch=${channelId}`);
          return;
        }
        const forwarded = maybeForwardAsObserver(userId, text, channelId, base);
        if (forwarded) {
          // Stay quiet on the slash-command path — the interaction needs an
          // ack within 3s; an ephemeral note keeps the chat clean.
          try { await interaction.reply({ content: '👁️ noted (read-only context)', ephemeral: true }); } catch {}
        } else if (ro.firstDrop) {
          try { await interaction.reply({ content: READ_ONLY_ACK, ephemeral: true }); } catch {}
        } else {
          try { await interaction.reply({ content: '🔒', ephemeral: true }); } catch {}
        }
        return;
      }
    }
    // ADR-0166 privilege model: whitelist ⇒ owner, SPG-admitted ⇒ guest.
    // `_isOwner` MUST flow to every command dispatch below — an SPG guest is
    // admitted to the chat but must NOT inherit the owner command surface
    // (/vault, /invite, /grant, …). Hardcoding isOwner:true here was a
    // privilege-escalation (security review 2026-06-27).
    const _isOwner = _isOwnerCheck(userId, text, channelId);
    if (!_isOwner) {
      // ADR-0166: check SPG invitation before rejecting non-whitelisted sender
      let _spgAllowed = false;
      try {
        const _spgScript = require('path').join(__dirname, '..', 'shared', 'spg.py');
        const _spgRes = require('child_process').spawnSync(
          process.env.CORVIN_PYTHON || 'python3',
          [_spgScript, 'is-allowed', CHANNEL, String(channelId), String(userId)],
          { encoding: 'utf8', timeout: 2000 }
        );
        if (_spgRes.status === 0 && _spgRes.stdout) {
          const _spgData = JSON.parse(_spgRes.stdout);
          _spgAllowed = _spgData.allowed === true;
        }
      } catch (_e) {
        // SPG check failed — fail-closed, reject as usual
      }
      if (!_spgAllowed) {
        try {
          await interaction.reply({
            content: `You are not authorized. Your user-id: \`${userId}\`\nAdd it to the whitelist in settings.json.`,
            ephemeral: true,
          });
        } catch {}
        return;
      }
    }
    // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure for whitelisted
    // users in the slash-command (interaction) path. Mirrors the messageCreate
    // disclosure logic — shown once per (chat, uid).
    if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: String(channelId), uid: String(userId) })) {
      const card = inChatCmds.disclosureCardText({
        channel: CHANNEL, ownerLabel: OPERATOR_NAME || '(owner)',
        hasObserverTranscript: false,
        lang: currentSettings().lang || 'en',
      });
      if (card) {
        let disclosureDelivered = false;
        try { await interaction.reply({ content: card, ephemeral: true }); disclosureDelivered = true; } catch (discErr) {
          log(`[WARN][disclosure] interaction card delivery FAILED uid=${userId} ch=${channelId} — will retry. err=${discErr && discErr.message}`);
        }
        if (disclosureDelivered) {
          const seen = inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: String(channelId), uid: String(userId), action: 'pending' });
          if (!seen.ok) log(`[disclosure] interaction mark_seen failed — ${seen.error}`);
          log(`disclosure shown (interaction) uid=${userId} ch=${channelId}`);
          return; // delivery consumed the interaction reply slot; next message continues normally
        }
      }
    }
    if (!rateAllow(userId, currentSettings().rate_limit_per_hour || 30)) {
      try { await interaction.reply({ content: 'Rate limit reached.', ephemeral: true }); } catch {}
      return;
    }

    // Defer ephemerally — the real reply for adapter-routed commands
    // (/btw, /stop, plain inbox) lands in the channel via outbox, so the
    // ephemeral ack is just a "received" confirmation visible only to
    // the invoker.
    try { await interaction.deferReply({ ephemeral: true }); } catch {}

    const cmdLower = text.trim().toLowerCase();
    const base = { from: String(userId), chat_id: channelId, ts: Date.now() };

    // /on /off /status — owner-side chat-toggle (mirror of messageCreate
    // gate at daemon.js:406). Without this branch the slash-command path
    // falls through to the LLM subprocess and Claude-CLI's internal
    // slash-handler replies "isn't available in this environment".
    {
      const tog = chatToggle.handleToggleCommand({
        text, chatKey: String(channelId), isOwner: _isOwner,
        settingsFile: SETTINGS_FILE,
      });
      if (tog) {
        try { await interaction.editReply(tog.reply); } catch {}
        log(`interaction toggle ${tog.kind} ch=${channelId}`);
        return;
      }
    }

    // /stop /cancel — adapter SIGTERMs the running subprocess.
    if (cmdLower === '/stop' || cmdLower === '/cancel' || cmdLower === '/abbruch' || cmdLower === '/halt') {
      writeInbox({ ...base, _cancel: true });
      try { await interaction.editReply('🛑 Cancel requested.'); } catch {}
      return;
    }
    // /btw — Layer 13 mid-stream injection. Same regex as messageCreate
    // for parity (case-insensitive, optional body).
    {
      const btwMatch = (text || '').match(/^\/btw(?:\s+([\s\S]+))?$/i);
      if (btwMatch) {
        const btwText = (btwMatch[1] || '').trim();
        writeInbox({ ...base, _btw: true, text: btwText });
        try {
          await interaction.editReply(btwText
            ? '📝 Note queued for the running task.'
            : '⚠️ Empty /btw — give it some text after the command.');
        } catch {}
        return;
      }
    }
    // Shared in-chat-commands dispatcher (handles /persona /help /reset
    // /voice-user-* /dialectic-* /ldd-* /profile /memory /vault /schedule …)
    {
      const cwk = inChatCmds.dispatch({
        text, channel: CHANNEL, chatKey: String(channelId),
        isOwner: _isOwner,  // whitelist ⇒ owner; SPG guest ⇒ false (no owner surface)
        uid: String(userId),  // so owner-attributed audit (e.g. SPG /open|/close) records the real uid
        settingsFile: SETTINGS_FILE,
      });
      if (cwk) {
        // Most slash-command replies (whoami, settings, help, etc.) stay
        // ephemeral — they're personal status info that would only clutter
        // the channel. A small allow-list publishes the reply as a regular
        // channel message instead, so workflow runs / structured outputs
        // stick in the chat history for follow-up reference.
        const PUBLIC_KINDS = new Set(['workflow']);
        const TEXT_LIMIT = 1900;
        const reply = String(cwk.reply || '(no output)');
        const isPublic = PUBLIC_KINDS.has(cwk.kind);
        try {
          if (isPublic && interaction.channel && interaction.channel.send) {
            // Public path: short ephemeral ack + full reply via channel.send
            // so every chat participant sees the output and the message
            // persists in the scrollback.
            try { await interaction.editReply('▶ running…'); } catch {}
            const ch = interaction.channel;
            if (reply.length <= TEXT_LIMIT) {
              await ch.send(reply);
            } else {
              for (let i = 0; i < reply.length; i += TEXT_LIMIT) {
                await ch.send(reply.slice(i, i + TEXT_LIMIT));
              }
            }
          } else if (reply.length <= TEXT_LIMIT) {
            await interaction.editReply(reply);
          } else {
            await interaction.editReply(reply.slice(0, TEXT_LIMIT));
            for (let i = TEXT_LIMIT; i < reply.length; i += TEXT_LIMIT) {
              await interaction.followUp({ content: reply.slice(i, i + TEXT_LIMIT), ephemeral: true });
            }
          }
        } catch {}
        log(`interaction in-chat-cmd ${cwk.kind} → ${channelId}${isPublic ? ' (public)' : ''}`);
        // Voice note for welcome/marketing commands (tts: true).
        // cwk.lang is set by in_chat_commands.dispatch() (/willkommen → 'de',
        // /welcome|/start|/hi → 'en').  Fall back to 'de' for safety.
        if (cwk.tts && interaction.channel) {
          synthesizeVoiceNoteForText(cwk.reply, cwk.lang || 'de', 'shimmer').then((oggPath) => {
            if (!oggPath) return;
            interaction.channel.send({ files: [new AttachmentBuilder(oggPath, { name: 'voice.ogg' })] })
              .catch(() => {})
              .finally(() => { try { fs.unlinkSync(oggPath); } catch {} });
            log(`in-chat-cmd ${cwk.kind} voice → ${channelId}`);
          }).catch(() => {});
        }
        return;
      }
    }
    // /debug toggle — daemon-local, mirrors messageCreate logic.
    if (cmdLower === '/debug' || cmdLower === '/debug on' || cmdLower === '/debug off') {
      let nowOn;
      if (cmdLower === '/debug on')       { enableDebugChannel(channelId);  nowOn = true; }
      else if (cmdLower === '/debug off') { disableDebugChannel(channelId); nowOn = false; }
      else {
        nowOn = !isDebugChannel(channelId);
        if (nowOn) enableDebugChannel(channelId); else disableDebugChannel(channelId);
      }
      try {
        await interaction.editReply(nowOn
          ? 'Debug mode on. You see every tool call.'
          : 'Debug mode off. You only see the rough plan.');
      } catch {}
      return;
    }
    // Fallback: write as plain text inbox so Claude (with its skill system)
    // handles it. /voice-on, /voice-off etc. are plugin skills — they go
    // through this path.
    activeChannels.set(channelId, Date.now());
    writeInbox({ ...base, text });
    try { await interaction.editReply('⏳ on it…'); } catch {}
  } catch (e) {
    log(`interactionCreate error: ${e.message}`);
    try {
      if (interaction.deferred || interaction.replied) {
        await interaction.followUp({ content: `Error: ${e.message}`, ephemeral: true });
      } else {
        await interaction.reply({ content: `Error: ${e.message}`, ephemeral: true });
      }
    } catch {}
  }
});

client.on('messageCreate', async (msg) => {
  try {
    if (msg.author.bot) return;
    const userId = msg.author.id;
    // Strip a leading @mention of the bot: in token-only mode (no privileged
    // MessageContent intent) guild text only arrives when the bot is
    // mentioned, and "<@botid> hello" should reach the adapter as "hello".
    // Harmless when the intent is active — a leading mention is address form,
    // not content.
    let text = msg.content || '';
    const meId = client.user?.id;
    if (meId) text = text.replace(new RegExp(`^\\s*<@!?${meId}>\\s*`), '');
    const id = newMsgId();

    {
      const ro = readOnlyOk(userId, text, String(msg.channel.id));
      if (ro.isReadOnly) {
        const base = { from: String(userId), chat_id: msg.channel.id, ts: Date.now() };
        // Layer-17 read-only consent / /share dispatcher (text-message path).
        const cc = inChatCmds.dispatchReadOnlyConsent({
          text, channel: CHANNEL, chatKey: String(msg.channel.id), uid: String(userId),
          settingsFile: SETTINGS_FILE,
        });
        if (cc) {
          if (cc.admitShare && cc.sharePayload) {
            try {
              writeInbox({ ...base, _observer: true, _share: true,
                           text: String(cc.sharePayload).slice(0, 2000) });
            } catch (e) { log(`/share inbox-write failed: ${e.message}`); }
          }
          if (cc.reply) {
            try { await msg.reply(cc.reply); } catch {}
          }
          log(`read-only consent (msg): ${cc.kind} from=${userId} ch=${msg.channel.id}`);
          return;
        }
        // Layer-19 — /join /pass for read-only senders.
        const dd = inChatCmds.dispatchReadOnlyDisclosure({
          text, channel: CHANNEL, chatKey: String(msg.channel.id), uid: String(userId),
          settingsFile: SETTINGS_FILE,
        });
        if (dd) {
          if (dd.reply) { try { await msg.reply(dd.reply); } catch {} }
          log(`read-only disclosure: ${dd.kind} from=${userId} ch=${msg.channel.id}`);
          return;
        }
        // Layer-21 — /propose <text> for read-only senders.
        const pp = inChatCmds.dispatchReadOnlyProposal({
          text, channel: CHANNEL, chatKey: String(msg.channel.id), uid: String(userId),
          settingsFile: SETTINGS_FILE,
        });
        if (pp) {
          if (pp.reply) { try { await msg.reply(pp.reply); } catch {} }
          log(`read-only proposal: ${pp.kind} from=${userId} ch=${msg.channel.id}`);
          return;
        }
        // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure for
        // read-only OBSERVERS too. Their message is forwarded to the LLM
        // (observer transcript), so they are interacting with the AI and
        // must be told it is one — not only reactively via /join. Shown
        // once per (chat, uid), same ledger as the owner path below.
        if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: String(msg.channel.id), uid: String(userId) })) {
          const ocard = inChatCmds.disclosureCardText({
            channel: CHANNEL, ownerLabel: OPERATOR_NAME || '(owner)',
            hasObserverTranscript: true,
            lang: currentSettings().lang || 'en',
          });
          if (ocard) {
            // EU AI Act Art. 50: only mark disclosed AFTER confirmed delivery.
            // A failed send must NOT mark the user as seen — next message retries.
            let disclosureDelivered = false;
            try { await msg.reply(ocard); disclosureDelivered = true; } catch (discErr) {
              log(`[WARN][disclosure] observer card delivery FAILED uid=${userId} ch=${msg.channel.id} — will retry next message. Art.50 compliance gap. err=${discErr && discErr.message}`);
            }
            if (disclosureDelivered) {
              const oseen = inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: String(msg.channel.id), uid: String(userId), action: 'pending' });
              if (!oseen.ok) log(`[disclosure] observer mark_seen failed — ${oseen.error}`);
              log(`disclosure shown (observer) uid=${userId} ch=${msg.channel.id}`);
            }
          }
        }
        const forwarded = maybeForwardAsObserver(userId, text, msg.channel.id, base);
        if (forwarded) {
          // Silent: the LLM will see the line on the next owner turn,
          // there is nothing for the bot to reply to right now.
        } else if (ro.firstDrop) {
          try { await msg.reply(READ_ONLY_ACK); } catch {}
        }
        return;
      }
    }
    // ADR-0166 privilege model: whitelist ⇒ owner, SPG-admitted ⇒ guest.
    // `_isOwner` MUST flow to every command dispatch below (see interaction
    // path). An SPG guest is admitted but must NOT inherit the owner surface.
    // Addressed = a DM to the bot, or a guild message that @mentions it. With
    // the MessageContent intent the bot also sees un-addressed guild chatter;
    // only an addressed message may claim auto-ownership (B1).
    const _addressed = !msg.guild || (!!meId && !!msg.mentions?.users?.has(meId));
    const _isOwner = _isOwnerCheck(userId, text, msg.channel.id, _addressed);
    if (!_isOwner) {
      // ADR-0166: check SPG invitation before rejecting non-whitelisted sender
      let _spgAllowed = false;
      try {
        const _spgScript = require('path').join(__dirname, '..', 'shared', 'spg.py');
        const _spgRes = require('child_process').spawnSync(
          process.env.CORVIN_PYTHON || 'python3',
          [_spgScript, 'is-allowed', CHANNEL, String(msg.channel.id), String(userId)],
          { encoding: 'utf8', timeout: 2000 }
        );
        if (_spgRes.status === 0 && _spgRes.stdout) {
          const _spgData = JSON.parse(_spgRes.stdout);
          _spgAllowed = _spgData.allowed === true;
        }
      } catch (_e) {
        // SPG check failed — fail-closed, reject as usual
      }
      if (!_spgAllowed) {
        try {
          await msg.reply(`You are not authorized. Your user-id: \`${userId}\`\nAdd it to the whitelist in settings.json (or send "/auth <pin>").\nThe owner can also open this chat with /all on.`);
        } catch {}
        return;
      }
    }
    // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure on first encounter
    // for whitelisted (owner) senders. Read-only senders get this via
    // dispatchReadOnlyDisclosure() above. Shown once per (chat, uid).
    if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: String(msg.channel.id), uid: String(userId) })) {
      const card = inChatCmds.disclosureCardText({
        channel: CHANNEL, ownerLabel: OPERATOR_NAME || '(owner)',
        hasObserverTranscript: false,
        lang: currentSettings().lang || 'en',
      });
      if (card) {
        // EU AI Act Art. 50: only mark disclosed AFTER confirmed delivery.
        // A failed send must NOT mark the user as seen — next message retries.
        let disclosureDelivered = false;
        try { await msg.reply(card); disclosureDelivered = true; } catch (discErr) {
          log(`[WARN][disclosure] card delivery FAILED uid=${userId} ch=${msg.channel.id} — will retry next message. Art.50 compliance gap. err=${discErr && discErr.message}`);
        }
        if (disclosureDelivered) {
          const seen = inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: String(msg.channel.id), uid: String(userId), action: 'pending' });
          if (!seen.ok) log(`[disclosure] mark_seen failed — ${seen.error}`);
          log(`disclosure shown uid=${userId} ch=${msg.channel.id}`);
        }
      }
    }
    if (!rateAllow(userId, currentSettings().rate_limit_per_hour || 30)) {
      try { await msg.reply('Rate limit reached. Please try again later.'); } catch {}
      return;
    }

    if (text === '/start') {
      await msg.reply(`Hi! I'm the Claude bridge on Discord.\n\nYour user-id: \`${userId}\`\nAdd it to \`settings.json\` → \`whitelist\`.`);
      return;
    }

    // Owner-side /on /off /status — opt-in toggle. Backwards-compat: a
    // settings.json without `enabled_chats` keeps the legacy default-on
    // behaviour, so existing deployments are unaffected.
    {
      const tog = chatToggle.handleToggleCommand({
        text, chatKey: String(msg.channel.id), isOwner: _isOwner,
        settingsFile: SETTINGS_FILE,
      });
      if (tog) {
        try { await msg.reply(tog.reply); } catch {}
        log(`toggle ${tog.kind} ch=${msg.channel.id}`);
        return;
      }
    }
    if (!chatToggle.isChatEnabled(currentSettings(), String(msg.channel.id))) {
      log(`channel ${msg.channel.id} not enabled, ignoring`);
      return;
    }

    const cmdLower = (text || '').trim().toLowerCase();
    // Note: /new /clear /reset are now owned by the in-chat dispatcher
    // (shared/js/in_chat_commands.js) so the layer-8 session-reset
    // (skills + forge + voice) all happens in one place.
    if (cmdLower === '/stop' || cmdLower === '/cancel' || cmdLower === '/abbruch' || cmdLower === '/halt') {
      log(`cancel cmd from ${userId} in channel ${msg.channel.id}`);
      writeInbox({ from: String(userId), chat_id: msg.channel.id, _cancel: true, ts: Date.now() });
      return;  // adapter SIGTERMs the running subproc and writes ACK
    }
    // /btw <text> — Layer 13 mid-stream injection.
    {
      const btwMatch = (text || '').match(/^\/btw(?:\s+([\s\S]+))?$/i);
      if (btwMatch) {
        const btwText = (btwMatch[1] || '').trim();
        log(`btw cmd from ${userId} in channel ${msg.channel.id} (len=${btwText.length})`);
        writeInbox({ from: String(userId), chat_id: msg.channel.id, _btw: true, text: btwText, ts: Date.now() });
        return;
      }
    }
    {
      const cwk = inChatCmds.dispatch({
        text, channel: CHANNEL, chatKey: String(msg.channel.id),
        isOwner: _isOwner,  // whitelist ⇒ owner; SPG guest ⇒ false (no owner surface)
        settingsFile: SETTINGS_FILE,
      });
      if (cwk) {
        try { await msg.reply(cwk.reply); } catch {}
        log(`in-chat-cmd ${cwk.kind} → ${msg.channel.id}`);
        // Voice note for welcome/marketing commands (tts: true).
        // cwk.lang is set by in_chat_commands.dispatch() (/willkommen → 'de',
        // /welcome|/start|/hi → 'en').  Fall back to 'de' for safety.
        if (cwk.tts) {
          synthesizeVoiceNoteForText(cwk.reply, cwk.lang || 'de', 'shimmer').then((oggPath) => {
            if (!oggPath) return;
            msg.channel.send({ files: [new AttachmentBuilder(oggPath, { name: 'voice.ogg' })] })
              .catch(() => {})
              .finally(() => { try { fs.unlinkSync(oggPath); } catch {} });
            log(`in-chat-cmd ${cwk.kind} voice → ${msg.channel.id}`);
          }).catch(() => {});
        }
        return;
      }
    }
    if (cmdLower === '/debug' || cmdLower === '/debug on' || cmdLower === '/debug off') {
      const chId = msg.channel.id;
      let nowOn;
      if (cmdLower === '/debug on') {
        enableDebugChannel(chId); nowOn = true;
      } else if (cmdLower === '/debug off') {
        disableDebugChannel(chId); nowOn = false;
      } else {
        nowOn = !isDebugChannel(chId);
        if (nowOn) enableDebugChannel(chId); else disableDebugChannel(chId);
      }
      log(`debug ${nowOn ? 'on' : 'off'} for channel ${chId}`);
      try {
        await msg.reply(nowOn
          ? 'Debug mode on. You see every tool call.'
          : 'Debug mode off. You only see the rough plan.');
      } catch {}
      return;
    }

    activeChannels.set(msg.channel.id, Date.now());
    try { await msg.channel.sendTyping(); } catch {}
    // Hourglass reaction on the user's message → instant visual ack. Removed
    // again when the real reply lands (see sendDiscord).
    try {
      await msg.react('⏳');
      pendingReactions.set(msg.channel.id, msg);
    } catch {}

    const base = { from: String(userId), chat_id: msg.channel.id, ts: Date.now() };

    // Handle attachments. Discord delivers them via msg.attachments (Map).
    if (msg.attachments.size > 0) {
      const att = msg.attachments.first();
      const fileResp = await fetch(att.url);
      const buf = Buffer.from(await fileResp.arrayBuffer());
      const safeName = (att.name || 'file').replace(/[^a-zA-Z0-9._-]/g, '_');
      const ct = (att.contentType || '').toLowerCase();
      const ext = (att.name || '').slice(att.name.lastIndexOf('.')).toLowerCase();
      if (ct.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp)$/.test(ext)) {
        const p = path.join(INBOX, `${id}_${safeName}`);
        fs.writeFileSync(p, buf);
        writeInbox({ ...base, image_path: p, caption: text });
        return;
      }
      if (ct.startsWith('audio/') || /\.(ogg|mp3|m4a|wav|opus)$/.test(ext)) {
        const p = path.join(INBOX, `${id}_${safeName}`);
        fs.writeFileSync(p, buf);
        writeInbox({ ...base, audio_path: p, caption: text });
        return;
      }
      if (ct.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/.test(ext)) {
        const p = path.join(INBOX, `${id}_${safeName}`);
        fs.writeFileSync(p, buf);
        writeInbox({ ...base, video_path: p, caption: text });
        return;
      }
      // Default: treat as document.
      const p = path.join(INBOX, `${id}_${safeName}`);
      fs.writeFileSync(p, buf);
      writeInbox({ ...base, document_path: p, document_name: att.name, mimetype: att.contentType, caption: text });
      return;
    }

    if (text) {
      writeInbox({ ...base, text });
    } else if (!messageContentActive) {
      // Expected in token-only mode: guild messages that neither mention the
      // bot nor are DMs arrive with stripped content. Log at trace level of
      // detail but with the actionable hint.
      log(`messageCreate dropped (token-only mode): guild message without @mention — ` +
          `mention the bot or DM it, or enable MESSAGE CONTENT INTENT in the Developer ` +
          `Portal (msg=${msg.id} author=${userId} channel=${msg.channel.id})`);
    } else {
      // Empty text + no attachments. The most likely cause is the
      // "MESSAGE CONTENT INTENT" being disabled in the Developer Portal —
      // Discord still delivers messageCreate events but strips msg.content,
      // so every guild message dies silently in the `if (text)` gate above.
      // Log loud so the next investigation isn't another 90-min hunt.
      log(`messageCreate dropped: empty content + no attachments — check MESSAGE_CONTENT_INTENT in Developer Portal (msg=${msg.id} author=${userId} channel=${msg.channel.id})`);
    }
  } catch (e) {
    log(`messageCreate error: ${e.message}`);
  }
});

// Refresh typing every 8s for active channels (Discord typing expires ~10s).
setInterval(async () => {
  const now = Date.now();
  for (const [chId, ts] of activeChannels.entries()) {
    if (now - ts > 60000) { activeChannels.delete(chId); continue; }
    try {
      const ch = await client.channels.fetch(chId);
      ch?.sendTyping();
    } catch {}
  }
}, 8000);

// ─── Outbox processing ──────────────────────────────────────────────────────
/**
 * Render execution context as a Discord embed.
 * @param {object} context - Normalized execution context
 * @returns {object} - Discord embed object
 */
function renderExecutionContextEmbed(context) {
  if (!context) return null;

  const ctx = normalizeExecutionContext(context);
  const embed = {
    title: '⚙️ Execution Context',
    color: getColorForMode(ctx.delegation_mode),
    fields: [
      {
        name: '🔧 Engine',
        value: formatEngineId(ctx.engine_id),
        inline: true,
      },
      {
        name: '📊 Model',
        value: ctx.model_name,
        inline: true,
      },
      {
        name: '⚡ Delegation',
        value: formatDelegationMode(ctx.delegation_mode),
        inline: true,
      },
      {
        name: '⏱️ Duration',
        value: formatDuration(ctx.duration_ms),
        inline: true,
      },
    ],
  };

  // Add token information if available
  if (ctx.tokens_input !== null || ctx.tokens_output !== null) {
    embed.fields.push({
      name: '🪙 Tokens',
      value: `in: ${formatTokens(ctx.tokens_input)} | out: ${formatTokens(ctx.tokens_output)}`,
      inline: true,
    });
  }

  // Add tool count if > 0
  if (ctx.tool_calls_count > 0) {
    embed.fields.push({
      name: '🔨 Tools',
      value: String(ctx.tool_calls_count),
      inline: true,
    });
  }

  // Add timestamp if available
  if (ctx.completed_at) {
    try {
      embed.timestamp = new Date(ctx.completed_at).toISOString();
    } catch {}
  }

  return embed;
}

async function sendDiscord(payload, _fpath) {
  const chId = payload.chat_id;
  if (!chId) { log(`no chat_id, skipping`); return; }

  // Reject a structurally impossible chat_id before it costs a REST call.
  // Discord channel ids are snowflakes: 17-20 digits, nothing else. The 724
  // dead-lettered envelopes found on 2026-07-26 were all `chat_id:
  // "owner-chat"` — a test placeholder that leaked into the live outbox — and
  // each one burned a round-trip just to come back as 50035. Failing locally
  // keeps that traffic off Discord's invalid-request budget, which is what
  // gets a bot rate-limited at the edge. Same error code as the API returns,
  // so PERMANENT_DISCORD_CODES retires it on attempt #1 exactly as before.
  if (!/^\d{17,20}$/.test(String(chId))) {
    const err = new Error(`chat_id ${JSON.stringify(chId)} is not a snowflake`);
    err.code = 50035;
    throw err;
  }

  // Stale-finalize gate. The outbox dir is processed in alphabetical order,
  // and `{msg_id}_00.json` (real reply) sorts BEFORE `{msg_id}_hb.json`
  // (heartbeat) and `{msg_id}_sNN.json` (progress). So when several files
  // for the same turn land between two polling ticks the daemon would
  // dispatch the real reply, then re-send a progress sticky on top of it
  // ("agent writes itself messages"). Once we've sent the final reply for
  // a given msg_id, drop every other file for that msg_id silently.
  const msgId = payload.msg_id;
  if ((payload._progress || payload._heartbeat) && sticky.isFinalized(msgId)) {
    log(`drop stale ${payload._progress ? 'progress' : 'heartbeat'} for finalized ${msgId}`);
    return;
  }

  const ch = await client.channels.fetch(chId);
  // A null channel here is NOT a delivered message. Returning normally told the
  // outbox poller "sent successfully" and it unlinked the file — a reply the
  // agent had already produced vanished silently (incident 2026-07-25: the
  // post-reboot poller ran 300 ms before the READY event, hit an empty channel
  // cache, and dropped a finished turn). Throw so the file stays queued; a
  // genuinely dead channel is retired by the dead-letter path instead.
  if (!ch) throw new Error(`channel ${chId} not found (cache miss or deleted)`);

  // Progress updates: edit a single sticky message instead of flooding the
  // chat with one message per tool call. On the first _progress payload we
  // send a new message and remember it; every subsequent one edits it.
  // When the real reply arrives the sticky message is deleted first.
  if (payload._progress && payload.text) {
    const existing = sticky.getProgress(chId);
    if (existing && existing.msg) {
      try { await existing.msg.edit(payload.text); return; } catch {
        // Edit failed (message deleted externally or Discord error). Delete the
        // old sticky explicitly so it does not remain visible alongside the new
        // one — without this the original heartbeat would linger as a ghost
        // message and the user sees two messages.
        try { await existing.msg.delete(); } catch {}
        sticky.clearProgress(chId);
      }
    }
    try {
      const sent = await ch.send(payload.text);
      sticky.setProgress(chId, { msg: sent, msgId: msgId || null });
    } catch {}
    return;
  }

  // Heartbeat: slot into the sticky system so it gets deleted when the real
  // reply arrives. If a progress update already claimed the sticky slot,
  // skip silently — the progress message is more informative anyway.
  if (payload._heartbeat && payload.text) {
    if (!sticky.hasProgress(chId)) {
      try {
        const sent = await ch.send(payload.text);
        sticky.setProgress(chId, { msg: sent, msgId: msgId || null });
      } catch {}
    }
    return;
  }

  // Real reply incoming — delete the sticky progress message first so the
  // chat shows the answer cleanly without a stale status line above it.
  if (sticky.hasProgress(chId)) {
    const prog = sticky.getProgress(chId);
    if (prog && prog.msg) {
      try {
        await prog.msg.delete();
      } catch (e) {
        // First delete attempt failed — retry once after a short pause.
        // A lingering sticky alongside the real reply is the "two messages"
        // symptom; a single retry covers most transient Discord API errors.
        log(`sticky delete failed (will retry): ${e && e.message || e}`);
        await new Promise(r => setTimeout(r, 400));
        try { await prog.msg.delete(); } catch (e2) {
          log(`sticky delete retry also failed: ${e2 && e2.message || e2}`);
        }
      }
    }
    sticky.clearProgress(chId);
  }

  // Mark this turn finalized BEFORE we touch Discord so any racing
  // progress/heartbeat that arrives mid-send is correctly classified.
  sticky.markFinalized(msgId);

  // Send text and voice as a single Discord message when both are present.
  // The adapter already chunks below Discord's 2000-char limit for us, so
  // in 99% of cases this loop runs exactly once. We keep a 1900-char hard
  // split as belt-and-braces against future regressions.
  const TEXT_LIMIT = 1900;
  const hasVoice = payload.voice_path && fs.existsSync(payload.voice_path);
  if (payload.text) {
    const textChunks = [];
    for (let i = 0; i < payload.text.length; i += TEXT_LIMIT) {
      textChunks.push(payload.text.slice(i, i + TEXT_LIMIT));
    }
    for (let i = 0; i < textChunks.length; i++) {
      const isLast = i === textChunks.length - 1;
      if (isLast && hasVoice) {
        await ch.send({ content: textChunks[i], files: [new AttachmentBuilder(payload.voice_path, { name: 'voice.ogg' })] });
      } else {
        await ch.send(textChunks[i]);
      }
    }
  } else if (hasVoice) {
    await ch.send({ files: [new AttachmentBuilder(payload.voice_path, { name: 'voice.ogg' })] });
  }
  if (payload.image_path && fs.existsSync(payload.image_path)) {
    const att = new AttachmentBuilder(payload.image_path, { name: path.basename(payload.image_path) });
    await ch.send({ content: payload.image_caption || undefined, files: [att] });
  }
  if (payload.document_path && fs.existsSync(payload.document_path)) {
    const att = new AttachmentBuilder(payload.document_path, { name: payload.document_name || path.basename(payload.document_path) });
    await ch.send({ content: payload.document_caption || undefined, files: [att] });
  }
  if (payload.video_path && fs.existsSync(payload.video_path)) {
    const att = new AttachmentBuilder(payload.video_path, { name: 'video.mp4' });
    await ch.send({ content: payload.video_caption || undefined, files: [att] });
  }

  // Phase 4 K=2: Render execution context embed if available and enabled
  if (shouldRenderContext(payload, currentSettings())) {
    try {
      const embed = renderExecutionContextEmbed(payload.execution_context);
      if (embed) {
        await ch.send({ embeds: [embed] });
        log(`execution context embed sent for ${msgId}`);
      }
    } catch (e) {
      log(`execution context embed failed: ${e && e.message || e}`);
    }
  }

  activeChannels.delete(chId);

  // Real reply (not a heartbeat) is on its way — drop our ⏳ reaction
  // from the user's message so the chat doesn't stay marked as pending.
  if (!payload._heartbeat && pendingReactions.has(chId)) {
    const userMsg = pendingReactions.get(chId);
    try {
      const reaction = userMsg.reactions.cache.find(r => r.emoji.name === '⏳');
      if (reaction) await reaction.users.remove(client.user.id);
    } catch {}
    pendingReactions.delete(chId);
  }
}

// Errors that are permanent by CONSTRUCTION — the envelope itself is malformed,
// so no amount of waiting can make it deliverable. These skip the retry budget
// entirely. The 328-envelope backlog of 2026-07-25 was 289× a non-snowflake
// chat_id ("owner-chat"), i.e. exactly this class: retried every tick forever,
// stretching a full poll pass to ~65 s and queueing real replies behind it.
//
// Deliberately NOT listed: 10003 Unknown Channel / 10004 Unknown Guild /
// 50001 Missing Access / 50013 Missing Permissions and HTTP 403/404. Those say
// "not reachable right now", which a Discord outage or a briefly-removed bot
// also produces — retiring a finished reply on the first such error would
// recreate the very data loss this change fixes. They still leave the outbox,
// just via the maxAttempts budget instead of on attempt #1.
const PERMANENT_DISCORD_CODES = new Set([
  50035, // Invalid Form Body — e.g. chat_id is not a snowflake
  50006, // Cannot send an empty message
  40005, // Request entity too large
]);

const outboxPoller = startOutboxPoller({
  outboxDir: OUTBOX, channel: CHANNEL, sendFn: sendDiscord, logger: log,
  // Wait for READY, not just for the token. The token is set on the REST
  // manager ~300 ms before the gateway hands us the channel cache, and sends
  // issued in that window resolve to a null channel (incident 2026-07-25).
  // isReady() also implies token != null, so this still covers the original
  // "Expected token to be set" log-flood case (incident 2026-07-10).
  preCheck: () => client.isReady(),
  deadLetterDir: DEAD_LETTER,
  isPermanent: (e) => PERMANENT_DISCORD_CODES.has(e?.code),
  // 20 ticks ≈ 10 s of retrying before an unreachable channel is retired.
  // Long enough to ride out a gateway hiccup, short enough that a genuinely
  // dead channel stops blocking the queue within one poll cycle.
  maxAttempts: 20,
});

process.on('unhandledRejection', r => log(`unhandledRejection: ${r && r.message || r}`));

// HTTP /status
startHealthServer({
  port: HTTP_PORT, kind: 'discord', logger: log,
  getStatus: () => ({
    paired: !!client.user,
    bot_tag: client.user?.tag || null,
    whitelist_size: (currentSettings().whitelist || []).length,
    pending_outbox: countPending(OUTBOX, CHANNEL),
    // Delivery liveness. `paired` and an open gateway socket say nothing about
    // whether the outbox is draining — on 2026-07-26 both looked healthy for
    // 38 minutes while a wedged poller delivered nothing. poller_stalled_s > 0
    // is the signal a watchdog needs to restart this daemon.
    poller_stalled_s: outboxPoller.stats().stalled_s,
    // `client.isReady()` (this daemon's preCheck) can also refuse forever
    // without ever tripping poller_stalled_s — that gap is what let 5
    // replies sit undelivered for 90 minutes on 2026-07-27 while this very
    // field would have shown it. > 0 means preCheck is the blocker.
    precheck_stalled_s: outboxPoller.stats().precheck_stalled_s,
  }),
});

// ── Resilience: shard-event logging, login-with-backoff, zombie-watchdog ────
// Background: a stale Cloudflare 503 ("reset reason: overflow") on the bot
// token chewed through 14 daemon restarts in 3 days, exhausting the daily
// IDENTIFY budget and locking the token at the edge. The legacy login path
// did `process.exit(1)` on any failure, turning every transient error into
// a systemd restart-storm that compounded the rate-limit. The watchdog
// catches the *other* shape of failure that triggered today's incident:
// a "silent half-connect" where the bot stays marked online but no events
// flow.
client.on('shardError',        err          => log(`shardError: ${err?.message || err}`));
client.on('shardDisconnect',   (ev, sid)    => log(`shardDisconnect shard=${sid} code=${ev?.code} reason=${ev?.reason || ''}`));
client.on('shardResume',       (sid, repl)  => { log(`shardResume shard=${sid} replayed=${repl}`); reconnectStrikes = 0; });

// Stuck-reconnect detector: if the shard issues `shardReconnecting` more
// than RECONNECT_STRIKES_FATAL times within RECONNECT_WINDOW_MS without an
// intervening `shardResume`, the gateway is wedged and discord.js's own
// resume loop won't recover. Exit so systemd performs a clean restart.
const RECONNECT_WINDOW_MS    = 60_000;
const RECONNECT_STRIKES_FATAL = 3;
let reconnectStrikes = 0;
let lastReconnectAt  = 0;
client.on('shardReconnecting', async sid => {
  log(`shardReconnecting shard=${sid}`);
  const now = Date.now();
  reconnectStrikes = (now - lastReconnectAt < RECONNECT_WINDOW_MS) ? reconnectStrikes + 1 : 1;
  lastReconnectAt  = now;
  if (reconnectStrikes >= RECONNECT_STRIKES_FATAL) {
    // A local network outage produces the exact same reconnect burst as a
    // wedged gateway, but exiting is counterproductive there: the restart
    // lands in loginWithBackoff against a dead uplink and the daemon goes
    // blind for the whole backoff ladder (incident 2026-07-10). If the
    // uplink is down, let discord.js keep its own resume loop running —
    // it recovers the session without a fresh IDENTIFY once DNS is back.
    if (!(await networkUp())) {
      log(`shardReconnecting: ${reconnectStrikes} strikes but local network is down — not a wedged gateway, staying up`);
      reconnectStrikes = 0;
      return;
    }
    // Re-check after the await: a concurrent handler invocation may have
    // reset the counter (its probe saw the outage) while ours was in
    // flight — exiting then would restart on strikes that were already
    // attributed to the outage.
    if (reconnectStrikes < RECONNECT_STRIKES_FATAL) return;
    log(`shardReconnecting: stuck loop (${reconnectStrikes} attempts in ${RECONNECT_WINDOW_MS/1000}s without resume) — exiting for systemd-managed restart`);
    try { client.destroy(); } catch {}
    process.exit(2);
  }
});

const LOGIN_BACKOFF_MS = [60_000, 5*60_000, 15*60_000, 30*60_000, 60*60_000];
const NET_PROBE_INTERVAL_MS = 15_000;
const TERMINAL_LOGIN_PATTERNS = /TOKEN_INVALID|invalid token|disallowed intents|invalid form body/i;

async function loginWithBackoff() {
  // Two failure classes, two policies:
  //  - API/HTTP errors (rate limit, Cloudflare 503, …): the request REACHED
  //    Discord and consumed IDENTIFY/rate budget — keep the conservative
  //    ladder (a stale CF 503 once locked the token after a restart storm).
  //  - Connection-level errors (DNS dead, ENOTFOUND, ECONNREFUSED): the
  //    request never left the host, no budget consumed. Probe DNS every
  //    15 s and retry immediately once the uplink is back, instead of
  //    sitting out a 900 s ladder step 12 minutes past network recovery
  //    (incident 2026-07-10). Network failures don't advance the ladder.
  // Preflight the privileged-intent portal state BEFORE the first IDENTIFY —
  // requesting MessageContent against a fresh app (toggle off) makes the
  // gateway reject the login outright. true → request it; false → token-only
  // mode; null (REST unreachable) → fall back to the marker a previous
  // disallowed-intents failure left behind.
  const mcAvailable = await messageContentAvailable(TOKEN);
  if (mcAvailable === false) {
    enterTokenOnlyMode('portal toggle is off');
  } else if (mcAvailable === true) {
    try { fs.unlinkSync(MC_MARKER); } catch {}
  } else if (fs.existsSync(MC_MARKER)) {
    enterTokenOnlyMode('preflight unreachable + previous disallowed-intents failure');
  }

  let apiAttempt = 0;
  for (;;) {
    try {
      await client.login(TOKEN);
      if (apiAttempt > 0) log(`login: succeeded after ${apiAttempt} retry/retries`);
      return;
    } catch (e) {
      const msg = e?.message || String(e);
      if (/disallowed intents/i.test(msg) && messageContentActive) {
        // NOT a token problem: the portal toggle is off and the preflight
        // could not tell us (REST unreachable / new failure mode). Leave a
        // marker so the supervisor restart boots straight into token-only
        // mode instead of looping on the same rejected IDENTIFY.
        try {
          fs.mkdirSync(path.dirname(MC_MARKER), { recursive: true });
          fs.writeFileSync(MC_MARKER,
            `${new Date().toISOString()} gateway rejected privileged intents\n`);
        } catch (we) { log(`marker write failed: ${we.message}`); }
        log('login failed: gateway rejected the privileged MESSAGE CONTENT intent ' +
            '(portal toggle is off — this is NOT a token problem). Exiting for a ' +
            'supervisor restart into token-only mode (DMs + @mentions work without ' +
            'any portal change).');
        process.exit(1);
      }
      if (e?.code === 'TokenInvalid' || TERMINAL_LOGIN_PATTERNS.test(msg)) {
        log(`login failed (terminal — token rotation needed): ${msg}`);
        process.exit(1);
      }
      // Fast path ONLY when the probe CONFIRMS the uplink is down. A
      // connection-shaped error with a green probe (ECONNRESET from a
      // Cloudflare edge ban, Discord-side TCP drops) is remote-caused,
      // may have consumed an IDENTIFY, and must take the ladder — the
      // error signature alone cannot distinguish local from remote.
      if (isNetworkError(msg) && !(await networkUp())) {
        log(`login failed (local network offline): ${msg}. probing uplink every ${NET_PROBE_INTERVAL_MS/1000}s`);
        // Always sleep at least one interval so a flapping uplink (probe
        // green, login still failing) can't tight-loop login attempts.
        do {
          await new Promise(r => setTimeout(r, NET_PROBE_INTERVAL_MS));
        } while (!(await networkUp()));
        log('login: uplink is back — retrying now');
        continue;
      }
      const delay = LOGIN_BACKOFF_MS[Math.min(apiAttempt, LOGIN_BACKOFF_MS.length - 1)];
      apiAttempt++;
      log(`login failed (attempt ${apiAttempt}, transient): ${msg}. backoff ${Math.round(delay/1000)}s`);
      await new Promise(r => setTimeout(r, delay));
    }
  }
}

// Zombie-watchdog. Fires every 60 s. If the gateway is not READY, or the
// last heartbeat-ack ping is bogus (-1) or stale (> 90 s — Discord's heartbeat
// interval is ~41 s), increment a strike counter. On 3 consecutive strikes
// the daemon exits with code 2 so systemd performs ONE controlled restart
// (gehärtet auf RestartSec=60, Burst=3 in 600 s). Single-shot strikes
// recover silently. The 60 s tick + 3 strikes give a 3-min detection window
// — short enough to recover from a silent half-connect within a single
// chat round-trip, long enough to absorb normal Discord-side reconnects.
const WATCHDOG_INTERVAL_MS = 60 * 1000;
const WATCHDOG_PING_MAX_MS = 90_000;
const WATCHDOG_STRIKES_FATAL = 3;
let watchdogStrikes = 0;
setInterval(async () => {
  if (!client.user) return; // not yet logged in
  const ping = client.ws?.ping;
  const status = client.ws?.status; // 0 = READY in discord.js v14
  const zombie = (status !== 0) || (ping == null) || (ping < 0) || (ping > WATCHDOG_PING_MAX_MS);
  if (zombie) {
    // Not-READY during a local network outage is expected offline behavior,
    // not a silent half-connect. Restarting would only trade a resumable
    // gateway session for a blind loginWithBackoff loop — freeze the strike
    // counter until the uplink is back (then a genuinely wedged gateway
    // still accumulates 3 strikes and restarts as before).
    if (!(await networkUp())) {
      log(`watchdog: gateway not READY but local network is down — strikes frozen at ${watchdogStrikes}`);
      return;
    }
    watchdogStrikes++;
    log(`watchdog: zombie indicator strike=${watchdogStrikes}/${WATCHDOG_STRIKES_FATAL} status=${status} ping=${ping}ms`);
    if (watchdogStrikes >= WATCHDOG_STRIKES_FATAL) {
      log('watchdog: zombie confirmed — exiting for systemd-managed restart');
      try { client.destroy(); } catch {}
      process.exit(2);
    }
  } else if (watchdogStrikes > 0) {
    log(`watchdog: recovered (status=${status} ping=${ping}ms)`);
    watchdogStrikes = 0;
  }
}, WATCHDOG_INTERVAL_MS);

loginWithBackoff();

process.on('SIGINT',  () => { log('shutting down'); client.destroy(); process.exit(0); });
process.on('SIGTERM', () => { log('shutting down'); client.destroy(); process.exit(0); });
