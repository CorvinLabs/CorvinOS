#!/usr/bin/env node
// daemon.js — Discord-Bot frontend, drop-in replacement for the Telegram /
// WhatsApp daemons. Uses the same shared inbox/outbox in bridges/shared/.
// All inbound messages are tagged channel:"discord". Adapter routes the
// reply back via the chat_id (= Discord channel ID).
//
// Setup:
//   1. https://discord.com/developers/applications → New Application →
//      Bot tab → Reset Token → copy.
//   2. Enable "MESSAGE CONTENT INTENT" under Privileged Gateway Intents.
//   3. OAuth2 → URL Generator → scopes: bot. Permissions: Read Messages,
//      Send Messages, Attach Files, Read Message History. Copy URL, open
//      it in browser, invite bot to your server (or to a personal server).
//   4. Put token in settings.json -> discord_token.
//   5. Add your Discord user ID to whitelist (right-click your name in
//      Discord → "User-ID copy"; needs Developer Mode enabled in
//      Settings → Advanced).
//   6. Run via systemd or `node daemon.js`.

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

// Enable/disable gate — exit-0 before loading discord.js if the channel
// has been turned off via the Bridges console (state.json).
require('../shared/js/bridge_state').exitIfDisabled('discord');

const { Client, GatewayIntentBits, Partials, AttachmentBuilder } = require('discord.js');

