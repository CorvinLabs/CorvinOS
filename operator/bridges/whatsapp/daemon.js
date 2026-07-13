#!/usr/bin/env node
// daemon.js — WhatsApp bridge daemon.
//
// Two operational modes:
//   --mock       : skip Baileys, expose only the HTTP test API. Used for E2E
//                  testing without a real WhatsApp account. POST /mock/inbound
//                  to inject a fake incoming message; outbound messages land
//                  in the local outbox as JSON files (no real send).
//   (default)    : connect to WhatsApp via Baileys. First run prints a QR
//                  code in the terminal — scan it with WhatsApp on your
//                  phone (Settings → Linked Devices) to pair. Auth state is
//                  persisted in ./auth/, so subsequent runs reconnect.
//
// Inbox/Outbox protocol (filesystem-based, simple, language-agnostic):
//   operator/voice/whatsapp/inbox/<id>.json   <- written when a message arrives
//   operator/voice/whatsapp/outbox/<id>.json  <- written by adapter.py to send
//
// The daemon polls the outbox every 500ms and removes processed files.

const fs = require('fs');
const path = require('path');
const http = require('http');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { bridgeSettingsPath } = require('../shared/js/bridge_paths');

require('../shared/js/bridge_state').exitIfDisabled('whatsapp');

const ROOT = __dirname;
const SHARED = path.resolve(ROOT, '..', 'shared');
const INBOX = path.join(SHARED, 'inbox');
const OUTBOX = path.join(SHARED, 'outbox');
const AUTH = path.join(ROOT, 'auth');
// ADR-0008 §8.3: settings live in <corvin_home>/bridges/whatsapp/.
const SETTINGS_FILE = (ch => {
  const can = bridgeSettingsPath(ch);
  const leg = path.join(ROOT, 'settings.json');
  if (!fs.existsSync(can) && fs.existsSync(leg)) {
    try { fs.mkdirSync(path.dirname(can), { recursive: true }); fs.copyFileSync(leg, can); } catch {}
  }
  return fs.existsSync(can) ? can : leg;
})('whatsapp');
const CHANNEL = 'whatsapp';
// Standalone TTS helper used to attach a voice-note to slash-command replies
// (e.g. /welcome). Slash-commands never reach the adapter's voice path because
// they are answered straight from the daemon — so we synthesize on this side.
const SAY_HELPER = path.resolve(ROOT, '..', '..', 'voice', 'scripts', 'say.py');

const inChatCmds = require('../shared/js/in_chat_commands');
const chatState = require('./chat_state');
const { makeStickyProgress } = require('../shared/js/sticky_progress');

