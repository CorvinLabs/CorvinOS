# Corvin — Memory and Recall

Corvin has four distinct memory systems, each serving a different purpose. This
guide explains what each one stores, where the data lives, and what commands control it.

---

## Overview

| System | What it stores | Command family | Storage location |
|---|---|---|---|
| **Voice profile** | How the bot adapts its TTS style to your preferences | `/voice-user-*` | `~/.config/corvin-voice/profile.json` |
| **Recall** | Searchable index of past conversations (PII-redacted) | `/recall`, `/forget` | `~/.corvin/global/memory/recall.db` |
| **User model** | A distilled model of your communication style | (automatic) | `~/.corvin/global/memory/user_model/<chan>__<chat>.json` |
| **Vault** | Secrets (API keys, passwords) injected into forge tools | `/vault *` | `~/.config/corvin-voice/secrets.json` |

---

## 1. Voice listener profile (`/voice-user-*`)

The listener profile tells the TTS pipeline how to adapt responses for you: vocabulary
level, jargon density, preferred style, and topic context. It is rendered as a
`HÖRER-PROFIL` block that is injected into the TTS system prompt *after* the persona
block and *before* the faithfulness SELF-CHECK — order is load-bearing for correct
behavior.

### Commands

| Command | Effect |
|---|---|
| `/voice-user-show` | Print your current profile as a formatted table |
| `/voice-user-set <field> <value>` | Update one field in your profile |
| `/voice-user-clear` | Reset your profile to defaults |
| `/voice-user-preview` | Generate a sample TTS response using your current profile (no message sent) |
| `/voice-user-help` | Show the full field reference |

### Fields

| Field | Type | Values | Default | Effect |
|---|---|---|---|---|
| `level` | enum | `novice` / `intermediate` / `expert` | `intermediate` | Vocabulary and explanation depth |
| `jargon` | integer | 0 – 5 | `2` | 0 = plain language; 5 = dense technical jargon |
| `style` | enum | `brief` / `detailed` / `conversational` | `conversational` | Response length and register |
| `background` | free text | Any string | (empty) | Appended as context: "The user has a background in ..." |
| `metaphors` | free text | Any string | (empty) | Preferred analogy domains: "The user responds well to cooking metaphors." |
| `domains` | comma-separated list | Any terms | (empty) | Topic areas to assume familiarity with |
| `learning` | integer | 0 – 3 | `1` | 0 = no learning mode; 3 = maximum elaboration and Socratic questions |

### Examples

```
/voice-user-set level expert
/voice-user-set jargon 4
/voice-user-set style brief
/voice-user-set background software engineer with 10 years in distributed systems
/voice-user-set domains Kubernetes, Rust, event sourcing
/voice-user-set metaphors I like physics analogies
```

### Storage

The profile is stored in `~/.config/corvin-voice/profile.json`. It is not part of the
audit chain (it contains no PII). You can edit it directly if you prefer:

```json
{
  "voice_audience_level": "expert",
  "voice_audience_jargon": 4,
  "voice_audience_style": "brief",
  "voice_audience_background": "software engineer with 10 years in distributed systems",
  "voice_audience_metaphors": "I like physics analogies",
  "voice_audience_domains": ["Kubernetes", "Rust", "event sourcing"],
  "voice_audience_learning": 1
}
```

Changes take effect on the next voice message — no restart needed.

---

## 2. Conversation recall (`/recall` and `/forget`)

Corvin indexes every conversation turn into a local FTS5 SQLite database so you can
search past conversations and so the user model (see below) can be distilled over time.

### How it works

- After each bridge turn, the adapter calls `index_turn()` in the background (best-effort).
- Text is **PII-redacted before storage** — names, email addresses, phone numbers, and
  credit card numbers are replaced with `[REDACTED]` before the text enters the index.
- The database is at `~/.corvin/global/memory/recall.db` with file mode `0600`.
- Recall is **enabled per persona** via `memory_recall_enabled: true` in the persona
  config. It is disabled by default for the base `assistant` persona. Operators opt in
  deliberately for personas that benefit from persistent context.

### Searching recall

```
/recall <query>
```

Full-text search over past conversations. Returns a ranked list of matching turns
with timestamps and a snippet. The query supports FTS5 syntax (quoted phrases,
boolean operators):

```
/recall "deployment failure"
/recall Kubernetes AND crash
/recall "last week" api error
```

Results are limited to conversations from your chat (scoped to the current channel
and chat ID). You cannot search another user's recall.

### Erasing specific turns

```
/forget <query-or-id>
```

If you pass a search query, Corvin shows matching turns and asks you to confirm
before deleting them. If you pass an event ID from a `/recall` result, that specific
turn is deleted without a confirmation prompt.

`/forget` is the per-turn erasure command. For a full GDPR Art. 17 subject erasure
(all data, all layers), see the `/forget` section below.

---

## 3. User model (automatic)

Alongside the turn index, Corvin maintains a **user model** — a distilled JSON
summary of how you communicate. This is injected as a `<user_context>` block at the
end of the system prompt (it must be last to satisfy the most-recent-instruction rule).

### How distillation works

Every 50 turns, the adapter runs a background `claude -p --max-turns 1 --no-tools`
subprocess that reads the recent turn index and writes a fresh model to:

```
~/.corvin/global/memory/user_model/<chan>__<chat>.json
```

The model schema:

