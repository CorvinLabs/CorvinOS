// handler.js — signal-cli REST API ↔ inbox/outbox logic.
//
// Extracted from daemon.js for testability: all I/O is injected,
// no global state, no network calls. Tests pass mock fetch + mock fs.
//
// Signal identity: phone numbers (+49123…). Used as both sender id and
// whitelist key — E.164 format, normalized on every comparison.
//
// signal-cli REST API:
//   GET  /v1/receive/<phone>         → array of envelope objects
//   POST /v2/send                    → { message, number, recipients }
//   (self-hosted: https://github.com/bbernhard/signal-cli-rest-api)

'use strict';

const fs   = require('fs');
const path = require('path');

const { newMsgId }   = require('../shared/js/msg-id');
const inChatCmds     = require('../shared/js/in_chat_commands');
const chatToggle     = require('../shared/js/chat_toggle');

const READ_ONLY_ACK =
  '🔒 You are read-only in this chat — you can follow along, but you ' +
  'cannot drive the bot. Ask the owner to add you to the whitelist.';

// Normalise E.164: strip spaces/dashes, ensure leading +
function normPhone(num) {
  if (!num) return '';
  const s = String(num).replace(/[\s\-().]/g, '');
  return s.startsWith('+') ? s : `+${s}`;
}

/**
 * makeHandler — factory that wires collaborators in.
 *
 * @param {object} cfg
 * @param {string}   cfg.inboxDir
 * @param {string}   cfg.settingsFile
 * @param {function} cfg.currentSettings
 * @param {object}   cfg.auth              — { authOk, readOnlyOk, rateAllow }
 * @param {function} cfg.logger
 * @param {function} cfg.sendSignal        — async (recipient, message) => void
 *                                           injected so tests can mock it
 */
