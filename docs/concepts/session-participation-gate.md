# Concept: Session Participation Gate (SPG)

**Status:** Implemented — see ADR-0166  
**Proposed ADR:** ADR-0166  
**Author:** shumway  
**Created:** 2026-06-26

---

## 1. Problem Statement

CorvinOS bridges run in messaging platforms where chats are often **shared spaces**:
WhatsApp groups, Discord channels, Telegram groups. The current access model has
two gaps that create privacy and UX problems:

**Gap A — Surveillance feeling for non-owners:**  
Any user who happens to be in the same chat as the bot can see when it responds,
can read its output, and — if they are on the whitelist — can trigger it. Other
users who are *not* on the whitelist still have their messages silently dropped,
but there is no guarantee the bot is not "listening." For non-technical users
sharing a group chat, this creates a legitimate "am I being monitored?" concern.

**Gap B — No in-chat participation control:**  
The whitelist is static configuration (file-based, requires operator access).
There is no way for the owner to temporarily open the bot to a friend in the same
group, nor to clearly signal "the bot is off right now." The only dynamic
mechanism (`/consent`) is opt-in for the *reader*, not an invitation from the
*owner*.

**What users expect:**  
- "The bot is mine. By default, only I can talk to it."
- "If I want my friend to use it for a moment, I explicitly invite them."
- "Other people in the group should not feel surveilled."
- "I can turn it completely off for a while without changing config files."

---

## 2. Existing Mechanisms (What We Build On)

| Mechanism | Layer | What it does | Limit |
|---|---|---|---|
| `whitelist` in settings.json | L16 | Static owner list — non-listed messages silently dropped | File-only, no in-chat control |
| `read_only` list | L16 | Read-only senders: see output but can't trigger | File-only |
| Role system (owner→admin→member→observer) | L18 | Persistent grants with TTL | Persists beyond session; stored in roles.json |
| Consent gate (`/consent on\|off\|<ttl>`) | L16 Phase 4 | Per-read-only-user opt-in for their own messages to be seen | Initiated by *reader*, not owner |
| `audience: "all"` in chat_profiles | Adapter | Bypasses whitelist for a specific chat | Too coarse, no time limit |
| `/join` self-registration | L19 | Allows a non-whitelisted user to self-add as observer | Self-service, no owner gate |

**What is missing:** A **dynamic, ephemeral, owner-controlled participation layer**
that:
1. Makes "private" the default for every new chat session
2. Lets the owner invite specific people for a bounded time
3. Ensures non-participants' messages are **not processed** (not sent to LLM, not stored in recall)
4. Provides in-chat commands that do not require config-file access

---

## 3. Design Principles

1. **Private-first:** The default for every chat session is that only whitelisted
   owners can interact. This is not a configurable default — it is the structural
   baseline.

2. **Explicit over implicit:** Participation must be granted by the owner, not
   claimed by the participant. No self-registration in participation mode (unlike
   the observer role via `/join`).

3. **Ephemeral by default:** Session invitations expire at session end or at an
   explicit TTL. They do not persist in `roles.json` unless the owner explicitly
   promotes them to a persistent grant.

4. **GDPR-clean drop:** Messages from non-participants are not forwarded to the LLM,
   not stored in recall.db, not indexed. Only metadata (hashed sender_id, timestamp,
   chat_key) is written to the audit chain — never message content.

5. **Silent non-response:** The bot does NOT reply to non-participant messages.
   Responding (even with "you don't have access") would reveal the bot's presence
   and could create spam on public channels.

6. **Disclosure on open:** When the owner switches to a mode that allows other
   participants, the bot sends an EU AI Act Art. 50 disclosure to the chat
   (same disclosure mechanism as the `/join` card).

7. **Layered with the whitelist:** SPG is a dynamic layer *inside* the whitelist
   gate. Non-whitelisted senders are still dropped by the existing whitelist check
   before SPG is ever evaluated. SPG only applies to senders who *are* on the
   whitelist (or in the role store).

---

## 4. Session Participation Mode

### 4.1 Mode Definition

A new per-chat-session state `session_mode` with three values:

| Mode | Who can interact | In-chat command |
|---|---|---|
| `private` | Whitelisted owners only | `/close` (or default) |
| `invited` | Owners + explicitly invited guests | `/invite @user` |
| `open` | All users with `member` role or above | `/open` |

**Default:** `private` for all new sessions (first message in a chat).  
**Persistence:** The mode is held in memory for the session lifetime. It resets to
`private` on `/reset`, `/clear`, or bridge restart. It does NOT persist to disk
by default.

**Optional persistence:** The operator can pin a chat to a mode via
`chat_profiles` in settings.json:
```json
"chat_profiles": {
  "group-chat-id": {
    "session_mode": "open"
  }
}
```
This is the replacement for `audience: "all"` (which should be deprecated once
SPG is implemented).

### 4.2 Session Invitations

An invitation grants a user the right to interact for the current session.

**Properties:**
- `uid`: the invited user's identifier
- `granted_by`: owner uid
- `granted_at`: wall-clock timestamp
- `expires_at`: `granted_at + ttl_seconds` (default: end of session, `null` for explicit TTL)
- `chat_key`: scoped to this chat
- `source`: `"slash"` | `"auto"` (future)

**Storage:** In-memory dict per `(channel, chat_key)`. Not written to `roles.json`
unless explicitly promoted via `/grant`.

---

## 5. New In-Chat Commands

All commands require `owner` capability (i.e., the sender must be on the whitelist).

### `/close`
Switch the current chat session to `private` mode.  
Effect: Only whitelist owners can trigger the bot. All other users are silently
dropped. No announcement is sent (avoids revealing active participants).  
Audit: `spg.mode_changed` with `mode=private`.

### `/open`
Switch to `open` mode (all members with at least `member` role can interact).  
Effect: Bot sends a disclosure card to the chat (EU AI Act Art. 50).  
Audit: `spg.mode_changed` with `mode=open`, `disclosure_sent=true`.

### `/invite @user [duration]`
Invite a specific user for this session.  
Duration: `30s`, `5m`, `1h`, `24h`, or omit for session-lifetime.  
Sets mode to `invited` if currently `private`.  
Response to owner: "✓ @user invited for [duration / this session]"  
Response to invited user: disclosure card (one-time, per-invite).  
Audit: `spg.guest_invited` with hashed `uid`, `ttl_s`, `granted_by` hash.

### `/uninvite @user` (alias: `/kick @user`)
Remove a previously invited user from the current session.  
They are silently dropped again without notification.  
Audit: `spg.guest_removed` with hashed `uid`.

### `/who`
Owner: list who is currently active in this session.  
Shows: mode, whitelisted owners present, invited guests + their TTL.  
Response is private to the owner (only sent to the requesting sender).

### `/on` / `/off`
Convenience aliases:
- `/on` → equivalent to `/open` (the bot "turns on" for the group)
- `/off` → equivalent to `/close` (the bot "turns off" for the group)

These are the "simple" UX the user requested — easy to remember, clear semantics.

---

## 6. GDPR-Clean Drop

When a message is received from a non-participant (mode=private, user not on
whitelist, user not in session invitation list):

1. The message is **not forwarded to the LLM subprocess**
2. The message is **not written to recall.db** (no FTS5 indexing)
3. The message is **not written to the conversation history**
4. An audit event `spg.message_dropped` is written to the L16 hash chain:
   - Allowed fields: `channel`, `chat_key`, `sender_id_hash` (SHA-256 prefix 8 chars), `session_mode`, `reason`
   - **Forbidden fields:** message content, full sender_id, any PII
5. The message is moved to PROCESSED (inbox dequeue, same as current whitelist drop)
6. **No response is sent** to the sender

This is a strict upgrade over the current `bridge.inbox_whitelist_drift` behavior
which already drops content — SPG adds the recall.db exclusion guarantee.

---

## 7. Interaction with Existing Layers