```json
{
  "communication_style": "direct and technical; prefers numbered lists over prose",
  "preferred_topics": ["distributed systems", "Rust", "homelab"],
  "vocabulary_markers": ["trade-off", "latency", "pipeline"],
  "interaction_patterns": ["asks follow-up questions", "provides code snippets"],
  "notable_context": ["runs Corvin on a Proxmox cluster", "main language is German"],
  "last_updated": "2026-05-21T10:30:00Z",
  "turn_count": 150
}
```

The model is injected silently — no visible change to how the bot appears to you.
The effect is that the bot gradually adapts its responses to your style without
you needing to re-explain your preferences in each session.

### Enabling the user model

User modeling is opt-in per persona. Operators enable it by adding to a persona config:

```json
{
  "memory_recall_enabled": true
}
```

The `memory_recall_enabled` flag gates both the recall index and the user model.

---

## 4. Secret vault (`/vault *`)

The vault stores secrets (API keys, tokens, passwords) that forge tools can use
at runtime. Values are **never** placed in the LLM context — they are injected
directly into the forge tool's sandbox environment via `bwrap` env variables and
exist only as environment variables inside the subprocess.

### Commands

| Command | Effect |
|---|---|
| `/vault set <name> <value>` | Store a secret under the given name |
| `/vault get <name>` | Show the secret (value is masked: `sk-...***`) |
| `/vault list` | List all secret names — values are never shown |
| `/vault delete <name>` | Permanently remove a secret |
| `/vault help` | Show this command reference |

### Examples

```
/vault set STRIPE_API_KEY sk-live-abc123...
/vault set GITHUB_TOKEN ghp_...
/vault list
  Stored secrets: STRIPE_API_KEY, GITHUB_TOKEN
/vault delete STRIPE_API_KEY
```

### How forge tools access vault secrets

A forge tool declares which secrets it needs in its `meta.secrets` field (names
only — never values):

```json
{
  "meta": {
    "secrets": ["GITHUB_TOKEN"]
  }
}
```

At execution time, the forge runner:
1. Reads the vault at `~/.config/corvin-voice/secrets.json` (mode 0600).
2. Injects the named secrets as environment variables into the bwrap sandbox.
3. The tool subprocess receives them via `os.environ["GITHUB_TOKEN"]`.
4. The values never appear in any log, audit event, or LLM context.

### Storage and security

- Storage: `~/.config/corvin-voice/secrets.json`
- File mode: `0600` (enforced at boot; path-gate blocks writes from bridge/adapter code)
- The boot self-test checks this mode and logs `CRITICAL` if it is wrong.
- Never add a secret *value* to a tool definition — only declare the name.

---

## 5. Right to erasure (`/forget` — full subject erasure)

Corvin implements GDPR Art. 17 (right to be forgotten) through the L36 erasure
orchestrator. A full erasure removes your data from every layer.

### How it works

```
/forget
```

When called without arguments (or with `/forget me`), Corvin initiates a full
subject erasure for your identity (the bridge channel + chat key you are messaging from).

The orchestrator:
1. Emits `erasure.requested` to the audit chain **before** any data is deleted
   (audit-first rule).
2. Walks every registered erasure handler in order:
   - **L28 recall**: deletes your turns from `recall.db` and removes your user model JSON.
   - **L7 skill-forge**: removes any user-scope skills you created.
   - **L33 session artifacts**: deletes your session artifact files and manifest entries.
   - **Identity mapping**: removes the mapping from your bridge identity to the
     internal pseudonymous subject ID.
3. Emits a per-layer `erasure.applied` or `erasure.failed` event for each handler.
4. Emits `erasure.completed` with an overall status summary.

### The audit chain and GDPR

The audit chain uses **pseudonymous subject IDs** throughout — it does not store raw
usernames, phone numbers, or email addresses. When you erase your identity mapping,
the audit events that mention your subject ID remain in the chain (which cannot be
edited without breaking the hash chain), but they can no longer be traced back to
you as a person. This follows EDPB pseudonymisation guidance and satisfies Art. 17
without breaking the tamper-evident audit record.

### What erasure does NOT delete

- **Sealed audit segments** — hash-sealed audit chain files are not modified by
  erasure. The pseudonymised events remain; the link between the pseudonym and you
  is gone.
- **Project-scope skills** that were promoted by an operator — these require a
  separate operator action.
- **Pinned artifacts** in `global/artifacts/` — these survive the session purge;
  an operator must delete them manually.

### After erasure

The bot will treat you as a new user on your next message (fresh disclosure card,
no recalled context, no user model). The vault is not cleared by erasure — vault
secrets are tied to the operator account, not to individual chat users.

---

## Data locations summary

| Data | Path | Mode | Cleared by `/forget` |
|---|---|---|---|
| Recall database | `~/.corvin/global/memory/recall.db` | 0600 | Yes |
| User model | `~/.corvin/global/memory/user_model/<chan>__<chat>.json` | 0600 | Yes |
| Vault | `~/.config/corvin-voice/secrets.json` | 0600 | No (operator data) |
| Voice profile | `~/.config/corvin-voice/profile.json` | 0600 | No (self-service) |
| Session artifacts | `~/.corvin/tenants/_default/sessions/<bridge>:<chat>/artifacts/` | dir | Yes (session scope) |
| Pinned artifacts | `~/.corvin/tenants/_default/global/artifacts/` | dir | No (operator must delete) |
| Audit chain | `~/.corvin/tenants/_default/global/audit.jsonl` | 0600 | No (pseudonymised) |