function makeHandler({ inboxDir, settingsFile, currentSettings, auth, logger, sendSignal }) {
  const log = logger || (() => {});
  const { rateAllow, authOk, readOnlyOk } = auth;

  function writeInbox(payload) {
    const id = newMsgId();
    fs.writeFileSync(
      path.join(inboxDir, `${id}.json`),
      JSON.stringify({ id, channel: 'signal', ...payload }, null, 2),
    );
    log(`inbox: ${id} from=${payload.from}`);
    return id;
  }

  function maybeForwardAsObserver(uid, text, chatKey, base) {
    if (!text || !String(text).trim()) return false;
    let mode = 'off';
    try { mode = inChatCmds.getObserverVisibility(settingsFile, String(chatKey)) || 'off'; }
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

  /**
   * handleEnvelope — process one signal-cli envelope object.
   *
   * signal-cli REST API delivers envelopes in this shape:
   * {
   *   envelope: {
   *     source: "+491234567890",
   *     sourceDevice: 1,
   *     dataMessage: { message: "Hello", timestamp: 1700000000000 }
   *   }
   * }
   *
   * @returns {Promise<string|null>}  — inbox id or null if dropped
   */
  async function handleEnvelope(envelope) {
    const inner = envelope.envelope || envelope;
    const dataMsg = inner.dataMessage;
    // Drop non-data messages (receipts, typing, call offers, etc.)
    if (!dataMsg) return null;

    const rawSender = inner.source || inner.sourceNumber || '';
    const sender    = normPhone(rawSender);
    const text      = (dataMsg.message || '').trim();
    // Signal has no chat groups in scope for v0.1 — use sender as chat key.
    // Group chats: inner.dataMessage.groupInfo.groupId could be used later.
    const isGroup   = !!(dataMsg.groupInfo);
    const chatKey   = isGroup
      ? `group:${dataMsg.groupInfo.groupId}`
      : sender;

    const base = { from: sender, chat_id: chatKey, ts: dataMsg.timestamp || Date.now() };

    // ── Read-only gate ────────────────────────────────────────────────────
    {
      const ro = readOnlyOk(sender, text, chatKey);
      if (ro.isReadOnly) {
        const cc = inChatCmds.dispatchReadOnlyConsent({
          text, channel: 'signal', chatKey: String(chatKey), uid: sender, settingsFile,
        });
        if (cc) {
          if (cc.admitShare && cc.sharePayload)
            try { writeInbox({ ...base, _observer: true, _share: true, text: String(cc.sharePayload).slice(0, 2000) }); } catch {}
          if (cc.reply) try { await sendSignal(sender, cc.reply); } catch {}
          return null;
        }
        const dd = inChatCmds.dispatchReadOnlyDisclosure({ text, channel: 'signal', chatKey: String(chatKey), uid: sender, settingsFile });
        if (dd) {
          if (dd.reply) try { await sendSignal(sender, dd.reply); } catch {}
          return null;
        }
        const pp = inChatCmds.dispatchReadOnlyProposal({ text, channel: 'signal', chatKey: String(chatKey), uid: sender, settingsFile });
        if (pp) {
          if (pp.reply) try { await sendSignal(sender, pp.reply); } catch {}
          return null;
        }
        // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure for read-only
        // OBSERVERS too. Their message is forwarded to the LLM (observer transcript),
        // so they are interacting with the AI and must be told — not only reactively
        // via /join. Shown once per (chat, uid), same ledger as the owner path.
        if (!inChatCmds.disclosureHasSeen({ channel: 'signal', chatKey: String(chatKey), uid: sender })) {
          const ocard = inChatCmds.disclosureCardText({
            channel: 'signal', ownerLabel: (currentSettings && currentSettings().operator_name) || '(owner)',
            hasObserverTranscript: true,
            lang: (currentSettings && currentSettings().lang) || 'en',
          });
          if (ocard) {
            try { await sendSignal(sender, ocard); } catch {}
            const oseen = inChatCmds.disclosureMarkSeen({ channel: 'signal', chatKey: String(chatKey), uid: sender, action: 'pending' });
            if (!oseen.ok) log(`[disclosure] observer mark_seen failed — ${oseen.error}`);
            log(`disclosure shown (observer) sender=${sender}`);
          }
        }
        const forwarded = maybeForwardAsObserver(sender, text, chatKey, base);
        if (!forwarded && ro.firstDrop)
          try { await sendSignal(sender, READ_ONLY_ACK); } catch {}
        return null;
      }
    }

    // ── Auth gate ─────────────────────────────────────────────────────────
    if (!authOk(sender, text, chatKey)) {
      try {
        await sendSignal(sender,
          `You are not authorized. Your number: ${sender}\n` +
          `Add it to settings.json → whitelist (or send "/auth <pin>").`);
      } catch {}
      return null;
    }
    // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure on first encounter.
    // Shown once per (chat, uid).
    if (!inChatCmds.disclosureHasSeen({ channel: 'signal', chatKey: String(chatKey), uid: sender })) {
      const card = inChatCmds.disclosureCardText({
        channel: 'signal', ownerLabel: '(owner)',
        hasObserverTranscript: false,
        lang: (currentSettings && currentSettings().lang) || 'en',
      });
      if (card) {
        try { await sendSignal(sender, card); } catch {}
        inChatCmds.disclosureMarkSeen({ channel: 'signal', chatKey: String(chatKey), uid: sender, action: 'pending' });
        log(`disclosure shown sender=${sender}`);
      }
    }
    if (!rateAllow(sender, currentSettings().rate_limit_per_hour || 60)) {
      try { await sendSignal(sender, 'Rate limit reached. Please try again later.'); } catch {}
      return null;
    }

    // ── Toggle commands ───────────────────────────────────────────────────
    {
      const tog = chatToggle.handleToggleCommand({ text, chatKey: String(chatKey), isOwner: true, settingsFile });
      if (tog) {
        try { await sendSignal(sender, tog.reply); } catch {}
        return null;
      }
    }
    if (!chatToggle.isChatEnabled(currentSettings(), String(chatKey))) {
      log(`chat ${chatKey} not enabled, ignoring`);
      return null;
    }

    // ── Special commands ──────────────────────────────────────────────────
    const cmdLower = text.toLowerCase();
    if (cmdLower === '/stop' || cmdLower === '/cancel' || cmdLower === '/abbruch' || cmdLower === '/halt') {
      writeInbox({ ...base, _cancel: true });
      return null;
    }
    {
      const btwMatch = text.match(/^\/btw(?:\s+([\s\S]+))?$/i);
      if (btwMatch) {
        writeInbox({ ...base, _btw: true, text: (btwMatch[1] || '').trim() });
        return null;
      }
    }
    {
      const cwk = inChatCmds.dispatch({ text, channel: 'signal', chatKey: String(chatKey), isOwner: true, settingsFile });
      if (cwk) {
        try { await sendSignal(sender, cwk.reply); } catch {}
        return null;
      }
    }

    if (!text) return null;
    return writeInbox({ ...base, text });
  }

  /**
   * processOutboxPayload — send one outbox item via Signal.
   * Signal is plain text only — no rich cards.
   * Attachments get a best-effort text notice.
   *
   * @returns {Promise<boolean>}
   */
  async function processOutboxPayload(payload) {
    const recipient = payload.chat_id;
    if (!recipient) { log('outbox: missing chat_id, dropping'); return false; }
    try {
      if (payload.text) await sendSignal(recipient, payload.text);
      if (payload.voice_path && fs.existsSync(payload.voice_path))
        await sendSignal(recipient, '🔊 _(Voice note not supported in Signal via REST API)_');
      if (payload.image_path && fs.existsSync(payload.image_path))
        await sendSignal(recipient, `📎 Image: ${path.basename(payload.image_path)}`);
      if (payload.document_path && fs.existsSync(payload.document_path))
        await sendSignal(recipient, `📎 File: ${payload.document_name || path.basename(payload.document_path)}`);
      return true;
    } catch (e) {
      log(`outbox send failed for ${recipient}: ${e.message}`);
      throw e;
    }
  }

  return { handleEnvelope, processOutboxPayload, writeInbox, normPhone };
}

module.exports = { makeHandler, normPhone };
