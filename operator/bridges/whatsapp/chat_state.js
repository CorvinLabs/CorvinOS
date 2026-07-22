// chat_state.js — pure JID + enable/disable helpers for the WhatsApp daemon.
//
// Why this exists:
// WhatsApp identifies the same conversation by TWO parallel JIDs:
//   - <number>@s.whatsapp.net   (classic phone-number JID)
//   - <id>@lid                   (linked-id, used by privacy-mode contacts)
// Baileys delivers messages with `key.remoteJid` populated to one form and
// `key.remoteJidAlt` populated to the other. The pair changes form session-
// to-session for the same chat. The historical daemon only looked at
// remoteJid — so /on registered one form, /off later tried to remove the
// other, the entry stayed in `enabled_chats`, and the bot kept replying.
//
// Fix: register and look up by ALL known forms of the same chat. The helpers
// below take an array of jids (`chatJidsForMessage(m)`) and treat any match
// against `enabled_chats` as "this chat is enabled". /on adds every form,
// /off drops every form.
//
// Hard-deny list (`disabled_chats`):
// Two-list model mirroring chat_toggle.js for Telegram/Discord. `disabled_chats`
// always wins — a chat there is silently blocked even when `enabled_chats` still
// carries the sibling JID form. This closes the JID-form-mismatch gap where
// /off with form A failed to remove form B from enabled_chats.

function normalizeJid(jid) {
  if (!jid) return jid;
  // Strip device suffix: "491...:11@s.whatsapp.net" -> "491...@s.whatsapp.net"
  return jid.replace(/:[0-9]+@/, '@');
}

// Return all known JID forms of the chat the message belongs to.
// Baileys 7+ exposes `key.remoteJidAlt` for the alternative form. Older
// Baileys versions only set `key.remoteJid` — both shapes work.
function chatJidsForMessage(m) {
  const out = [];
  const k = m && m.key;
  if (!k) return out;
  const push = (j) => {
    if (!j) return;
    const n = normalizeJid(j);
    if (n && !out.includes(n)) out.push(n);
  };
  push(k.remoteJid);
  push(k.remoteJidAlt);
  return out;
}

// Expand a set of JIDs to also include every known alias from
// `settings.chat_aliases`. Used by disableChats so a `/off` arriving
// under one form removes the entry stored under a sibling form too.
function _expandAliases(jids, settings) {
  const out = new Set((jids || []).map(normalizeJid).filter(Boolean));
  const aliases = (settings && settings.chat_aliases) || {};
  let frontier = Array.from(out);
  while (frontier.length > 0) {
    const next = [];
    for (const j of frontier) {
      const linked = aliases[j] || [];
      for (const a of linked) {
        const n = normalizeJid(a);
        if (n && !out.has(n)) {
          out.add(n);
          next.push(n);
        }
      }
    }
    frontier = next;
  }
  return out;
}

// True when any of `jids` is in `enabled_chats`, UNLESS any of `jids` (or
// their aliases) is in `disabled_chats` — the hard deny-list always wins.
// Both sides go through normalizeJid so device-suffix variants don't break.
//
// The query JIDs are alias-expanded for BOTH checks. The enabled side needs
// this because the outbound gate only has the single raw reply JID: after a
// re-pair WhatsApp issues a new @lid, /on may have registered only the phone
// form, and replies addressed to the @lid form were dropped as "disabled
// chat" while inbound (which sees both forms) kept passing. Expanding the
// query restores the symmetry. The deny side expands the same way, so the
// widened enabled match can never step around a /off.
function isAnyChatEnabled(jids, settings) {
  const list = jids || [];
  if (list.length === 0) return false;
  const query = _expandAliases(list, settings);

  // Hard deny: disabled_chats beats enabled_chats.
  if (Array.isArray(settings.disabled_chats) && settings.disabled_chats.length > 0) {
    const denied = _expandAliases(settings.disabled_chats, settings);
    for (const j of query) {
      if (denied.has(j)) return false;
    }
  }

  const enabled = (settings.enabled_chats || []).map(normalizeJid);
  for (const j of query) {
    if (enabled.includes(j)) return true;
  }
  return false;
}

