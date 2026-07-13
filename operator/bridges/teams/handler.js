// handler.js — Bot Framework TurnContext → inbox/outbox logic.
//
// Extracted from daemon.js so tests can call handleActivity() and sendTeams()
// directly without starting an HTTP server or connecting to Azure.
//
// Consumers must inject all collaborators (auth, logger, paths, refs) so this
// module has no global state and is fully testable.

'use strict';

const fs   = require('fs');
const path = require('path');

const { newMsgId }   = require('../shared/js/msg-id');
const inChatCmds     = require('../shared/js/in_chat_commands');
const chatToggle      = require('../shared/js/chat_toggle');
const cards          = require('./cards');
const { makeStickyProgress } = require('../shared/js/sticky_progress');

const READ_ONLY_ACK =
  '🔒 You are read-only in this chat — you can follow along, but you ' +
  'cannot drive the bot. Ask the owner to add you to the whitelist.';

const AUTH_DENY_TPL = (email) =>
  `You are not authorized. Your identity: \`${email}\`\n` +
  `Add it to settings.json → whitelist (or send "/auth <pin>").\n` +
  `The owner can also open this chat with /all on.`;

/**
 * makeHandler — factory that wires collaborators into the handler functions.
 *
 * @param {object} cfg
 * @param {string}   cfg.inboxDir        — absolute path to shared/inbox/
 * @param {string}   cfg.settingsFile    — absolute path to settings.json
 * @param {function} cfg.currentSettings — hot-reload accessor
 * @param {object}   cfg.auth            — { authOk, readOnlyOk, rateAllow }
 * @param {function} cfg.logger          — log(msg) function
 * @param {Map}      cfg.conversationRefs — chatKey → ConversationReference
 */