// ── Shared bridge runtime (Phase 2 refactor) ────────────────────────────────
const { makeLogger }            = require('../shared/js/logger');
const { makeSettingsAccessor }  = require('../shared/js/settings');
const { makeAuth }              = require('../shared/js/auth');
const { startOutboxPoller }     = require('../shared/js/outbox');
const { startHealthServer }     = require('../shared/js/health-server');
const { makeAnnouncer }         = require('../shared/js/local-announce');
const { newMsgId }              = require('../shared/js/msg-id');
const inChatCmds                = require('../shared/js/in_chat_commands');
const chatToggle                = require('../shared/js/chat_toggle');
const slashCommands             = require('./slash_commands');
const { bridgeSettingsPath }    = require('../shared/js/bridge_paths');

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
const progressMessages = new Map();
// msg_id → finalizedAt-ts. Once a real reply has been delivered for a given
// turn, any further _progress / _heartbeat outbox files we still encounter
// for that same msg_id are stale (sort-order race between `_00.json` and
// `_sNN.json` / `_hb.json`) and we drop them silently. TTL keeps the map
// bounded.
const finalizedMsgIds = new Map();
const FINALIZED_TTL_MS = 60_000;
function _isFinalized(msgId) {
  if (!msgId) return false;
  const ts = finalizedMsgIds.get(String(msgId));
  return !!ts && (Date.now() - ts) < FINALIZED_TTL_MS;
}
function _markFinalized(msgId) {
  if (!msgId) return;
  finalizedMsgIds.set(String(msgId), Date.now());
}
setInterval(() => {
  const cutoff = Date.now() - FINALIZED_TTL_MS;
  for (const [k, ts] of finalizedMsgIds.entries()) {
    if (ts < cutoff) finalizedMsgIds.delete(k);
  }
}, 30_000).unref?.();

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
    const _isOwner = authOk(userId, text, channelId);
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
    const text = msg.content || '';
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
    const _isOwner = authOk(userId, text, msg.channel.id);
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
async function sendDiscord(payload, _fpath) {
  const chId = payload.chat_id;
  if (!chId) { log(`no chat_id, skipping`); return; }

  // Stale-finalize gate. The outbox dir is processed in alphabetical order,
  // and `{msg_id}_00.json` (real reply) sorts BEFORE `{msg_id}_hb.json`
  // (heartbeat) and `{msg_id}_sNN.json` (progress). So when several files
  // for the same turn land between two polling ticks the daemon would
  // dispatch the real reply, then re-send a progress sticky on top of it
  // ("agent writes itself messages"). Once we've sent the final reply for
  // a given msg_id, drop every other file for that msg_id silently.
  const msgId = payload.msg_id;
  if ((payload._progress || payload._heartbeat) && _isFinalized(msgId)) {
    log(`drop stale ${payload._progress ? 'progress' : 'heartbeat'} for finalized ${msgId}`);
    return;
  }

  const ch = await client.channels.fetch(chId);
  if (!ch) { log(`channel ${chId} not found`); return; }

  // Progress updates: edit a single sticky message instead of flooding the
  // chat with one message per tool call. On the first _progress payload we
  // send a new message and remember it; every subsequent one edits it.
  // When the real reply arrives the sticky message is deleted first.
  if (payload._progress && payload.text) {
    const existing = progressMessages.get(chId);
    if (existing && existing.msg) {
      try { await existing.msg.edit(payload.text); return; } catch {
        // Edit failed (message deleted externally or Discord error). Delete the
        // old sticky explicitly so it does not remain visible alongside the new
        // one — without this the original heartbeat would linger as a ghost
        // message and the user sees two messages.
        try { await existing.msg.delete(); } catch {}
        progressMessages.delete(chId);
      }
    }
    try {
      const sent = await ch.send(payload.text);
      progressMessages.set(chId, { msg: sent, msgId: msgId || null });
    } catch {}
    return;
  }

  // Heartbeat: slot into the sticky system so it gets deleted when the real
  // reply arrives. If a progress update already claimed the sticky slot,
  // skip silently — the progress message is more informative anyway.
  if (payload._heartbeat && payload.text) {
    if (!progressMessages.has(chId)) {
      try {
        const sent = await ch.send(payload.text);
        progressMessages.set(chId, { msg: sent, msgId: msgId || null });
      } catch {}
    }
    return;
  }

  // Real reply incoming — delete the sticky progress message first so the
  // chat shows the answer cleanly without a stale status line above it.
  if (progressMessages.has(chId)) {
    const prog = progressMessages.get(chId);
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
    progressMessages.delete(chId);
  }

  // Mark this turn finalized BEFORE we touch Discord so any racing
  // progress/heartbeat that arrives mid-send is correctly classified.
  _markFinalized(msgId);

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

startOutboxPoller({
  outboxDir: OUTBOX, channel: CHANNEL, sendFn: sendDiscord, logger: log,
});

process.on('unhandledRejection', r => log(`unhandledRejection: ${r && r.message || r}`));

// HTTP /status
startHealthServer({
  port: HTTP_PORT, kind: 'discord', logger: log,
  getStatus: () => ({
    paired: !!client.user,
    bot_tag: client.user?.tag || null,
    whitelist_size: (currentSettings().whitelist || []).length,
    pending_outbox: fs.readdirSync(OUTBOX).filter(f => f.endsWith('.json')).length,
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
client.on('shardReconnecting', sid => {
  log(`shardReconnecting shard=${sid}`);
  const now = Date.now();
  reconnectStrikes = (now - lastReconnectAt < RECONNECT_WINDOW_MS) ? reconnectStrikes + 1 : 1;
  lastReconnectAt  = now;
  if (reconnectStrikes >= RECONNECT_STRIKES_FATAL) {
    log(`shardReconnecting: stuck loop (${reconnectStrikes} attempts in ${RECONNECT_WINDOW_MS/1000}s without resume) — exiting for systemd-managed restart`);
    try { client.destroy(); } catch {}
    process.exit(2);
  }
});

const LOGIN_BACKOFF_MS = [60_000, 5*60_000, 15*60_000, 30*60_000, 60*60_000];
const TERMINAL_LOGIN_PATTERNS = /TOKEN_INVALID|invalid token|disallowed intents|invalid form body/i;

async function loginWithBackoff() {
  for (let attempt = 0; ; attempt++) {
    try {
      await client.login(TOKEN);
      if (attempt > 0) log(`login: succeeded after ${attempt} retry/retries`);
      return;
    } catch (e) {
      const msg = e?.message || String(e);
      if (e?.code === 'TokenInvalid' || TERMINAL_LOGIN_PATTERNS.test(msg)) {
        log(`login failed (terminal — token rotation needed): ${msg}`);
        process.exit(1);
      }
      const delay = LOGIN_BACKOFF_MS[Math.min(attempt, LOGIN_BACKOFF_MS.length - 1)];
      log(`login failed (attempt ${attempt + 1}, transient): ${msg}. backoff ${Math.round(delay/1000)}s`);
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
setInterval(() => {
  if (!client.user) return; // not yet logged in
  const ping = client.ws?.ping;
  const status = client.ws?.status; // 0 = READY in discord.js v14
  const zombie = (status !== 0) || (ping == null) || (ping < 0) || (ping > WATCHDOG_PING_MAX_MS);
  if (zombie) {
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
