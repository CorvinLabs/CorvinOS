// chat_toggle.js — shared per-chat /on /off gate for telegram/discord/slack.
//
// WhatsApp has its own variant in `whatsapp/chat_state.js` because the
// platform delivers a chat under two parallel JID forms (s.whatsapp.net /
// lid). The other three bridges have stable single-form chat-ids, so a
// simple list lookup suffices.
//
// Two-list model:
//   * `enabled_chats` — opt-in allow-list. Presence flips the bot into
//     opt-in mode (only chats whose id is in the list reach the adapter).
//   * `disabled_chats` — HARD deny-list. Always wins, regardless of mode.
//     A chat in this list is silently dropped even when `enabled_chats`
//     is absent (default-on mode) or when the chat is also listed there.
//
// Why two lists: `/off` must be a real deny. Before this split, calling
// `/off` in default-on mode (no `enabled_chats` field) was a silent no-op
// because adding the chat to a missing list does nothing, and creating
// `enabled_chats: []` would have collaterally turned every OTHER chat
// off too. The hard deny-list solves both: `/off` is always effective,
// and other chats stay on.
//
// Backwards-compat:
//   * settings.json without either field → default-on (legacy behaviour).
//   * settings.json with `enabled_chats: [...]` → opt-in mode, plus the
//     hard deny-list still applies on top.
//
// `enableChat` / `disableChat` mutate the on-disk settings.json atomically
// (write-then-rename) and are idempotent. `/on` removes the chat from
// `disabled_chats`; `/off` adds it to `disabled_chats`. Each command also
// touches `enabled_chats` when that field already exists, so opt-in
// setups stay coherent — but neither command introduces `enabled_chats`
// when it's absent, preserving the default-on contract for other chats.
//
// Public API:
//   isToggleEnabled(settings)        — true iff settings.enabled_chats is an array
//   isChatEnabled(settings, chatKey) — false if chatKey in disabled_chats;
//                                      else true iff toggle off OR chatKey in enabled_chats
//   enableChat(settingsFile, chatKey)
//   disableChat(settingsFile, chatKey)
//   handleToggleCommand({ text, chatKey, isOwner, settingsFile })
//     → { kind, reply } | null

const fs = require('fs');

function _read(settingsFile) {
  try {
    return JSON.parse(fs.readFileSync(settingsFile, 'utf8'));
  } catch {
    return {};
  }
}

function _write(settingsFile, data) {
  const tmp = `${settingsFile}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
  fs.renameSync(tmp, settingsFile);
}

function isToggleEnabled(settings) {
  return Array.isArray(settings && settings.enabled_chats);
}

function _isHardDisabled(settings, key) {
  if (!Array.isArray(settings && settings.disabled_chats)) return false;
  return settings.disabled_chats.map(String).includes(String(key || ''));
}

function isChatEnabled(settings, chatKey) {
  const key = String(chatKey || '');
  if (_isHardDisabled(settings, key)) return false;
  if (!isToggleEnabled(settings)) return true;  // legacy default-on
  return settings.enabled_chats.map(String).includes(key);
}

function enableChat(settingsFile, chatKey) {
  const key = String(chatKey || '');
  const data = _read(settingsFile);
  let dirty = false;
  if (Array.isArray(data.disabled_chats)) {
    const before = data.disabled_chats.length;
    data.disabled_chats = data.disabled_chats.filter((k) => String(k) !== key);
    if (data.disabled_chats.length !== before) dirty = true;
    if (data.disabled_chats.length === 0) {
      delete data.disabled_chats;
      dirty = true;
    }
  }
  // Only touch enabled_chats if the operator already opted into the
  // allow-list. Introducing the field here would flip every OTHER chat
  // from default-on to silently-off — exactly the blast radius the
  // disabled_chats path was designed to avoid.
  if (Array.isArray(data.enabled_chats)) {
    if (!data.enabled_chats.map(String).includes(key)) {
      data.enabled_chats.push(key);
      dirty = true;
    }
  }
  if (dirty) _write(settingsFile, data);
}

function disableChat(settingsFile, chatKey) {
  const key = String(chatKey || '');
  const data = _read(settingsFile);
  let dirty = false;
  // Always add to the hard deny-list so `/off` is effective in every
  // mode — including legacy default-on.
  if (!Array.isArray(data.disabled_chats)) {
    data.disabled_chats = [];
    dirty = true;
  }
  if (!data.disabled_chats.map(String).includes(key)) {
    data.disabled_chats.push(key);
    dirty = true;
  }
  // Drop from the allow-list when opt-in mode is active. Don't create
  // the field on absence — see the comment in enableChat.
  if (Array.isArray(data.enabled_chats)) {
    const before = data.enabled_chats.length;
    data.enabled_chats = data.enabled_chats.filter((k) => String(k) !== key);
    if (data.enabled_chats.length !== before) dirty = true;
  }
  if (dirty) _write(settingsFile, data);
}

// Daemon-side single-call entry. Returns:
//   null                             — text isn't a toggle command
//   { kind, reply, owner_only? }     — daemon should send `reply` and stop.
//
// `kind` is one of "toggle-on" / "toggle-off" / "toggle-status" /
// "toggle-denied". The daemon decides whether to also `continue` past
// adapter forwarding (always yes for these).
function handleToggleCommand({ text, chatKey, isOwner, settingsFile }) {
  const cmd = String(text || '').trim().toLowerCase();
  if (cmd !== '/on' && cmd !== '/off' && cmd !== '/status') return null;
  if (!isOwner) {
    return {
      kind: 'toggle-denied',
      reply: `Nur der Bot-Owner darf ${cmd} benutzen. · Only the bot owner may use ${cmd}.`,
    };
  }
  const data = _read(settingsFile);
  if (cmd === '/on') {
    enableChat(settingsFile, chatKey);
    return {
      kind: 'toggle-on',
      reply: '✅ KI ist für diesen Chat aktiviert. · AI is on for this chat.',
    };
  }
  if (cmd === '/off') {
    disableChat(settingsFile, chatKey);
    return {
      kind: 'toggle-off',
      reply: '🛑 KI ist für diesen Chat deaktiviert. · AI is off for this chat.',
    };
  }
  // /status — clean two-line reply: state first, mode hint second
  // (only when the operator hasn't opted into the explicit allow-list).
  const on = isChatEnabled(data, chatKey);
  const state = on
    ? '✅ KI ist für diesen Chat aktiv. · AI is on.'
    : '🛑 KI ist für diesen Chat deaktiviert. · AI is off.';
  const optIn = isToggleEnabled(data);
  const note = optIn
    ? ''
    : '\n_Modus: default-on — die KI ist in jedem Whitelist-Chat aktiv, sofern nicht explizit per `/off` deaktiviert._';
  return {
    kind: 'toggle-status',
    reply: state + note,
  };
}

module.exports = {
  isToggleEnabled,
  isChatEnabled,
  enableChat,
  disableChat,
  handleToggleCommand,
};