function makeHandler({ inboxDir, settingsFile, currentSettings, auth, logger, conversationRefs }) {
  const log = logger || (() => {});
  const { rateAllow, authOk, readOnlyOk } = auth;

  // Sticky-progress + finalize-guard state for _progress/_heartbeat outbox
  // payloads. Mirrors the Discord/Telegram/Slack/WhatsApp/Signal daemons
  // (shared/js/sticky_progress.js): the first _progress payload for a turn
  // sends a new activity and remembers its activity id; every subsequent
  // one edits it in place via TurnContext.updateActivity() instead of
  // flooding the chat with one message per tool call. Once the real reply
  // lands, any stale _progress/_heartbeat file for the same msg_id (outbox
  // alphabetical-sort race) is dropped silently.
  const sticky = makeStickyProgress({ ttlMs: 60_000 });

  function writeInbox(payload) {
    const id = newMsgId();
    fs.writeFileSync(
      path.join(inboxDir, `${id}.json`),
      JSON.stringify({ id, channel: 'teams', ...payload }, null, 2),
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
   * handleActivity — process one inbound Teams TurnContext.
   *
   * @param {object} ctx  — real TurnContext or mock with { activity, sendActivity }
   * @returns {Promise<string|null>}  — inbox message id, or null if dropped
   */
  async function handleActivity(ctx) {
    const activity = ctx.activity;
    if (!activity || activity.type !== 'message') return null;

    // Teams delivers UPN (user@company.com) or AAD Object ID.
    // We prefer UPN for whitelist legibility; fall back to aadObjectId.
    const email   = activity.from?.userPrincipalName
                  || activity.from?.aadObjectId
                  || activity.from?.id
                  || 'unknown';
    const chatKey = activity.conversation?.id || 'unknown';
    const text    = (activity.text || '').trim();
    const base    = { from: String(email), chat_id: chatKey, ts: Date.now() };

    // ── Read-only gate ────────────────────────────────────────────────────
    {
      const ro = readOnlyOk(email, text, chatKey);
      if (ro.isReadOnly) {
        const cc = inChatCmds.dispatchReadOnlyConsent({
          text, channel: 'teams', chatKey: String(chatKey), uid: String(email),
          settingsFile,
        });
        if (cc) {
          if (cc.admitShare && cc.sharePayload) {
            try {
              writeInbox({ ...base, _observer: true, _share: true,
                           text: String(cc.sharePayload).slice(0, 2000) });
            } catch (e) { log(`/share inbox-write failed: ${e.message}`); }
          }
          if (cc.reply) {
            try { await ctx.sendActivity(cc.reply); } catch {}
          }
          return null;
        }
        const dd = inChatCmds.dispatchReadOnlyDisclosure({
          text, channel: 'teams', chatKey: String(chatKey), uid: String(email),
          settingsFile,
        });
        if (dd) {
          if (dd.reply) try { await ctx.sendActivity(dd.reply); } catch {}
          return null;
        }
        const pp = inChatCmds.dispatchReadOnlyProposal({
          text, channel: 'teams', chatKey: String(chatKey), uid: String(email),
          settingsFile,
        });
        if (pp) {
          if (pp.reply) try { await ctx.sendActivity(pp.reply); } catch {}
          return null;
        }
        // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure for read-only
        // OBSERVERS too. Their message is forwarded to the LLM (observer transcript),
        // so they are interacting with the AI and must be told — not only reactively
        // via /join. Shown once per (chat, uid), same ledger as the owner path.
        if (!inChatCmds.disclosureHasSeen({ channel: 'teams', chatKey: String(chatKey), uid: email })) {
          const ocard = inChatCmds.disclosureCardText({
            channel: 'teams', ownerLabel: (currentSettings && currentSettings().operator_name) || '(owner)',
            hasObserverTranscript: true,
            lang: (currentSettings && currentSettings().lang) || 'en',
          });
          if (ocard) {
            try { await ctx.sendActivity(ocard); } catch {}
            const oseen = inChatCmds.disclosureMarkSeen({ channel: 'teams', chatKey: String(chatKey), uid: email, action: 'pending' });
            if (!oseen.ok) log(`[disclosure] observer mark_seen failed — ${oseen.error}`);
            log(`disclosure shown (observer) email=${email} chat=${chatKey}`);
          }
        }
        const forwarded = maybeForwardAsObserver(email, text, chatKey, base);
        if (!forwarded && ro.firstDrop) {
          try { await ctx.sendActivity(READ_ONLY_ACK); } catch {}
        }
        return null;
      }
    }

    // ── Auth gate ─────────────────────────────────────────────────────────
    if (!authOk(email, text, chatKey)) {
      try { await ctx.sendActivity(AUTH_DENY_TPL(email)); } catch {}
      return null;
    }
    // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure on first encounter.
    // Shown once per (chat, uid/email).
    if (!inChatCmds.disclosureHasSeen({ channel: 'teams', chatKey: String(chatKey), uid: email })) {
      const card = inChatCmds.disclosureCardText({
        channel: 'teams', ownerLabel: '(owner)',
        hasObserverTranscript: false,
        lang: (currentSettings && currentSettings().lang) || 'en',
      });
      if (card) {
        try { await ctx.sendActivity(card); } catch {}
        inChatCmds.disclosureMarkSeen({ channel: 'teams', chatKey: String(chatKey), uid: email, action: 'pending' });
        log(`disclosure shown email=${email} chat=${chatKey}`);
      }
    }
    if (!rateAllow(email, currentSettings().rate_limit_per_hour || 60)) {
      try { await ctx.sendActivity('Rate limit reached. Please try again later.'); } catch {}
      return null;
    }

    // ── Toggle commands (/on /off /status) ────────────────────────────────
    {
      const tog = chatToggle.handleToggleCommand({
        text, chatKey: String(chatKey), isOwner: true, settingsFile,
      });
      if (tog) {
        try { await ctx.sendActivity(tog.reply); } catch {}
        return null;
      }
    }
    if (!chatToggle.isChatEnabled(currentSettings(), String(chatKey))) {
      log(`chat ${chatKey} not enabled, ignoring`);
      return null;
    }

    // ── In-chat commands (/new, /consent, cowork, etc.) ──────────────────
    const cmdLower = text.toLowerCase();
    if (cmdLower === '/stop' || cmdLower === '/cancel' || cmdLower === '/abbruch' || cmdLower === '/halt') {
      log(`cancel cmd from ${email} in chat ${chatKey}`);
      writeInbox({ ...base, _cancel: true });
      return null;
    }
    {
      const btwMatch = text.match(/^\/btw(?:\s+([\s\S]+))?$/i);
      if (btwMatch) {
        const btwText = (btwMatch[1] || '').trim();
        writeInbox({ ...base, _btw: true, text: btwText });
        return null;
      }
    }
    {
      const cwk = inChatCmds.dispatch({
        text, channel: 'teams', chatKey: String(chatKey),
        isOwner: true, settingsFile,
      });
      if (cwk) {
        try { await ctx.sendActivity(cwk.reply); } catch {}
        return null;
      }
    }

    if (!text) return null;
    return writeInbox({ ...base, text });
  }

  /**
   * sendTeams — deliver one outbox payload as an Adaptive Card.
   *
   * @param {object} payload      — outbox JSON (text, voice_path, etc.)
   * @param {object} adapter      — CloudAdapter or mock with continueConversation()
   * @param {string} teamsAppId   — Microsoft App ID (for proactive send auth)
   * @returns {Promise<boolean>}
   */
  async function sendTeams(payload, adapter, teamsAppId) {
    const chatKey = payload.chat_id;
    if (!chatKey) { log('outbox: missing chat_id, dropping'); return false; }

    const ref = conversationRefs.get(chatKey);
    if (!ref) {
      log(`outbox: no conversationRef for ${chatKey}, dropping`);
      return false;
    }

    // Stale-finalize gate — same race as the other daemons: the outbox dir
    // is processed in alphabetical order, so `{msg_id}_00.json` (real
    // reply) can sort BEFORE `{msg_id}_hb.json` / `{msg_id}_sNN.json`
    // (heartbeat/progress). Once the real reply for a msg_id has been
    // delivered, drop every other file for that msg_id silently.
    const msgId = payload.msg_id;
    if ((payload._progress || payload._heartbeat) && sticky.isFinalized(msgId)) {
      log(`outbox: drop stale ${payload._progress ? 'progress' : 'heartbeat'} for finalized ${msgId}`);
      return true;
    }

    try {
      await adapter.continueConversation(ref, teamsAppId, async (ctx) => {
        // Progress updates: edit a single sticky activity instead of
        // flooding the chat with one message per tool call. On the first
        // _progress payload we send a new activity and remember its id;
        // every subsequent one edits it in place via updateActivity().
        if (payload._progress && payload.text) {
          const existing = sticky.getProgress(chatKey);
          if (existing) {
            try {
              await ctx.updateActivity({ ...cards.fromText(payload.text), id: existing.activityId });
              return;
            } catch (e) {
              log(`sticky update failed, falling back to new activity: ${e && e.message || e}`);
              try { await ctx.deleteActivity(existing.activityId); } catch {}
              sticky.clearProgress(chatKey);
            }
          }
          const res = await ctx.sendActivity(cards.fromText(payload.text));
          if (res && res.id) sticky.setProgress(chatKey, { activityId: res.id });
          return;
        }

        // Heartbeat: slot into the sticky system so it gets deleted when
        // the real reply arrives. If a progress update already claimed the
        // sticky slot, skip silently — the progress message is more
        // informative anyway.
        if (payload._heartbeat && payload.text) {
          if (!sticky.hasProgress(chatKey)) {
            const res = await ctx.sendActivity(cards.fromText(payload.text));
            if (res && res.id) sticky.setProgress(chatKey, { activityId: res.id });
          }
          return;
        }

        // Real reply incoming — delete the sticky progress activity first
        // so the chat shows the answer cleanly without a stale status line
        // above it.
        if (sticky.hasProgress(chatKey)) {
          const prog = sticky.getProgress(chatKey);
          try {
            await ctx.deleteActivity(prog.activityId);
          } catch (e) {
            log(`sticky delete failed: ${e && e.message || e}`);
          }
          sticky.clearProgress(chatKey);
        }
        // Mark this turn finalized BEFORE we touch Teams so any racing
        // progress/heartbeat that arrives mid-send is correctly classified.
        sticky.markFinalized(msgId);

        if (payload.text) {
          const card = cards.fromText(payload.text);
          await ctx.sendActivity(card);
        }
        if (payload.voice_path && fs.existsSync(payload.voice_path)) {
          // Teams does not support inline voice notes; send a text note.
          await ctx.sendActivity(cards.plainCard('🔊 _(Voice note not supported in Teams)_'));
        }
        if (payload.image_path && fs.existsSync(payload.image_path)) {
          // For images, fall back to a plain notification; file sharing requires
          // the Microsoft Graph Files.ReadWrite permission which is optional.
          await ctx.sendActivity(cards.plainCard(`📎 Image: ${path.basename(payload.image_path)}`));
        }
        if (payload.document_path && fs.existsSync(payload.document_path)) {
          await ctx.sendActivity(cards.plainCard(`📎 File: ${payload.document_name || path.basename(payload.document_path)}`));
        }
      });
      return true;
    } catch (e) {
      log(`outbox: send failed for ${chatKey}: ${e.message}`);
      throw e;
    }
  }

  return { handleActivity, sendTeams, writeInbox };
}

module.exports = { makeHandler };