| Existing layer | Interaction |
|---|---|
| Whitelist check (`_inbox_sender_authorized`) | SPG runs AFTER the whitelist gate. Non-whitelisted senders are still dropped by the whitelist first. SPG only decides for whitelisted-but-not-invited senders. |
| Role system (L18) | `member` and above implies `trigger` capability. SPG's `open` mode respects this. SPG invitations are ephemeral and do NOT create role entries. |
| Consent gate (L16 Phase 4) | Consent governs whether a read-only sender's messages are forwarded to the LLM. SPG governs whether the sender can trigger at all. They are orthogonal: a user needs SPG participation AND active consent for their messages to be included as context in another user's turn. |
| `/join` (L19) | `/join` self-registers as `observer`. In `private` mode, self-registration via `/join` should be suppressed or deferred until the owner runs `/open`. In `open` mode, `/join` works as today. |
| Disclosure card (L19) | SPG triggers the disclosure card when transitioning to `open` or `invited` mode. The same disclosure infrastructure is reused. |
| Audit chain (L16) | SPG emits to the L16 hash chain. New event types: `spg.mode_changed`, `spg.guest_invited`, `spg.guest_removed`, `spg.message_dropped`. |
| Session reset (L8) | `/reset` / `/clear` / `/new` resets the session mode back to `private` and clears all invitations. |

---

## 8. Settings.json Changes (Additive, Non-Breaking)

New optional field in channel settings:

```json
{
  "whitelist": ["owner-id"],
  "group_privacy": "private"
}
```

Values:
- `"private"` (default, omit = private): new sessions start in private mode
- `"open"`: new sessions start in open mode (legacy-compatible behavior)
- `"invite-only"`: new sessions start in invited mode with an empty invitation list

This replaces the current `audience: "all"` override (which will be deprecated
in a follow-up once migration is complete).

---

## 9. Migration Path

| Scenario | Behavior | Action needed |
|---|---|---|
| Existing install with `whitelist: ["me"]` | Works as before — only owner can interact. SPG mode=private by default. No change. | None |
| Existing install with `audience: "all"` | This opens to everyone. Must be replaced with `group_privacy: "open"` or `chat_profiles.<key>.session_mode: "open"`. | Operator opts in to migration |
| Existing install with `read_only: ["friend"]` | Friend is still a read-only observer. To also make them a session participant, owner runs `/invite @friend`. | Owner uses `/invite` |
| New install | SPG private mode is the default. No whitelist = all messages dropped (existing fail-open warning preserved for backward compat). | None |

---

## 10. Open Questions for the ADR

1. **`/join` suppression:** Should `/join` be silently ignored in `private` mode, or
   should it respond with "bot is in private mode, ask the owner to `/open`"? The
   second reveals presence; the first is more privacy-preserving.

2. **Invitation persistence option:** Should `/invite @user --persist` promote the
   invitation to a permanent `member` role in `roles.json`? Or is that the job of
   the existing `/grant` command?

3. **Multi-owner chats:** What if two owners are in the same chat and one runs `/open`
   while the other runs `/close`? Last-write-wins seems correct — the mode reflects
   the last owner command.

4. **Notification to invited user:** When `/invite @user` is run, should the invited
   user receive a disclosure card? Yes — EU AI Act Art. 50 requires disclosure. But
   the disclosure card may reveal to other chat members that the user was invited
   (in a group chat, cards are often visible to all). Consider sending it as a
   direct message if the platform supports it.

5. **`/off` as unconditional kill-switch:** Should `/off` prevent even the *owner*
   from triggering the bot, until they run `/on` again? This would be a true
   "bot is sleeping" state. The current `/close` only blocks others, not the owner.

6. **Deprecation of `audience: "all"`:** Hard cutoff in the same ADR or in a
   follow-up?

---

## 11. ADR-0166 Scope (implemented)

The ADR should cover:
- Formal definition of `session_mode` and its three values
- The SPG gate logic (evaluation order relative to existing gates)
- New audit event schema (all 4 event types)
- In-chat command set (6 commands) with their capability requirements
- settings.json `group_privacy` field
- GDPR-clean drop guarantee (what is and is not stored)
- Disclosure trigger on mode change
- Migration path for `audience: "all"`
- Interaction with L8 (session reset), L16 (consent), L18 (roles), L19 (disclosure)

**Out of scope for ADR-0165:**
- Cross-platform push notification when invited (platform-specific, separate ADR)
- Automatic mode suggestions based on chat size (possible future ML feature)
- Admin-level SPG delegation (admins granting invitations — possible follow-up)