// Add every jid in `jids` to `settings.enabled_chats` and remove them from
// `settings.disabled_chats` (so /on always un-does a prior /off). De-dupes
// by normalized form. Creates cross-link aliases when multiple forms are present.
// Mutates `settings` in place — caller is responsible for persisting.
function enableChats(jids, settings) {
  const list = (jids || []).map(normalizeJid).filter(Boolean);
  if (!Array.isArray(settings.enabled_chats)) settings.enabled_chats = [];
  const have = new Set(settings.enabled_chats.map(normalizeJid));
  for (const n of list) {
    if (!have.has(n)) {
      settings.enabled_chats.push(n);
      have.add(n);
    }
  }
  if (list.length >= 2) {
    if (!settings.chat_aliases || typeof settings.chat_aliases !== 'object') {
      settings.chat_aliases = {};
    }
    for (const a of list) {
      if (!Array.isArray(settings.chat_aliases[a])) {
        settings.chat_aliases[a] = [];
      }
      const seen = new Set([a, ...settings.chat_aliases[a]]);
      for (const b of list) {
        if (!seen.has(b)) {
          settings.chat_aliases[a].push(b);
          seen.add(b);
        }
      }
    }
  }

  // Clear hard deny-list entries for these JIDs (and their aliases) so /on
  // is always effective even after a prior /off under a different form.
  if (Array.isArray(settings.disabled_chats) && settings.disabled_chats.length > 0) {
    const drop = _expandAliases(list, settings);
    settings.disabled_chats = settings.disabled_chats
      .filter(j => !drop.has(normalizeJid(j)));
    if (settings.disabled_chats.length === 0) delete settings.disabled_chats;
  }
}

// Drop every jid in `jids` (and every recorded alias of those jids) from
// `settings.enabled_chats` AND add them to `settings.disabled_chats` (hard
// deny). This ensures /off is always effective even when the JID form in
// enabled_chats differs from the one in the /off message — the deny-list
// catches subsequent messages that carry the disabled form as remoteJid or
// remoteJidAlt.
// Mutates `settings` in place. Aliases come from `settings.chat_aliases`.
function disableChats(jids, settings) {
  const drop = _expandAliases(jids, settings);

  // Remove from enabled list.
  if (!Array.isArray(settings.enabled_chats)) {
    settings.enabled_chats = [];
  } else {
    settings.enabled_chats = settings.enabled_chats
      .filter((j) => !drop.has(normalizeJid(j)));
    // Aliases that no longer appear in enabled_chats are dead weight —
    // a future /on may re-pair the forms freshly. Prune to keep the
    // settings file small.
    if (settings.chat_aliases && typeof settings.chat_aliases === 'object') {
      for (const k of Object.keys(settings.chat_aliases)) {
        if (drop.has(normalizeJid(k))) delete settings.chat_aliases[k];
      }
    }
  }

  // Hard deny: add every known form (input + aliases) to disabled_chats.
  // This is the structural backstop: even if enabled_chats still carries
  // the sibling JID form (because chat_aliases was empty when /off fired),
  // the deny-list blocks any subsequent message that shares at least one
  // common JID form with this /off message.
  if (!Array.isArray(settings.disabled_chats)) settings.disabled_chats = [];
  const disabledSet = new Set(settings.disabled_chats.map(normalizeJid));
  for (const j of drop) {
    if (!disabledSet.has(j)) {
      settings.disabled_chats.push(j);
      disabledSet.add(j);
    }
  }
}

// Register all jid forms in `jids` as mutual aliases in `settings.chat_aliases`.
// Called for every incoming message that has multiple JID forms. Returns true
// when new aliases were added (caller should persist settings). This populates
// the alias map retroactively for chats enabled before the alias mechanism
// existed, so that subsequent /off calls can find all forms via _expandAliases.
function maybeRegisterAliases(jids, settings) {
  const list = (jids || []).map(normalizeJid).filter(Boolean);
  if (list.length < 2) return false;
  if (!settings.chat_aliases || typeof settings.chat_aliases !== 'object') {
    settings.chat_aliases = {};
  }
  let changed = false;
  for (const a of list) {
    if (!Array.isArray(settings.chat_aliases[a])) {
      settings.chat_aliases[a] = [];
    }
    const seen = new Set([a, ...settings.chat_aliases[a]]);
    for (const b of list) {
      if (!seen.has(b)) {
        settings.chat_aliases[a].push(b);
        seen.add(b);
        changed = true;
      }
    }
  }
  return changed;
}

module.exports = {
  normalizeJid,
  chatJidsForMessage,
  isAnyChatEnabled,
  enableChats,
  disableChats,
  maybeRegisterAliases,
};
