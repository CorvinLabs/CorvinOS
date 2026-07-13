#!/usr/bin/env node
// daemon.js — Slack frontend, drop-in replacement for the WhatsApp /
// Telegram / Discord daemons. Same shared inbox/outbox JSON contract.
// Inbound messages are tagged channel:"slack". Adapter routes the reply
// back via chat_id (= Slack channel ID, e.g. D01ABC for DMs, C01ABC for
// channels).
//
// Auth: Socket Mode (no public URL needed). Two tokens:
//   - SLACK_BOT_TOKEN   (xoxb-…)  — Bot User OAuth Token
//   - SLACK_APP_TOKEN   (xapp-…)  — App-Level Token with connections:write
//
// Required Bot Token Scopes:
//   chat:write, files:read, files:write, reactions:write,
//   channels:history, groups:history, im:history, mpim:history,
//   im:read, im:write
//
// Event Subscriptions (Socket Mode):
//   message.channels, message.groups, message.im, message.mpim
//
// See README → Slack bridge for the full click-by-click setup.

const fs   = require('fs');
const path = require('path');

require('../shared/js/bridge_state').exitIfDisabled('slack');

const { App } = require('@slack/bolt');

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
const { bridgeSettingsPath }    = require('../shared/js/bridge_paths');
const { makeStickyProgress }    = require('../shared/js/sticky_progress');

const ROOT = __dirname;
const PLUGIN_ROOT = path.resolve(ROOT, '..', '..');
const SHARED = path.resolve(ROOT, '..', 'shared');
const INBOX  = path.join(SHARED, 'inbox');
const OUTBOX = path.join(SHARED, 'outbox');
// ADR-0008 §8.3: settings live in <corvin_home>/bridges/slack/.
const SETTINGS_FILE = (ch => {
  const can = bridgeSettingsPath(ch);
  const leg = path.join(ROOT, 'settings.json');
  if (!fs.existsSync(can) && fs.existsSync(leg)) {
    try { fs.mkdirSync(path.dirname(can), { recursive: true }); fs.copyFileSync(leg, can); } catch {}
  }
  return fs.existsSync(can) ? can : leg;
})('slack');
const CHANNEL = 'slack';
for (const d of [INBOX, OUTBOX]) fs.mkdirSync(d, { recursive: true });

const HTTP_PORT = parseInt(process.env.SLACK_HTTP_PORT || '7894', 10);

const log = makeLogger('slack');
const { loadSettings, currentSettings } = makeSettingsAccessor(SETTINGS_FILE, log);
const settings = loadSettings(); // boot snapshot for tokens
const { rateAllow, authOk, readOnlyOk } = makeAuth({
  settingsFile: SETTINGS_FILE, currentSettings, loadSettings, logger: log,
  channel: CHANNEL,
});

const READ_ONLY_ACK = '🔒 You are read-only in this chat — you can read along, but you cannot drive the bot. Ask the owner to add you to the whitelist if that is wrong.';

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
  pluginRoot: PLUGIN_ROOT, channelLabel: 'Slack', currentSettings, logger: log,
});

const BOT_TOKEN = process.env.SLACK_BOT_TOKEN || settings.slack_bot_token;
const APP_TOKEN = process.env.SLACK_APP_TOKEN || settings.slack_app_token;
if (!BOT_TOKEN || !APP_TOKEN) {
  log('FATAL: SLACK_BOT_TOKEN and SLACK_APP_TOKEN both required');
  log('       (env vars or settings.json: slack_bot_token + slack_app_token)');
  process.exit(1);
}

// channel-id → ts of last user activity. Drives the typing indicator refresh.
const activeChannels = new Map();
// channel-id → { ts: <slack message timestamp>, channel: <id> } so we can
// remove our ⏳ reaction once the real reply ships (heartbeats don't count).
const pendingReactions = new Map();
let botUserId = null;

// Sticky-progress + finalize-guard state for _progress/_heartbeat outbox
// payloads. Mirrors the Discord/Telegram daemons (shared/js/sticky_progress.js):
// the first _progress payload for a turn posts a new message; every
// subsequent one edits it in place via chat.update() instead of flooding the
// channel with one message per tool call. Once the real reply lands, any
// stale _progress/_heartbeat file for the same msg_id (outbox
// alphabetical-sort race) is dropped silently.
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