// ── say.py TTS helper ─────────────────────────────────────────────────────
// Spawns operator/voice/scripts/say.py to produce an OGG-Opus voice-note.
// Returns the absolute path on success, null on any silent skip (no API key,
// quota, network). Stdout is the absolute path; empty stdout = skipped.
function synthesizeVoiceNoteForText(text, lang = 'de', voice = 'shimmer', timeoutMs = 30000) {
  return new Promise((resolve) => {
    if (!text || !text.trim()) return resolve(null);
    let outPath = path.join(OUTBOX, `welcome_${Date.now()}.ogg`);
    let stdoutBuf = '';
    let stderrBuf = '';
    let resolved = false;
    const finish = (val) => { if (!resolved) { resolved = true; resolve(val); } };
    let child;
    try {
      // Use the interpreter the adapter runs under (anaconda) when configured
      // via PYTHON in service.env — system python3 typically lacks `openai`,
      // which makes say.py silently fall through to a text-only reply.
      const pyBin = process.env.PYTHON || 'python3';
      child = spawn(pyBin, [SAY_HELPER, outPath, text, lang, voice], {
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (e) {
      log(`say.py spawn failed: ${e.message}`);
      return finish(null);
    }
    const timer = setTimeout(() => {
      try { child.kill('SIGTERM'); } catch {}
      log(`say.py timed out after ${timeoutMs}ms`);
      finish(null);
    }, timeoutMs);
    child.stdout.on('data', (b) => { stdoutBuf += b.toString(); });
    child.stderr.on('data', (b) => { stderrBuf += b.toString(); });
    child.on('error', (e) => {
      clearTimeout(timer);
      log(`say.py error: ${e.message}`);
      finish(null);
    });
    child.on('close', () => {
      clearTimeout(timer);
      const trimmed = stdoutBuf.trim();
      if (!trimmed) {
        // Silent skip — say.py wrote diagnostics to stderr.
        if (stderrBuf.trim()) log(`say.py skip: ${stderrBuf.trim().slice(0, 200)}`);
        return finish(null);
      }
      if (!fs.existsSync(trimmed)) {
        log(`say.py reported path that does not exist: ${trimmed}`);
        return finish(null);
      }
      finish(trimmed);
    });
  });
}

for (const d of [INBOX, OUTBOX, AUTH]) {
  fs.mkdirSync(d, { recursive: true });
}

const argv = process.argv.slice(2);
const argSet = new Set(argv);
const MOCK = argSet.has('--mock');
const PAIR_ONLY = argSet.has('--pair-only');
// --pair-code <phoneNumber>: request an 8-digit pairing code (no QR scan).
// Phone number is digits only, no plus, e.g. 491729809432.
const PAIR_CODE_IDX = argv.indexOf('--pair-code');
const PAIR_CODE_PHONE = PAIR_CODE_IDX >= 0 ? argv[PAIR_CODE_IDX + 1] : null;
const HTTP_PORT = parseInt(process.env.WA_HTTP_PORT || '7891', 10);

function loadSettings() {
  try {
    const s = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8'));
    if (!Array.isArray(s.enabled_chats)) s.enabled_chats = [];
    return s;
  } catch {
    return {
      whitelist: [],
      enabled_chats: [],
      rate_limit_per_hour: 30,
      always_voice: false,
      voice_threshold_chars: 200,
      pin: null,
    };
  }
}

// Atomic write: tmp-file + rename, so a crash mid-write can't corrupt settings.
function saveSettings() {
  // Always persist the LIVE settings object (currentSettings() may have
  // reloaded from disk since boot). Writing `settings` (boot snapshot) after
  // a hot-reload would silently overwrite external changes with stale RAM.
  const live = currentSettings();
  const tmp = SETTINGS_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(live, null, 2));
  fs.renameSync(tmp, SETTINGS_FILE);
}

// Hot-reload: read paths go through currentSettings(), which reloads the file
// on mtime change. Edits in settings.json (whitelist, enabled_chats,
// debug_chats, rate_limit, pin) take effect without a daemon restart.
// Mutations from the daemon itself (enableChat/disableChat → saveSettings)
// change the mtime anyway, so the cache syncs on the next read.
let _settingsCache = null;
let _settingsMtime = 0;
function currentSettings() {
  try {
    const m = fs.statSync(SETTINGS_FILE).mtimeMs;
    if (m !== _settingsMtime) {
      _settingsCache = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8'));
      if (_settingsMtime > 0) log(`settings.json reloaded (mtime=${m})`);
      _settingsMtime = m;
    }
  } catch { /* keep last good */ }
  return _settingsCache || settings;
}

// Single-JID wrappers — keep the legacy 1-arg shape for callers that don't
// have the full Baileys message at hand (e.g. setup scripts, manual edits).
// Multi-form callsites should use chatState.chatJidsForMessage(m) directly
// and pass the resulting array to the *Chats helpers.
function isChatEnabled(jid) {
  return chatState.isAnyChatEnabled([jid], currentSettings());
}

function enableChat(jid) {
  chatState.enableChats([jid], currentSettings());
  saveSettings();
}

function disableChat(jid) {
  chatState.disableChats([jid], currentSettings());
  saveSettings();
}

// Multi-JID-aware variants used by the message handler. They take the full
// Baileys message so both `key.remoteJid` AND `key.remoteJidAlt` enter the
// state — the load-bearing fix for the "/off didn't actually disable"
// regression where the lid form and the phone form of the same chat were
// stored independently.
function isChatEnabledForMessage(m) {
  return chatState.isAnyChatEnabled(chatState.chatJidsForMessage(m),
                                    currentSettings());
}

function enableChatForMessage(m) {
  // Operate on currentSettings() so hot-reloaded external edits are not
  // overwritten. After saveSettings() the file mtime changes and the
  // next currentSettings() call re-syncs _settingsCache from disk.
  chatState.enableChats(chatState.chatJidsForMessage(m), currentSettings());
  saveSettings();
}

function disableChatForMessage(m) {
  chatState.disableChats(chatState.chatJidsForMessage(m), currentSettings());
  saveSettings();
}

function isDebugChat(jid) {
  const norm = normalizeJid(jid);
  return (currentSettings().debug_chats || []).map(normalizeJid).includes(norm);
}

function enableDebugChat(jid) {
  const norm = normalizeJid(jid);
  if (!Array.isArray(settings.debug_chats)) settings.debug_chats = [];
  if (!isDebugChat(norm)) {
    settings.debug_chats.push(norm);
    saveSettings();
  }
}

function disableDebugChat(jid) {
  const norm = normalizeJid(jid);
  settings.debug_chats = (settings.debug_chats || [])
    .filter(j => normalizeJid(j) !== norm);
  saveSettings();
}

function log(...args) {
  const ts = new Date().toISOString();
  console.log(`[${ts}] [wa-daemon]`, ...args);
}

function newMsgId() {
  return Date.now().toString(36) + '_' + crypto.randomBytes(3).toString('hex');
}

// Most recent QR string emitted by Baileys. Served as PNG at GET /qr and as
// an auto-refreshing HTML page at GET /pair, so the user can scan from their
// browser instead of fiddling with terminal ASCII or pairing codes.
let lastQR = null;
let lastQRTimestamp = 0;

// Rate limit: in-memory map of sender -> array of timestamps in last hour.
const rateMap = new Map();

// Active senders: map of sender JID -> timestamp of last incoming message.
// Used to refresh the WA "composing" typing indicator until we send a reply.
const activeSenders = new Map();
// Pending reactions: map of sender JID -> message key of the user's last
// inbound message. We set a ⏳ reaction on receipt and flip it to ✅ when the
// real reply (not the heartbeat) goes out, so the user gets immediate visual
// feedback that the bridge picked up their request.
const pendingReactions = new Map();

// Sticky-progress + finalize-guard state for _progress/_heartbeat outbox
// payloads. Mirrors the Discord/Telegram/Slack daemons
// (shared/js/sticky_progress.js): the first _progress payload for a turn
// sends a new message and remembers its Baileys message key; every
// subsequent one edits it in place via the WhatsApp message-edit feature
// (sendMessage({..., edit: key})) instead of flooding the chat with one
// message per tool call. Once the real reply lands, any stale
// _progress/_heartbeat file for the same msg_id (outbox alphabetical-sort
// race) is dropped silently.
const sticky = makeStickyProgress({ ttlMs: 60_000 });

// Message IDs we sent ourselves (replies, acks, heartbeats, etc.). Needed
// because our own outbound messages bounce back via messages.upsert with
// fromMe=true. Without this set we'd loop on our own replies in 1:1 chats.
// Grows by O(messages per session); harmless in practice.
const ownSentIds = new Set();
async function safeSend(socket, jid, content) {
  const sent = await socket.sendMessage(jid, content);
  if (sent?.key?.id) ownSentIds.add(sent.key.id);
  return sent;
}
function rateAllow(sender, perHour) {
  const now = Date.now();
  const HOUR = 60 * 60 * 1000;
  const arr = (rateMap.get(sender) || []).filter(t => now - t < HOUR);
  if (arr.length >= perHour) {
    rateMap.set(sender, arr);
    return false;
  }
  arr.push(now);
  rateMap.set(sender, arr);
  return true;
}

const settings = loadSettings();

// Startup: clear enabled_chats so every bridge session starts fresh.
// Chats must be explicitly /on'd within each session. This prevents a stale
// enabled_chats list from silently activating the bot in groups or 1:1 chats
// the owner no longer wants active. disabled_chats is kept — /off is permanent.
// Opt out by setting "chats_persist_on_restart": true in settings.json.
{
  const _cs = currentSettings();
  if (_cs.chats_persist_on_restart !== true) {
    const _prev = (_cs.enabled_chats || []).length;
    if (_prev > 0) {
      _cs.enabled_chats = [];
      saveSettings();
      log(`startup: cleared ${_prev} enabled_chats — all chats start OFF (use /on to activate)`);
    }
  } else {
    log('startup: chats_persist_on_restart=true — keeping enabled_chats from last session');
  }
}

// Normalize a JID for whitelist comparison. WhatsApp delivers messages
// with a per-device suffix like ":11" (491729809432:11@s.whatsapp.net) and
// so a separate @lid identifier. We strip these so the whitelist only
// needs to contain the bare phone-number JID.
// Re-export chat_state.normalizeJid here so the historical local references
// (whitelist comparison, read_only, debug_chats) keep working byte-identical.
const normalizeJid = chatState.normalizeJid;

// Returns true if the message was sent by the account owner.
// Primary check: m.key.fromMe (set by the WA server, unforgeable).
// Fallback: in group chats Baileys sometimes delivers the owner's own messages
// with fromMe=false (multi-device quirk). We fall back to comparing
// m.key.participant against our own JID/LID — still safe because the
// participant field is end-to-end authenticated by the Signal protocol.
function isOwnerMsg(m, s) {
  if (m.key.fromMe) return true;
  const participant = normalizeJid(m.key.participant);
  if (!participant) return false;
  const myId  = normalizeJid(s && s.user && s.user.id);
  const myLid = normalizeJid(s && s.user && s.user.lid);
  return Boolean((myId && participant === myId) || (myLid && participant === myLid));
}

// Read-only gate (Layer 16, Phase 2). Senders on `read_only` may sit in
// the chat but cannot trigger the bot. Whitelist beats read_only on
// collisions. First drop per (chat,user) is acked politely; subsequent
// drops are silent. Every drop is audited as bridge.read_only_drop.
const _roSeen = new Set();
let _auditEmit = null;
function _emitAuditEvent(eventType, opts) {
  if (_auditEmit === null) {
    try { _auditEmit = require('../shared/js/audit').auditEvent; }
    catch { _auditEmit = false; }
  }
  if (typeof _auditEmit !== 'function') return;
  try { _auditEmit(eventType, opts); } catch { /* fire-and-forget */ }
}
function readOnlyOk(sender, text, chatKey) {
  const cs = currentSettings();
  const wlNorm = (cs.whitelist || []).map(normalizeJid);
  if (wlNorm.length === 0) return { isReadOnly: false };
  const sNorm = normalizeJid(sender);
  if (wlNorm.includes(sNorm)) return { isReadOnly: false };
  if (chatKey) {
    try {
      const aud = require('../shared/js/in_chat_commands').getAudience(SETTINGS_FILE, String(chatKey));
      if (aud === 'all') return { isReadOnly: false };
    } catch {}
  }
  const roNorm = (cs.read_only || []).map(normalizeJid);
  if (!roNorm.includes(sNorm)) return { isReadOnly: false };
  const seenKey = `${chatKey || ''}::${sNorm}`;
  const firstDrop = !_roSeen.has(seenKey);
  if (firstDrop) _roSeen.add(seenKey);
  log(`auth: read_only drop sender=${sNorm} chat=${chatKey || ''} first=${firstDrop}`);
  const snippet = (text || '').toString().slice(0, 200);
  _emitAuditEvent('bridge.read_only_drop', {
    channel: CHANNEL, user: sNorm, chatKey: chatKey || '',
    details: {
      first_drop: firstDrop,
      snippet,
      truncated: (text || '').toString().length > 200,
    },
  });
  return { isReadOnly: true, firstDrop };
}

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

function authOk(sender, text, chatKey, fromMe) {
  // Owner's own messages always pass — fromMe=true means the message
  // came from the owner's own phone (WhatsApp's built-in attribution).
  if (fromMe) return true;

  const cs = currentSettings();
  const sNorm = normalizeJid(sender);

  // Per-chat audience override (/all on) opens the chat to everyone.
  // Checked before the whitelist so it works even with an empty whitelist.
  if (chatKey) {
    try {
      const aud = require('../shared/js/in_chat_commands').getAudience(SETTINGS_FILE, String(chatKey));
      if (aud === 'all') {
        log(`auth: audience=all in ${chatKey}, accepting ${sNorm}`);
        return true;
      }
    } catch {}
  }

  // Empty whitelist = fail-closed: deny everyone except the owner (handled
  // above) and chats explicitly opened via /all on (handled above).
  if (!cs.whitelist || cs.whitelist.length === 0) {
    log(`auth: no whitelist, denying ${sNorm}`);
    return false;
  }

  const wlNorm = cs.whitelist.map(normalizeJid);
  if (!wlNorm.includes(sNorm)) {
    if (cs.pin && text && text.trim().startsWith('/auth ')) {
      const given = text.trim().slice(6).trim();
      if (given === cs.pin) {
        settings.whitelist = (settings.whitelist || []).concat([sNorm]);
        fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2));
        log(`auth: PIN accepted, added ${sNorm} to whitelist`);
        return true;
      }
    }
    log(`auth: rejected sender=${sender} (normalized=${sNorm})`);
    return false;
  }
  return true;
}