// ─── Slack app (Socket Mode) ────────────────────────────────────────────────
const app = new App({
  token: BOT_TOKEN,
  appToken: APP_TOKEN,
  socketMode: true,
  logLevel: 'warn',
});

// Cache the bot's own user-id so we can ignore its own messages.
app.client.auth.test().then(r => {
  botUserId = r.user_id;
  log(`logged in as @${r.user} (bot_id=${botUserId}, team=${r.team})`);
}).catch(e => log(`auth.test failed: ${e.message}`));

// Helper: download a private Slack file via its url_private (needs Bearer).
async function downloadSlackFile(url, dest) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${BOT_TOKEN}` } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(dest, buf);
}

app.event('message', async ({ event, client }) => {
  try {
    // Skip bot/system messages and our own posts.
    if (event.subtype && event.subtype !== 'file_share') return;
    if (event.bot_id) return;
    if (event.user && event.user === botUserId) return;

    const userId  = event.user;
    const chatId  = event.channel;     // C... / G... / D...
    const text    = event.text || '';
    const msgTs   = event.ts;          // Slack uses string timestamps as message id

    if (!userId) return;

    {
      const ro = readOnlyOk(userId, text, String(chatId));
      if (ro.isReadOnly) {
        const base = { from: String(userId), chat_id: chatId, ts: Date.now() };
        // Layer-17: read-only senders may run /consent on/off/<ttl>/status
        // and /share <text> BEFORE the observer-buffer path.
        const cc = inChatCmds.dispatchReadOnlyConsent({
          text, channel: CHANNEL, chatKey: String(chatId), uid: String(userId),
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
            try { await client.chat.postMessage({ channel: chatId, text: cc.reply }); } catch {}
          }
          log(`read-only consent: ${cc.kind} from=${userId} chat=${chatId}`);
          return;
        }
        // Layer-19 — /join /pass for read-only senders.
        const dd = inChatCmds.dispatchReadOnlyDisclosure({
          text, channel: CHANNEL, chatKey: String(chatId), uid: String(userId),
          settingsFile: SETTINGS_FILE,
        });
        if (dd) {
          if (dd.reply) {
            try { await client.chat.postMessage({ channel: chatId, text: dd.reply }); } catch {}
          }
          log(`read-only disclosure: ${dd.kind} from=${userId} chat=${chatId}`);
          return;
        }
        // Layer-21 — /propose <text> for read-only senders.
        const pp = inChatCmds.dispatchReadOnlyProposal({
          text, channel: CHANNEL, chatKey: String(chatId), uid: String(userId),
          settingsFile: SETTINGS_FILE,
        });
        if (pp) {
          if (pp.reply) {
            try { await client.chat.postMessage({ channel: chatId, text: pp.reply }); } catch {}
          }
          log(`read-only proposal: ${pp.kind} from=${userId} chat=${chatId}`);
          return;
        }
        // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure for read-only
        // OBSERVERS too. Their message is forwarded to the LLM (observer transcript),
        // so they are interacting with the AI and must be told — not only reactively
        // via /join. Shown once per (chat, uid), same ledger as the owner path.
        if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: String(chatId), uid: String(userId) })) {
          const ocard = inChatCmds.disclosureCardText({
            channel: CHANNEL, ownerLabel: currentSettings().operator_name || '(owner)',
            hasObserverTranscript: true,
            lang: currentSettings().lang || 'en',
          });
          if (ocard) {
            try { await client.chat.postMessage({ channel: chatId, text: ocard }); } catch {}
            const oseen = inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: String(chatId), uid: String(userId), action: 'pending' });
            if (!oseen.ok) log(`[disclosure] observer mark_seen failed — ${oseen.error}`);
            log(`disclosure shown (observer) uid=${userId} ch=${chatId}`);
          }
        }
        const forwarded = maybeForwardAsObserver(userId, text, chatId, base);
        if (forwarded) {
          // Silent forward — the LLM will see the line on the next owner turn.
        } else if (ro.firstDrop) {
          try { await client.chat.postMessage({ channel: chatId, text: READ_ONLY_ACK }); } catch {}
        }
        return;
      }
    }
    if (!authOk(userId, text, chatId)) {
      try {
        await client.chat.postMessage({
          channel: chatId,
          text: `You are not authorized. Your user-id: \`${userId}\`\n` +
                `Add it to settings.json → whitelist (or send "/auth <pin>").\n` +
                `The owner can also open this chat with /all on.`,
        });
      } catch {}
      return;
    }
    // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure on first encounter.
    // Shown once per (chat, uid).
    if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: String(chatId), uid: String(userId) })) {
      const card = inChatCmds.disclosureCardText({
        channel: CHANNEL, ownerLabel: '(owner)',
        hasObserverTranscript: false,
        lang: currentSettings().lang || 'en',
      });
      if (card) {
        try { await client.chat.postMessage({ channel: chatId, text: card }); } catch {}
        inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: String(chatId), uid: String(userId), action: 'pending' });
        log(`disclosure shown uid=${userId} ch=${chatId}`);
      }
    }
    if (!rateAllow(userId, currentSettings().rate_limit_per_hour || 30)) {
      try { await client.chat.postMessage({ channel: chatId, text: 'Rate limit reached. Please try again later.' }); } catch {}
      return;
    }

    // Owner-side /on /off /status — opt-in toggle. Settings.json without
    // `enabled_chats` keeps the legacy default-on behaviour.
    {
      const tog = chatToggle.handleToggleCommand({
        text, chatKey: String(chatId), isOwner: true,
        settingsFile: SETTINGS_FILE,
      });
      if (tog) {
        try { await client.chat.postMessage({ channel: chatId, text: tog.reply }); } catch {}
        log(`toggle ${tog.kind} chat=${chatId}`);
        return;
      }
    }
    if (!chatToggle.isChatEnabled(currentSettings(), String(chatId))) {
      log(`chat ${chatId} not enabled, ignoring`);
      return;
    }

    const cmdLower = text.trim().toLowerCase();
    if (cmdLower === '/start') {
      await client.chat.postMessage({
        channel: chatId,
        text: `Hi! I'm the Claude bridge on Slack.\n` +
              `Your user-id: \`${userId}\`\nAdd it to settings.json → whitelist.`,
      });
      return;
    }
    // Note: /new /clear /reset are now owned by the in-chat dispatcher
    // (shared/js/in_chat_commands.js) so the layer-8 session-reset
    // (skills + forge + voice) all happens in one place.
    if (cmdLower === '/stop' || cmdLower === '/cancel' || cmdLower === '/abbruch' || cmdLower === '/halt') {
      log(`cancel cmd from ${userId} in channel ${chatId}`);
      writeInbox({ from: String(userId), chat_id: chatId, _cancel: true, ts: Date.now() });
      return;  // adapter SIGTERMs the running subproc and writes ACK
    }
    // /btw <text> — Layer 13 mid-stream injection.
    {
      const btwMatch = (text || '').match(/^\/btw(?:\s+([\s\S]+))?$/i);
      if (btwMatch) {
        const btwText = (btwMatch[1] || '').trim();
        log(`btw cmd from ${userId} in channel ${chatId} (len=${btwText.length})`);
        writeInbox({ from: String(userId), chat_id: chatId, _btw: true, text: btwText, ts: Date.now() });
        return;
      }
    }

    {
      const cwk = inChatCmds.dispatch({
        text, channel: CHANNEL, chatKey: String(chatId),
        isOwner: true,  // authOk checked the whitelist
        settingsFile: SETTINGS_FILE,
      });
      if (cwk) {
        try { await client.chat.postMessage({ channel: chatId, text: cwk.reply }); } catch {}
        log(`in-chat-cmd ${cwk.kind} → ${chatId}`);
        return;
      }
    }

    activeChannels.set(chatId, Date.now());

    // Hourglass reaction on the user's message → instant ack. Removed when
    // the real reply ships (see sendSlack).
    try {
      await client.reactions.add({
        channel: chatId, timestamp: msgTs, name: 'hourglass_flowing_sand',
      });
      pendingReactions.set(chatId, { channel: chatId, ts: msgTs });
    } catch (e) {
      // Slack returns "already_reacted" if we duplicate; non-fatal.
    }

    const base = { from: String(userId), chat_id: chatId, ts: Date.now() };

    // Handle file attachments. Slack delivers them via event.files (array).
    if (event.files && event.files.length > 0) {
      const f = event.files[0];
      const safeName = (f.name || 'file').replace(/[^a-zA-Z0-9._-]/g, '_');
      const id = newMsgId();
      const dest = path.join(INBOX, `${id}_${safeName}`);
      try {
        await downloadSlackFile(f.url_private_download || f.url_private, dest);
      } catch (e) {
        log(`file download failed: ${e.message}`);
        return;
      }
      const mt  = (f.mimetype || '').toLowerCase();
      const ext = (f.name || '').slice((f.name || '').lastIndexOf('.')).toLowerCase();
      if (mt.startsWith('image/') || /\.(png|jpe?g|gif|webp|bmp)$/.test(ext)) {
        writeInbox({ ...base, image_path: dest, caption: text });
      } else if (mt.startsWith('audio/') || /\.(ogg|mp3|m4a|wav|opus)$/.test(ext)) {
        writeInbox({ ...base, audio_path: dest, caption: text });
      } else if (mt.startsWith('video/') || /\.(mp4|mov|webm|mkv)$/.test(ext)) {
        writeInbox({ ...base, video_path: dest, caption: text });
      } else {
        writeInbox({ ...base, document_path: dest, document_name: f.name, mimetype: f.mimetype, caption: text });
      }
      return;
    }

    if (text.trim()) writeInbox({ ...base, text });
  } catch (e) {
    log(`event-message error: ${e.message}`);
  }
});