function writeInbox(payload) {
  const id = newMsgId();
  const file = path.join(INBOX, `${id}.json`);
  fs.writeFileSync(file, JSON.stringify({ id, channel: CHANNEL, ...payload }, null, 2));
  const kind = payload.audio_path ? 'voice'
             : payload.image_path ? 'image'
             : payload.document_path ? 'document'
             : payload.video_path ? 'video'
             : 'text';
  log(`inbox: wrote ${id} from=${payload.from} type=${kind}`);

  // Local announce: ping/voice/notify on the host machine when a message
  // arrives. Best-effort, never blocks the daemon. Default: earcon.
  const mode = currentSettings().local_announce_inbound || 'off';
  const PLUGIN_ROOT = path.resolve(ROOT, '..');
  if (mode === 'earcon' || mode === 'voice' || mode === 'text') {
    try {
      // Build a short snippet for voice/text modes.
      const snippet = (payload.text || payload.caption || '').slice(0, 200) || `[${kind}]`;
      const fromShort = (payload.from || '').replace('@s.whatsapp.net', '').replace('@lid', '');
      if (mode === 'earcon') {
        spawn('python3', [path.join(PLUGIN_ROOT, 'scripts', 'earcon.py'), 'play', 'tool'], {
          detached: true, stdio: 'ignore',
        }).unref();
      } else if (mode === 'voice') {
        const intro = kind === 'text' ? `Neue message von ${fromShort}: ${snippet}` : `Neue ${kind}-message von ${fromShort}.${snippet ? ' ' + snippet : ''}`;
        spawn(path.join(PLUGIN_ROOT, 'scripts', 'speak.sh'), ['--lang', 'de', '--text', intro], {
          detached: true, stdio: 'ignore',
        }).unref();
      } else if (mode === 'text') {
        spawn('notify-send', ['-a', 'WhatsApp', `${fromShort}`, snippet || `[${kind}]`], {
          detached: true, stdio: 'ignore',
        }).unref();
      }
    } catch (e) {
      log(`local_announce_inbound failed: ${e.message}`);
    }
  }

  return id;
}

// Outbox watcher. Picks up files written by adapter.py, sends them via WA
// (or in mock mode, just logs and removes them). Each file has the shape:
//   { to: "phone@s.whatsapp.net", text: "...", voice_path?: "..." }
let waSocket = null;
async function processOutbox() {
  let files;
  try {
    files = fs.readdirSync(OUTBOX).filter(f => f.endsWith('.json')).sort();
  } catch { return; }
  for (const f of files) {
    const fpath = path.join(OUTBOX, f);
    let payload;
    try {
      payload = JSON.parse(fs.readFileSync(fpath, 'utf8'));
    } catch (e) {
      log(`outbox: bad JSON in ${f}: ${e.message}`);
      fs.unlinkSync(fpath);
      continue;
    }
    // Only handle our channel's files. Telegram/Discord daemons handle theirs.
    // Strict: missing `channel` is a writer bug, not a default-WhatsApp routing
    // hint. Drop+log so the file doesn't get silently delivered to a wrong account.
    if (!payload.channel) {
      log(`outbox: missing 'channel' field in ${f}, dropping`);
      try { fs.unlinkSync(fpath); } catch {}
      continue;
    }
    if (payload.channel !== CHANNEL) continue;
    // Layer-22 follow-up: if the target chat has been /off-disabled
    // between the time the adapter generated this reply and the
    // daemon picking it up, drop the send. Without this gate the
    // user sees the bridge replaying old replies long after /off
    // because in-flight subprocess turns keep producing outbox
    // envelopes that this loop happily delivers. Refusal here is
    // the structural fix; the adapter-side queue itself is
    // intentionally chat-agnostic and we don't want to teach it
    // about per-bridge enabled_chats state.
    if (payload.to && !isChatEnabled(payload.to)) {
      log(`outbox: dropping reply to disabled chat ${payload.to} (file=${f})`);
      try { fs.unlinkSync(fpath); } catch (e) {
        log(`outbox: unlink after disabled-chat drop failed: ${e.message}`);
      }
      continue;
    }
    // Stale-finalize gate — same race as the Discord/Telegram/Slack daemons:
    // the outbox dir is processed in alphabetical order, so `{msg_id}_00.json`
    // (real reply) can sort BEFORE `{msg_id}_hb.json` / `{msg_id}_sNN.json`
    // (heartbeat/progress). Once the real reply for a msg_id has been
    // delivered, drop every other file for that msg_id silently.
    if ((payload._progress || payload._heartbeat) && sticky.isFinalized(payload.msg_id)) {
      log(`outbox: drop stale ${payload._progress ? 'progress' : 'heartbeat'} for finalized ${payload.msg_id}`);
      try { fs.unlinkSync(fpath); } catch {}
      continue;
    }
    try {
      if (MOCK) {
        const kinds = [];
        if (payload.text) kinds.push('text');
        if (payload.voice_path) kinds.push('voice');
        if (payload.image_path) kinds.push('image');
        if (payload.document_path) kinds.push('doc');
        if (payload.video_path) kinds.push('video');
        if (payload._progress) kinds.push('progress');
        if (payload._heartbeat) kinds.push('heartbeat');
        log(`outbox MOCK: would send to=${payload.to} kinds=[${kinds.join(',')}] text="${(payload.text||'').slice(0,60)}"`);
        if (!payload._progress && !payload._heartbeat) sticky.markFinalized(payload.msg_id);
      } else {
        if (!waSocket) { log('outbox: socket not ready, retrying later'); return; }

        // Progress updates: edit a single sticky message instead of flooding
        // the chat with one message per tool call. On the first _progress
        // payload for a chat we send a new message and remember its Baileys
        // message key; every subsequent one edits it in place via the
        // WhatsApp message-edit feature (`edit: key`). When the real reply
        // arrives the sticky message is deleted first.
        if (payload._progress && payload.text) {
          const existing = sticky.getProgress(payload.to);
          if (existing) {
            try {
              await safeSend(waSocket, payload.to, { text: payload.text, edit: existing.key });
              fs.unlinkSync(fpath);
              continue;
            } catch (e) {
              log(`sticky edit failed, falling back to new message: ${e.message}`);
              try { await safeSend(waSocket, payload.to, { delete: existing.key }); } catch {}
              sticky.clearProgress(payload.to);
            }
          }
          const sent = await safeSend(waSocket, payload.to, { text: payload.text });
          if (sent && sent.key) sticky.setProgress(payload.to, { key: sent.key });
          fs.unlinkSync(fpath);
          continue;
        }

        // Heartbeat: slot into the sticky system so it gets deleted when the
        // real reply arrives. If a progress update already claimed the
        // sticky slot, skip silently — the progress message is more
        // informative anyway.
        if (payload._heartbeat && payload.text) {
          if (!sticky.hasProgress(payload.to)) {
            const sent = await safeSend(waSocket, payload.to, { text: payload.text });
            if (sent && sent.key) sticky.setProgress(payload.to, { key: sent.key });
          }
          fs.unlinkSync(fpath);
          continue;
        }

        // Real reply incoming — delete the sticky progress message first so
        // the chat shows the answer cleanly without a stale status line
        // above it.
        if (sticky.hasProgress(payload.to)) {
          const prog = sticky.getProgress(payload.to);
          try {
            await safeSend(waSocket, payload.to, { delete: prog.key });
          } catch (e) {
            // First delete attempt failed — retry once after a short pause,
            // same belt-and-braces approach as the Discord daemon.
            log(`sticky delete failed (will retry): ${e && e.message || e}`);
            await new Promise((r) => setTimeout(r, 400));
            try { await safeSend(waSocket, payload.to, { delete: prog.key }); } catch (e2) {
              log(`sticky delete retry also failed: ${e2 && e2.message || e2}`);
            }
          }
          sticky.clearProgress(payload.to);
        }
        // Mark this turn finalized BEFORE we touch WhatsApp so any racing
        // progress/heartbeat that arrives mid-send is correctly classified.
        sticky.markFinalized(payload.msg_id);

        // Send order: text first (header), then voice/image/doc/video.
        if (payload.text) {
          await safeSend(waSocket, payload.to, { text: payload.text });
        }
        if (payload.voice_path && fs.existsSync(payload.voice_path)) {
          await safeSend(waSocket, payload.to, {
            audio: fs.readFileSync(payload.voice_path),
            mimetype: 'audio/ogg; codecs=opus',
            ptt: true,
          });
        }
        if (payload.image_path && fs.existsSync(payload.image_path)) {
          // Try with thumbnail first; if Sharp chokes on the format, retry
          // with thumbnail explicitly disabled so the send still goes through.
          try {
            await safeSend(waSocket, payload.to, {
              image: fs.readFileSync(payload.image_path),
              caption: payload.image_caption || undefined,
            });
          } catch (e) {
            log(`image send with thumbnail failed (${e.message}), retrying without thumbnail`);
            await safeSend(waSocket, payload.to, {
              image: fs.readFileSync(payload.image_path),
              caption: payload.image_caption || undefined,
              jpegThumbnail: null,
            });
          }
        }
        if (payload.document_path && fs.existsSync(payload.document_path)) {
          await safeSend(waSocket, payload.to, {
            document: fs.readFileSync(payload.document_path),
            fileName: payload.document_name || path.basename(payload.document_path),
            mimetype: payload.document_mimetype || 'application/octet-stream',
            caption: payload.document_caption || undefined,
          });
        }
        if (payload.video_path && fs.existsSync(payload.video_path)) {
          await safeSend(waSocket, payload.to, {
            video: fs.readFileSync(payload.video_path),
            caption: payload.video_caption || undefined,
          });
        }
        // Clear typing indicator once the reply is on its way.
        try { await waSocket.sendPresenceUpdate('paused', payload.to); } catch {}
        activeSenders.delete(payload.to);

        // Flip the hourglass reaction on the user's last message to a ✅
        // once the real reply is delivered. Heartbeat envelopes don't count
        // as "real reply" — we keep the ⏳ until the actual answer ships.
        if (!payload._heartbeat && pendingReactions.has(payload.to)) {
          const userKey = pendingReactions.get(payload.to);
          try {
            await safeSend(waSocket, payload.to, { react: { text: '✅', key: userKey } });
          } catch (e) {
            // non-fatal
          }
          pendingReactions.delete(payload.to);
        }
      }
      fs.unlinkSync(fpath);
    } catch (e) {
      log(`outbox: send failed for ${f}: ${e.message}`);
    }
  }
}

// processOutbox is async — wrap each tick so an unhandled rejection inside
// it (e.g. Baileys' sharp-based thumbnail crashing on a malformed PNG) can
// not take the whole daemon down.
let outboxRunning = false;
setInterval(() => {
  if (outboxRunning) return; // skip overlapping ticks
  outboxRunning = true;
  Promise.resolve()
    .then(() => processOutbox())
    .catch(e => log(`processOutbox tick error: ${e.message}`))
    .finally(() => { outboxRunning = false; });
}, 500);

// Also catch any unhandledRejection so Baileys' internal failures (sharp,
// network blips, bad media) only affect that single message, not the daemon.
process.on('unhandledRejection', (reason) => {
  log(`unhandledRejection: ${reason && reason.message || reason}`);
});