// ─── Outbox processing ──────────────────────────────────────────────────────
async function sendSlack(payload, _fpath) {
  const chId = payload.chat_id;
  if (!chId) { log(`no chat_id, skipping`); return; }

  // Stale-finalize gate — same race as the other daemons: the outbox dir is
  // processed in alphabetical order, so `{msg_id}_00.json` (real reply) can
  // sort BEFORE `{msg_id}_hb.json` / `{msg_id}_sNN.json` (heartbeat/progress).
  // Once the real reply for a msg_id has been delivered, drop every other
  // file for that msg_id silently.
  const msgId = payload.msg_id;
  if ((payload._progress || payload._heartbeat) && sticky.isFinalized(msgId)) {
    log(`drop stale ${payload._progress ? 'progress' : 'heartbeat'} for finalized ${msgId}`);
    return;
  }

  // Progress updates: edit a single sticky message instead of flooding the
  // channel with one message per tool call. On the first _progress payload
  // we post a new message and remember its ts; every subsequent one edits
  // it via chat.update. When the real reply arrives the sticky message is
  // deleted first.
  if (payload._progress && payload.text) {
    const existing = sticky.getProgress(chId);
    if (existing) {
      try {
        await app.client.chat.update({ channel: chId, ts: existing.ts, text: payload.text });
        return;
      } catch (e) {
        // Edit failed (message deleted externally or a Slack API error).
        // Delete the old sticky explicitly so it does not remain visible
        // alongside the new one.
        log(`sticky update failed, falling back to new message: ${e && e.message || e}`);
        try { await app.client.chat.delete({ channel: chId, ts: existing.ts }); } catch {}
        sticky.clearProgress(chId);
      }
    }
    try {
      const sent = await app.client.chat.postMessage({ channel: chId, text: payload.text });
      sticky.setProgress(chId, { ts: sent.ts });
    } catch {}
    return;
  }

  // Heartbeat: slot into the sticky system so it gets deleted when the real
  // reply arrives. If a progress update already claimed the sticky slot,
  // skip silently — the progress message is more informative anyway.
  if (payload._heartbeat && payload.text) {
    if (!sticky.hasProgress(chId)) {
      try {
        const sent = await app.client.chat.postMessage({ channel: chId, text: payload.text });
        sticky.setProgress(chId, { ts: sent.ts });
      } catch {}
    }
    return;
  }

  // Real reply incoming — delete the sticky progress message first so the
  // channel shows the answer cleanly without a stale status line above it.
  if (sticky.hasProgress(chId)) {
    const prog = sticky.getProgress(chId);
    try {
      await app.client.chat.delete({ channel: chId, ts: prog.ts });
    } catch (e) {
      // First delete attempt failed — retry once after a short pause, same
      // belt-and-braces approach as the Discord daemon.
      log(`sticky delete failed (will retry): ${e && e.message || e}`);
      await new Promise((r) => setTimeout(r, 400));
      try { await app.client.chat.delete({ channel: chId, ts: prog.ts }); } catch (e2) {
        log(`sticky delete retry also failed: ${e2 && e2.message || e2}`);
      }
    }
    sticky.clearProgress(chId);
  }

  // Mark this turn finalized BEFORE we touch Slack so any racing
  // progress/heartbeat that arrives mid-send is correctly classified.
  sticky.markFinalized(msgId);

  // Slack text limit per message is 4000 chars; we split conservatively.
  if (payload.text) {
    const TEXT_LIMIT = 3500;
    if (payload.text.length <= TEXT_LIMIT) {
      await app.client.chat.postMessage({ channel: chId, text: payload.text });
    } else {
      for (let i = 0; i < payload.text.length; i += TEXT_LIMIT) {
        await app.client.chat.postMessage({ channel: chId, text: payload.text.slice(i, i + TEXT_LIMIT) });
      }
    }
  }
  if (payload.voice_path && fs.existsSync(payload.voice_path)) {
    await app.client.files.uploadV2({
      channel_id: chId, file: fs.createReadStream(payload.voice_path),
      filename: 'voice.ogg', title: 'voice',
    });
  }
  if (payload.image_path && fs.existsSync(payload.image_path)) {
    await app.client.files.uploadV2({
      channel_id: chId, file: fs.createReadStream(payload.image_path),
      filename: path.basename(payload.image_path),
      initial_comment: payload.image_caption || undefined,
    });
  }
  if (payload.document_path && fs.existsSync(payload.document_path)) {
    await app.client.files.uploadV2({
      channel_id: chId, file: fs.createReadStream(payload.document_path),
      filename: payload.document_name || path.basename(payload.document_path),
      initial_comment: payload.document_caption || undefined,
    });
  }
  if (payload.video_path && fs.existsSync(payload.video_path)) {
    await app.client.files.uploadV2({
      channel_id: chId, file: fs.createReadStream(payload.video_path),
      filename: 'video.mp4',
      initial_comment: payload.video_caption || undefined,
    });
  }
  activeChannels.delete(chId);

  if (!payload._heartbeat && pendingReactions.has(chId)) {
    const { channel, ts } = pendingReactions.get(chId);
    try {
      await app.client.reactions.remove({ channel, timestamp: ts, name: 'hourglass_flowing_sand' });
    } catch {}
    pendingReactions.delete(chId);
  }
}

startOutboxPoller({
  outboxDir: OUTBOX, channel: CHANNEL, sendFn: sendSlack, logger: log,
});

process.on('unhandledRejection', r => log(`unhandledRejection: ${r && r.message || r}`));

// HTTP /status — same shape as the other bridges so bridge.sh status works.
startHealthServer({
  port: HTTP_PORT, kind: 'slack', logger: log,
  getStatus: () => ({
    paired: !!botUserId,
    bot_user_id: botUserId,
    whitelist_size: (currentSettings().whitelist || []).length,
    pending_outbox: fs.readdirSync(OUTBOX).filter(f => f.endsWith('.json')).length,
  }),
});

// Kick off the Socket Mode connection.
app.start().catch(e => { log(`bolt.start failed: ${e.message}`); process.exit(1); });

process.on('SIGINT',  () => { log('shutting down'); app.stop().finally(() => process.exit(0)); });
process.on('SIGTERM', () => { log('shutting down'); app.stop().finally(() => process.exit(0)); });