// HTTP API. Used by mock mode for testing, and by /whatsapp-status in any mode.
const httpServer = http.createServer(async (req, res) => {
  // Browser-based pairing page: shows the current QR as a large image with
  // an auto-refresh so the user can scan from their screen.
  if (req.method === 'GET' && req.url === '/pair') {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    const paired = !!waSocket;
    const hasQR = !!lastQR;
    const ageSec = lastQR ? Math.floor((Date.now() - lastQRTimestamp) / 1000) : null;
    let body;
    if (paired) {
      body = `<h1 style="color:#25D366">Verbunden ✓</h1><p>JID: <code>${waSocket.user?.id || ''}</code></p><p>Du kannst dieses Fenster schließen.</p>`;
    } else if (!hasQR) {
      body = `<h1>Pairing</h1><p>Warte auf QR-Code vom WhatsApp-Server…</p><p style="opacity:0.6">Diese Seite loads sich automatically neu.</p>`;
    } else {
      body = `
        <h1>WhatsApp pairen</h1>
        <p style="font-size:1.1em">Auf dem Phone: WhatsApp → settings → Linked Devices → <b>device add</b> → diesen QR scannen.</p>
        <img src="/qr.png?ts=${lastQRTimestamp}" style="width:380px;height:380px;background:white;padding:16px;border-radius:8px"/>
        <p style="opacity:0.6;font-size:0.9em">QR-Alter: ${ageSec}s — refresht alle 5s</p>
      `;
    }
    res.end(`<!doctype html><html><head>
      <meta charset="utf-8"><meta http-equiv="refresh" content="5">
      <title>corvin-voice WA pairing</title>
      <style>body{font-family:system-ui,sans-serif;background:#0a0a0a;color:#eee;display:flex;flex-direction:column;align-items:center;padding:40px;text-align:center}h1{margin-top:0}code{background:#222;padding:2px 6px;border-radius:4px}</style>
      </head><body>${body}</body></html>`);
    return;
  }

  if (req.method === 'GET' && req.url.startsWith('/qr.png')) {
    if (!lastQR) { res.statusCode = 404; res.end('no qr available'); return; }
    try {
      const QRCode = require('qrcode');
      const buf = await QRCode.toBuffer(lastQR, { type: 'png', errorCorrectionLevel: 'M', margin: 2, scale: 10 });
      res.setHeader('Content-Type', 'image/png');
      res.setHeader('Cache-Control', 'no-store');
      res.end(buf);
    } catch (e) {
      res.statusCode = 500;
      res.end(`qr render failed: ${e.message}`);
    }
    return;
  }

  res.setHeader('Content-Type', 'application/json');
  if (req.method === 'GET' && req.url === '/status') {
    res.end(JSON.stringify({
      mock: MOCK,
      paired: !!waSocket,
      whitelist_size: (currentSettings().whitelist || []).length,
      enabled_chats: (currentSettings().enabled_chats || []).length,
      pending_outbox: fs.readdirSync(OUTBOX).filter(f => f.endsWith('.json')).length,
    }));
    return;
  }
  if (req.method === 'POST' && req.url === '/mock/inbound' && MOCK) {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const msg = JSON.parse(body);
        {
          const ro = readOnlyOk(msg.from, msg.text, msg.chat_id || msg.from);
          if (ro.isReadOnly) {
            const base = { from: msg.from, chat_id: msg.chat_id || msg.from, ts: Date.now() };
            // Layer-17 read-only consent / /share — mock path. Reply lands on
            // the HTTP response so tests can observe the dispatch outcome.
            const cc = inChatCmds.dispatchReadOnlyConsent({
              text: msg.text || '', channel: CHANNEL,
              chatKey: String(msg.chat_id || msg.from),
              uid: String(msg.from),
              settingsFile: SETTINGS_FILE,
            });
            if (cc) {
              if (cc.admitShare && cc.sharePayload) {
                try {
                  writeInbox({ ...base, _observer: true, _share: true,
                               text: String(cc.sharePayload).slice(0, 2000) });
                } catch (e) { log(`/share inbox-write failed: ${e.message}`); }
              }
              res.statusCode = cc.admitShare ? 202 : 200;
              res.end(JSON.stringify({
                consent: cc.kind,
                reply: cc.reply || null,
                shareAdmitted: !!cc.admitShare,
              }));
              return;
            }
            const forwarded = maybeForwardAsObserver(
              msg.from, msg.text, msg.chat_id || msg.from, base
            );
            res.statusCode = forwarded ? 202 : 403;
            res.end(JSON.stringify({
              error: forwarded ? null : 'read-only',
              firstDrop: ro.firstDrop,
              observerForwarded: forwarded,
            }));
            return;
          }
        }
        if (!authOk(msg.from, msg.text, msg.chat_id || msg.from)) {
          res.statusCode = 403;
          res.end(JSON.stringify({ error: 'not authorized' }));
          return;
        }
        if (!rateAllow(msg.from, currentSettings().rate_limit_per_hour)) {
          res.statusCode = 429;
          res.end(JSON.stringify({ error: 'rate limit' }));
          return;
        }
        // Phase 5.12: simulate media reject in mock-mode. The real daemon's
        // messages.upsert handler does the equivalent for actual WA messages.
        if (msg.media_type && !msg.text && !msg.audio_path) {
          // Drop a "rejection" message straight into the outbox so the test
          // can observe the same end-state as the real path.
          const rejId = newMsgId();
          const out = {
            to: msg.from,
            text: 'Currently I only process text and voice messages. Images, documents and stickers are not yet supported.',
          };
          fs.writeFileSync(path.join(OUTBOX, `${rejId}_00.json`), JSON.stringify(out));
          log(`media reject (mock): type=${msg.media_type} from=${msg.from}`);
          res.end(JSON.stringify({ ok: true, id: rejId, rejected: true }));
          return;
        }
        const id = writeInbox(msg);
        res.end(JSON.stringify({ ok: true, id }));
      } catch (e) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }
  res.statusCode = 404;
  res.end(JSON.stringify({ error: 'not found' }));
});

httpServer.listen(HTTP_PORT, '127.0.0.1', () => {
  log(`HTTP API listening on http://127.0.0.1:${HTTP_PORT}  mock=${MOCK}`);
});

if (MOCK) {
  log('Running in MOCK mode. POST /mock/inbound to inject messages.');
  log(`Inbox: ${INBOX}`);
  log(`Outbox: ${OUTBOX}`);
} else {
  // Real Baileys path. Loaded lazily so mock mode does not require the heavy dep.
  (async () => {
    let baileys, qrcode;
    try {
      // Baileys 6.x — CommonJS, sync require works.
      baileys = require('@whiskeysockets/baileys');
      qrcode = require('qrcode-terminal');
    } catch (e) {
      log('Baileys load failed. Run `npm install` in operator/voice/whatsapp/ first.');
      log(`Error: ${e.message}`);
      process.exit(1);
    }
    const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, Browsers } = baileys;
    const { state, saveCreds } = await useMultiFileAuthState(AUTH);
    // Fetch the actual WA Web version the server currently expects. Baileys'
    // bundled fallback version goes stale within weeks and produces a 405
    // "Connection Failure" during initial registration.
    let version;
    try {
      const fetched = await fetchLatestBaileysVersion();
      version = fetched.version;
      log(`Using WhatsApp Web version ${version.join('.')} (isLatest=${fetched.isLatest})`);
    } catch (e) {
      log(`fetchLatestBaileysVersion failed: ${e.message} — using bundled fallback`);
    }

    // makeSocket is called once initially and again on every "restartRequired"
    // (515) close event. Baileys emits 515 right after pairing is "configured
    // successfully" — the server expects the client to reconnect to finish
    // the registration handshake. If we exit instead of reconnecting, the
    // phone shows "device konnte nicht hinzugefügt werden".
    const makeSocket = () => makeWASocket({
      auth: state,
      version,
      browser: Browsers.ubuntu('Desktop'),
    });

    // Set ONCE at daemon start — never reset on reconnect.  This is the
    // reference point for the staleness guards in messages.upsert.  If this
    // were inside attachHandlers() it would reset on every reconnect, making
    // the 5-min toggle-guard relative to the last reconnect rather than the
    // daemon start — the root cause of repeated "AI on" replays.
    const sessionStartMs = Date.now();
    // Deduplication set: track message IDs of toggle commands already
    // processed this session.  Prevents Baileys from firing the same /on
    // twice when it delivers the same message in multiple reconnect batches.
    const seenToggleIds = new Set();

    let sock = makeSocket();
    attachHandlers(sock);
    return;

    // ─── helpers ──────────────────────────────────────────────────────────
    function attachHandlers(s) {

    // Pairing-Code mode: instead of waiting for the QR-event, ask WhatsApp
    // for an 8-digit code we can type into the phone. Much easier than QR
    // scanning, especially when the phone's camera or QR-scan flow is glitchy.
    if (PAIR_CODE_PHONE && !state.creds.registered) {
      // requestPairingCode needs ~2s after socket creation before it works.
      setTimeout(async () => {
        try {
          const code = await s.requestPairingCode(PAIR_CODE_PHONE);
          // Pretty-print: "ABCD-EFGH" with a hyphen in the middle.
          const pretty = code.length === 8 ? `${code.slice(0,4)}-${code.slice(4)}` : code;
          log('');
          log('  ┌─────────────────────────────────────────┐');
          log(`  │   Pairing code:    ${pretty.padEnd(20)} │`);
          log('  └─────────────────────────────────────────┘');
          log('');
          log('  Im Phone: WhatsApp → settings → Linked Devices');
          log('         → device add → "Mit Telefonnummer verknüpfen"');
          log(`         → Nummer ${PAIR_CODE_PHONE} → diesen Code eingeben`);
          log('');
        } catch (e) {
          log(`requestPairingCode failed: ${e.message}`);
          process.exit(1);
        }
      }, 2000);
    }
    s.ev.on('creds.update', () => {
      saveCreds();
      // Don't exit here — pairing isn't truly complete until after the
      // server-triggered restart (close reason 515) and a successful second
      // 'open'. The connection.update handler decides when to exit.
    });
    s.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
      if (qr) {
        lastQR = qr;
        lastQRTimestamp = Date.now();
        log('QR received. Open in browser: http://127.0.0.1:' + HTTP_PORT + '/pair');
        log('(or scan the terminal ASCII below)');
        qrcode.generate(qr, { small: true });
      }
      if (connection === 'open') {
        log(`Connected. JID=${s.user?.id}`);
        waSocket = s;
        // Pair-only success: only exit once registered=true. The first 'open'
        // (right after QR scan) typically has registered=false; Baileys then
        // emits close-515, we reconnect, and the SECOND 'open' has it true.
        if (PAIR_ONLY) {
          if (state.creds.registered) {
            log('Pairing complete (registered=true). Exiting.');
            setTimeout(() => process.exit(0), 1000);
          } else {
            log('Connected but not registered yet — waiting for post-pair sync...');
          }
        }
      }
      if (connection === 'close') {
        const reason = lastDisconnect?.error?.output?.statusCode;
        const reasonName = Object.entries(DisconnectReason || {}).find(([_, v]) => v === reason)?.[0] || 'unknown';
        log(`Connection closed (reason=${reason}/${reasonName}).`);
        waSocket = null;
        if (reason === DisconnectReason?.loggedOut) {
          log('Logged out — auth invalidated. Delete auth/ and pair again.');
          process.exit(0);
        }
        // 515 = restartRequired is the EXPECTED close right after a QR scan.
        // Reconnecting completes the registration. For all other transient
        // failures we so retry — only loggedOut (401) is terminal.
        log('Reconnecting in 1s...');
        setTimeout(() => {
          sock = makeSocket();
          attachHandlers(sock);
        }, 1000);
      }
    });
    s.ev.on('messages.upsert', async ({ messages, type }) => {
      // Baileys delivers two kinds of upsert batches:
      //   'notify'  — real-time inbound messages (process normally)
      //   'append'  — history-sync / offline-queue replay (skip commands)
      //
      // History-sync batches contain OLD messages that were sent before this
      // daemon session. Without this guard, a `/on` sent days ago gets
      // replayed on every reconnect and re-enables a chat the owner just
      // turned off with `/off`. We drop the entire non-notify batch rather
      // than trying to filter per-message, because the batch type is the
      // authoritative signal from the WA server.
      if (type !== 'notify') {
        log(`upsert: skipping ${messages.length} history-sync message(s) (type=${type})`);
        return;
      }

      for (const m of messages) {
        if (!m.message) {
          log(`upsert: skipping empty message from ${m.key.remoteJid} (type=${type})`);
          continue;
        }

        // Second line of defence: reject messages whose timestamp predates this
        // session start by more than 30 s. Baileys occasionally delivers
        // queued-while-offline messages as type='notify' even though they are
        // effectively stale. Toggle commands (/on /off) must never fire for
        // messages the owner sent in a previous session.
        const msgTs = (m.messageTimestamp || 0) * 1000; // WA uses seconds
        if (msgTs > 0 && msgTs < sessionStartMs - 30_000) {
          const ageS = Math.round((sessionStartMs - msgTs) / 1000);
          log(`upsert: skipping stale message (age=${ageS}s) from ${m.key.remoteJid}`);
          continue;
        }

        const sender = m.key.remoteJid;

        // Opportunistically register JID aliases (phone ↔ lid) from every
        // message that carries both forms. Once registered, disableChats can
        // find all forms via _expandAliases even when /off arrives under just
        // one form. Only persists when new aliases are actually discovered.
        {
          const _jids = chatState.chatJidsForMessage(m);
          if (_jids.length >= 2 && chatState.maybeRegisterAliases(_jids, currentSettings())) {
            saveSettings();
          }
        }

        // Unwrap ephemeral / view-once / device-sent envelopes that Baileys 7
        // wraps real content in. The actual chat message often sits one or
        // two levels deeper than m.message itself.
        let inner = m.message;
        while (inner) {
          if (inner.ephemeralMessage)        { inner = inner.ephemeralMessage.message; continue; }
          if (inner.viewOnceMessage)         { inner = inner.viewOnceMessage.message; continue; }
          if (inner.viewOnceMessageV2)       { inner = inner.viewOnceMessageV2.message; continue; }
          if (inner.viewOnceMessageV2Extension) { inner = inner.viewOnceMessageV2Extension.message; continue; }
          if (inner.deviceSentMessage)       { inner = inner.deviceSentMessage.message; continue; }
          if (inner.documentWithCaptionMessage) { inner = inner.documentWithCaptionMessage.message; continue; }
          if (inner.editedMessage)           { inner = inner.editedMessage.message; continue; }
          break;
        }
        const text = inner?.conversation || inner?.extendedTextMessage?.text || '';

        // Drop our own outbound replies that bounce back here with fromMe=true.
        // (Without this, enabling fromMe processing in 1:1 chats would loop.)
        if (m.key.fromMe && m.key.id && ownSentIds.has(m.key.id)) {
          continue;
        }

        // Owner-only chat-toggle commands. The `fromMe` flag is set by the
        // WhatsApp server when *we* sent the message (from any of our linked
        // devices), so it cannot be spoofed by a group member. /on, /off, /status
        // act on whichever chat the message was sent in.
        const cmd = (text || '').trim().toLowerCase();
        const isToggleCmd = cmd === '/on' || cmd === '/off' || cmd === '/status';
        // Note: /new /clear /reset are now owned by the in-chat dispatcher
        // (shared/js/in_chat_commands.js) so the layer-8 session-reset
        // (skills + forge + voice) all happens in one place.
        const isCancelCmd = cmd === '/stop' || cmd === '/cancel' || cmd === '/abbruch' || cmd === '/halt';
        const isDebugCmd = cmd === '/debug' || cmd === '/debug on' || cmd === '/debug off';
        if (isToggleCmd) {
          // Deduplication: same message ID can arrive in multiple reconnect
          // batches (Baileys occasionally re-delivers notify messages after a
          // socket replacement).  Process each unique message at most once.
          const toggleMsgId = m.key?.id;
          if (toggleMsgId && seenToggleIds.has(toggleMsgId)) {
            log(`upsert: skipping duplicate toggle "${cmd}" (id=${toggleMsgId})`);
            continue;
          }
          if (toggleMsgId) seenToggleIds.add(toggleMsgId);
          // Extra staleness guard for toggle commands: 5 minutes instead of
          // the global 30 s. Replayed /on from a prior session must never
          // clear disabled_chats that the owner set deliberately.
          // NOTE: sessionStartMs is set once at daemon start (outer scope) —
          // it does NOT reset on reconnect.
          if (msgTs > 0 && msgTs < sessionStartMs - 300_000) {
            const ageS = Math.round((sessionStartMs - msgTs) / 1000);
            log(`upsert: skipping stale toggle "${cmd}" (age=${ageS}s > 5 min)`);
            continue;
          }
          if (isOwnerMsg(m, s)) {
            try {
              if (cmd === '/on') {
                enableChatForMessage(m);
                const jids = chatState.chatJidsForMessage(m);
                log(`owner-cmd: enabled ${sender} (jids=${jids.join(',')})`);
                const ackSent = await safeSend(s, sender, { text: '✅ AI on for this chat.' });
                log(`owner-cmd: /on ack sent=${!!ackSent?.key?.id} to=${sender}`);
              } else if (cmd === '/off') {
                disableChatForMessage(m);
                const jids = chatState.chatJidsForMessage(m);
                log(`owner-cmd: disabled ${sender} (jids=${jids.join(',')})`);
                // Kill any in-flight Claude subprocess immediately. The adapter's
                // _cancel_chat path SIGTERMs the process group; without this the
                // subprocess keeps running, writes outbox files, and the daemon's
                // outbox gate (line 474) only drops them after Claude finishes —
                // which can be seconds later. Writing _cancel here stops it now.
                // The adapter's enabled_chats gate in process_one() will also drop
                // any queued inbox files for this chat that haven't started yet.
                writeInbox({ from: sender, _cancel: true, ts: Date.now() });
                const ackSent = await safeSend(s, sender, { text: '🔇 AI off for this chat.' });
                log(`owner-cmd: /off ack sent=${!!ackSent?.key?.id} to=${sender}`);
              } else {
                const on = isChatEnabledForMessage(m);
                await safeSend(s, sender, { text: on ? '✅ AI is on.' : '🔇 AI is off.' });
              }
            } catch (e) {
              log(`owner-cmd send failed: ${e.message}`);
            }
          } else {
            log(`ignored toggle-cmd "${cmd}" from non-owner ${sender}`);
          }
          continue;  // never forward toggle commands to Claude
        }
        if (isCancelCmd) {
          if (isOwnerMsg(m, s)) {
            log(`owner-cmd: cancel running task for ${sender}`);
            writeInbox({ from: sender, _cancel: true, ts: Date.now() });
          } else {
            log(`ignored cancel-cmd "${cmd}" from non-owner ${sender}`);
          }
          continue;  // adapter SIGTERMs the running subproc and writes ACK
        }
        // /btw <text> — Layer 13 mid-stream injection. Owner-only on WhatsApp
        // (fromMe), same gate as /cancel. The original-case text is matched
        // because the body after /btw must keep its capitalization.
        {
          const btwMatch = (text || '').match(/^\/btw(?:\s+([\s\S]+))?$/i);
          if (btwMatch) {
            if (isOwnerMsg(m, s)) {
              const btwText = (btwMatch[1] || '').trim();
              log(`owner-cmd: btw inject for ${sender} (len=${btwText.length})`);
              writeInbox({ from: sender, _btw: true, text: btwText, ts: Date.now() });
            } else {
              log(`ignored btw-cmd from non-owner ${sender}`);
            }
            continue;  // adapter injects + ACK
          }
        }
        if (isDebugCmd) {
          if (isOwnerMsg(m, s)) {
            try {
              let nowOn;
              if (cmd === '/debug on') {
                enableDebugChat(sender); nowOn = true;
              } else if (cmd === '/debug off') {
                disableDebugChat(sender); nowOn = false;
              } else {
                nowOn = !isDebugChat(sender);
                if (nowOn) enableDebugChat(sender); else disableDebugChat(sender);
              }
              log(`owner-cmd: debug ${nowOn ? 'on' : 'off'} for ${sender}`);
              await safeSend(s, sender, { text: nowOn
                ? 'Debug mode on. You see every tool call.'
                : 'Debug mode off. You only see the rough plan.' });
            } catch (e) {
              log(`debug-cmd send failed: ${e.message}`);
            }
          } else {
            log(`ignored debug-cmd "${cmd}" from non-owner ${sender}`);
          }
          continue;
        }

        // Cowork in-chat commands: /help, /personas, /persona <name>, /whoami, /skills.
        // Owner-only writes (persona switch); reads work for any whitelisted sender.
        const cwk = inChatCmds.dispatch({
          text, channel: CHANNEL, chatKey: sender,
          isOwner: isOwnerMsg(m, s), settingsFile: SETTINGS_FILE,
        });
        if (cwk) {
          try {
            await safeSend(s, sender, { text: cwk.reply });
            log(`in-chat-cmd ${cwk.kind} → ${sender}`);
            // Slash-commands skip the adapter's voice path because they're
            // answered directly here. For replies that opt in (kind=welcome
            // today, more later), synthesize a voice-note on the fly and
            // send it as a SEPARATE message immediately after the text —
            // never folded into the text reply.
            if (cwk.tts === true && cwk.reply) {
              const oggPath = await synthesizeVoiceNoteForText(cwk.reply, cwk.lang || 'de', 'shimmer');
              if (oggPath) {
                try {
                  await safeSend(s, sender, {
                    audio: fs.readFileSync(oggPath),
                    mimetype: 'audio/ogg; codecs=opus',
                    ptt: true,
                  });
                  log(`in-chat-cmd ${cwk.kind} voice → ${sender} (${path.basename(oggPath)})`);
                } catch (e) {
                  log(`in-chat-cmd voice send failed: ${e.message}`);
                }
              }
            }
          } catch (e) {
            log(`in-chat-cmd send failed: ${e.message}`);
          }
          continue;
        }

        // Default-off gate: only chats the owner explicitly enabled via /on
        // get forwarded to the adapter. Silent ignore otherwise. The check
        // honours BOTH `key.remoteJid` and `key.remoteJidAlt` so a chat
        // enabled under the lid form is still recognised when the next
        // message arrives under the phone form (and vice versa).
        if (!isChatEnabledForMessage(m)) {
          if (!isOwnerMsg(m, s)) log(`chat ${sender} not enabled, ignoring`);
          continue;
        }

        // In enabled chats — including groups — the owner's own messages
        // are forwarded to Claude. /on is the explicit opt-in: once a chat
        // is enabled, everything in it goes to the adapter (the owner's own
        // bot replies are filtered above via ownSentIds, so we don't loop).
        // To stop the bot in any chat, just send /off.
        const audioMsg = inner?.audioMessage;
        const imageMsg = inner?.imageMessage;
        const docMsg = inner?.documentMessage;
        const stickerMsg = inner?.stickerMessage;
        const kindLog = audioMsg ? 'voice' : imageMsg ? 'image' : docMsg ? 'doc' : stickerMsg ? 'sticker' : text ? 'text' : 'other';
        if (kindLog === 'other') {
          // Help the user / me debug schema drift: dump the top-level keys of
          // both the outer wrapper and (after unwrap) the inner message.
          const outerKeys = Object.keys(m.message || {}).join(',');
          const innerKeys = inner ? Object.keys(inner).join(',') : '<null>';
          log(`upsert: from=${sender} kind=other (outer_keys=[${outerKeys}] inner_keys=[${innerKeys}])`);
        } else {
          log(`upsert: from=${sender} kind=${kindLog} text="${text.slice(0,80)}"`);
        }
        {
          const ro = readOnlyOk(sender, text, sender);
          if (ro.isReadOnly) {
            const base = { from: sender, ts: Date.now() };
            // Layer-17 read-only consent / /share — real-Baileys path.
            const cc = inChatCmds.dispatchReadOnlyConsent({
              text, channel: CHANNEL, chatKey: sender, uid: sender,
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
                try { await safeSend(s, sender, { text: cc.reply }); } catch {}
              }
              log(`read-only consent: ${cc.kind} from=${sender}`);
              continue;
            }
            // Layer-19 — /join /pass for read-only senders.
            const dd = inChatCmds.dispatchReadOnlyDisclosure({
              text, channel: CHANNEL, chatKey: sender, uid: sender,
              settingsFile: SETTINGS_FILE,
            });
            if (dd) {
              if (dd.reply) {
                try { await safeSend(s, sender, { text: dd.reply }); } catch {}
              }
              log(`read-only disclosure: ${dd.kind} from=${sender}`);
              continue;
            }
            // Layer-21 — /propose <text> for read-only senders.
            const pp = inChatCmds.dispatchReadOnlyProposal({
              text, channel: CHANNEL, chatKey: sender, uid: sender,
              settingsFile: SETTINGS_FILE,
            });
            if (pp) {
              if (pp.reply) {
                try { await safeSend(s, sender, { text: pp.reply }); } catch {}
              }
              log(`read-only proposal: ${pp.kind} from=${sender}`);
              continue;
            }
            // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure for read-only
            // OBSERVERS too. Their message is forwarded to the LLM (observer transcript),
            // so they are interacting with the AI and must be told — not only reactively
            // via /join. Shown once per (chat, uid), same ledger as the owner path.
            if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: sender, uid: sender })) {
              const ocard = inChatCmds.disclosureCardText({
                channel: CHANNEL, ownerLabel: currentSettings().operator_name || '(owner)',
                hasObserverTranscript: true,
                lang: currentSettings().lang || 'en',
              });
              if (ocard) {
                try { await safeSend(s, sender, { text: ocard }); } catch {}
                const oseen = inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: sender, uid: sender, action: 'pending' });
                if (!oseen.ok) log(`[disclosure] observer mark_seen failed — ${oseen.error}`);
                log(`disclosure shown (observer) sender=${sender}`);
              }
            }
            const forwarded = maybeForwardAsObserver(sender, text, sender, base);
            if (forwarded) {
              // Silent forward — owner gets the transcript next turn.
            } else if (ro.firstDrop) {
              try { await safeSend(s, sender, { text: READ_ONLY_ACK }); } catch {}
            }
            continue;
          }
        }
        if (!authOk(sender, text, sender, m.key.fromMe)) {
          continue;
        }
        // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure on first encounter.
        // Shown once per sender JID.
        if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: sender, uid: sender })) {
          const card = inChatCmds.disclosureCardText({
            channel: CHANNEL, ownerLabel: '(owner)',
            hasObserverTranscript: false,
            lang: currentSettings().lang || 'en',
          });
          if (card) {
            try { await safeSend(s, sender, { text: card }); } catch {}
            inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: sender, uid: sender, action: 'pending' });
            log(`disclosure shown sender=${sender}`);
          }
        }
        if (!rateAllow(sender, currentSettings().rate_limit_per_hour)) {
          log(`rate: blocked ${sender}`);
          await safeSend(s, sender, { text: 'Rate limit reached. Please try again later.' });
          continue;
        }
        // Phase 5.14: handle every supported media type. Each branch downloads
        // the file and writes an inbox JSON with the appropriate <kind>_path.
        // adapter.py then dispatches per kind: voice→Whisper, image→Vision,
        // doc→text-extract, video→ffmpeg first-frame.
        const videoMsg = m.message.videoMessage;
        const caption = imageMsg?.caption || videoMsg?.caption || docMsg?.caption || '';
        const newId = newMsgId();
        if (audioMsg) {
          const buf = await baileys.downloadMediaMessage(m, 'buffer', {});
          const p = path.join(INBOX, `${newId}.ogg`);
          fs.writeFileSync(p, buf);
          writeInbox({ from: sender, audio_path: p, ts: Date.now() });
        } else if (imageMsg) {
          const buf = await baileys.downloadMediaMessage(m, 'buffer', {});
          const ext = (imageMsg.mimetype || 'image/jpeg').split('/')[1].split(';')[0] || 'jpg';
          const p = path.join(INBOX, `${newId}.${ext}`);
          fs.writeFileSync(p, buf);
          writeInbox({ from: sender, image_path: p, caption, ts: Date.now() });
        } else if (docMsg) {
          const buf = await baileys.downloadMediaMessage(m, 'buffer', {});
          const fname = docMsg.fileName || `${newId}.bin`;
          const safe = fname.replace(/[^a-zA-Z0-9._-]/g, '_');
          const p = path.join(INBOX, `${newId}_${safe}`);
          fs.writeFileSync(p, buf);
          writeInbox({ from: sender, document_path: p, document_name: fname, mimetype: docMsg.mimetype, caption, ts: Date.now() });
        } else if (videoMsg) {
          const buf = await baileys.downloadMediaMessage(m, 'buffer', {});
          const p = path.join(INBOX, `${newId}.mp4`);
          fs.writeFileSync(p, buf);
          writeInbox({ from: sender, video_path: p, caption, ts: Date.now() });
        } else if (stickerMsg) {
          // Sticker = small image. Treat as image so Vision can describe it.
          const buf = await baileys.downloadMediaMessage(m, 'buffer', {});
          const p = path.join(INBOX, `${newId}.webp`);
          fs.writeFileSync(p, buf);
          writeInbox({ from: sender, image_path: p, is_sticker: true, ts: Date.now() });
        } else if (text) {
          writeInbox({ from: sender, text, ts: Date.now() });
        } else {
          continue;  // unknown shape, skip
        }
        // Phase 5.10: typing indicator. Tells the WA user "Claude is responding"
        // even before the adapter has finished. We mark this sender as "active"
        // and refresh the indicator periodically; processOutbox() clears it
        // when it sends the actual response.
        try {
          await s.sendPresenceUpdate('composing', sender);
        } catch (e) {
          // non-fatal
        }
        activeSenders.set(sender, Date.now());

        // Hourglass reaction on the user's own message → instant visual ack
        // ("got it, working on it") without spamming a separate text message.
        // Flipped to ✅ in processOutbox() once the real reply goes out.
        try {
          await safeSend(s, sender, { react: { text: '⏳', key: m.key } });
          pendingReactions.set(sender, m.key);
        } catch (e) {
          // non-fatal — group settings or older WA clients may reject reactions
        }
      }
    });
    } // end attachHandlers

    // Refresh typing indicator every 8s for senders we're still working on.
    // Uses waSocket (the latest socket) so it survives reconnects.
    setInterval(async () => {
      if (!waSocket) return;
      const now = Date.now();
      for (const [sender, ts] of activeSenders.entries()) {
        if (now - ts > 60000) {
          activeSenders.delete(sender);
          continue;
        }
        try { await waSocket.sendPresenceUpdate('composing', sender); } catch {}
      }
    }, 8000);
  })().catch(e => { log('fatal:', e); process.exit(1); });
}

process.on('SIGINT', () => { log('shutting down'); process.exit(0); });
process.on('SIGTERM', () => { log('shutting down'); process.exit(0); });
