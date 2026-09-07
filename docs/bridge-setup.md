# CorvinOS — Bridge Setup Guide

Add a messaging bridge to CorvinOS in minutes.
Each bridge connects one messenger channel to the AI assistant.
You can add or remove bridges at any time — no reinstall needed.

---

## Quick overview

| Bridge | What you need | Setup time |
|---|---|---|
| **Discord** | A Discord bot token | ~5 min |
| **Telegram** | A bot token from @BotFather | ~3 min |
| **WhatsApp** | A WhatsApp account (QR-code scan) | ~2 min |
| **Slack** | A Slack app with OAuth token | ~10 min |
| **Email** | IMAP/SMTP credentials | ~5 min |

---

## Option A — Console UI (recommended)

1. Open the web console: `corvin serve` → <http://localhost:8765/console/>
2. Go to **Settings → Bridges**
3. Click **Add bridge**, pick the messenger, and follow the wizard
4. The bridge starts automatically once the token is saved

---

## Option B — Command line

```bash
# Interactive bridge wizard (all steps guided):
corvin-install          # or: corvin setup (if already installed)

# Add a single bridge later:
corvin-install --bridge discord   # guided token setup for Discord only
```

---

## Discord

### 1. Create a bot
1. Open <https://discord.com/developers/applications>
2. Click **New Application** → give it a name → **Create**
3. Left sidebar: **Bot** → **Add Bot** → confirm
4. Under **Token**: click **Reset Token** → copy the token

### 2. Privileged Intents (optional)
The bot token alone is enough: without any privileged intent, DMs and
@mentions work out of the box (the daemon detects the portal state and starts
in token-only mode). Enable on the **Bot** page under **Privileged Gateway
Intents** only if you want more:
- **Message Content Intent** — read *all* guild-channel text (not just @mentions)
- **Server Members Intent** — optional, for user management

### 3. Invite the bot to your server
1. Left sidebar: **OAuth2 → URL Generator**
2. Scopes: `bot`
3. Bot permissions: `Send Messages`, `Read Message History`, `Attach Files`, `Use Slash Commands`
4. Copy the generated URL → open in browser → invite to your server

### 4. Save the token
In the console: **Settings → Bridges → Discord → paste token → Save**

Or on the command line:
```bash
# Place the token in the bridge config:
mkdir -p ~/.corvin/bridges/discord
cat > ~/.corvin/bridges/discord/settings.json <<'EOF'
{
  "discord_token": "YOUR_BOT_TOKEN_HERE",
  "whitelist": ["your_discord_user_id"],
  "rate_limit_per_minute": 20
}
EOF
```

Find your Discord user ID: **Discord Settings → Advanced → Developer Mode on** → right-click your name → **Copy User ID**

---

## Telegram

### 1. Create a bot via @BotFather
1. Open Telegram → search for **@BotFather** → `/start`
2. Send `/newbot` → follow the prompts
3. Copy the **HTTP API token** (looks like `123456:ABC-DEF...`)

### 2. Set up the bridge
Console: **Settings → Bridges → Telegram → paste token → Save**

Or via command line:
```bash
mkdir -p ~/.corvin/bridges/telegram
cat > ~/.corvin/bridges/telegram/settings.json <<'EOF'
{
  "telegram_token": "YOUR_BOT_TOKEN",
  "whitelist": ["your_telegram_user_id"],
  "rate_limit_per_minute": 20
}
EOF
```

Find your Telegram user ID: message **@userinfobot** → it replies with your numeric ID.

### 3. Disable the privacy mode (optional)
If you want the bot to read group messages:
`/setprivacy` → select your bot → **Disable**

---

## WhatsApp

WhatsApp uses a QR-code scan — no token needed.

### 1. Start the pairing flow (one click — no terminal)
In the console: **Setup wizard → WhatsApp → Start WhatsApp bridge**, or
**Settings → Bridges → WhatsApp → QR / Re-link → Start WhatsApp bridge**.

The button installs Node.js + the WhatsApp dependencies on demand (one-time)
and starts the bridge daemon for you. Live progress is shown; the QR code then
appears **in the console** as soon as the daemon is running.

Terminal alternative (Linux/macOS):
```bash
bridge.sh up                 # starts configured bridges; QR served on :7891
```

### 2. Scan the QR code
Open WhatsApp on your phone → **Settings → Linked Devices → Link a Device** → scan
the QR shown in the console. To link by number instead, start the bridge with
`--pair-code +49123456789`.

The session is saved automatically. Re-authentication is needed every ~14 days.

> **Note:** WhatsApp requires Node.js ≥ 20 (Baileys). The console auto-installs
> a pinned Node.js LTS if your system Node is missing or older than 20, then
> runs `npm install` in `~/.corvin/bridges/whatsapp/` on first start. If
> auto-install fails (e.g. no winget on Windows), the console shows per-OS
> manual steps with a link to nodejs.org.

---

## Slack

### 1. Create a Slack app
1. Open <https://api.slack.com/apps> → **Create New App → From scratch**
2. Name the app, pick your workspace

### 2. Add OAuth scopes
Left sidebar → **OAuth & Permissions → Scopes → Bot Token Scopes**:
- `channels:history`, `channels:read`
- `chat:write`, `files:write`
- `im:history`, `im:read`, `im:write`
- `users:read`

### 3. Install the app and copy the token
Left sidebar → **Install App** → **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-...`)

### 4. Save the token
Console: **Settings → Bridges → Slack → paste xoxb-... token → Save**

---

## Email

### 1. Prepare credentials
You need:
- IMAP server + port (e.g. `imap.gmail.com:993`)
- SMTP server + port (e.g. `smtp.gmail.com:587`)
- Login email + password (or app-specific password)

For Gmail: enable IMAP in Gmail settings and create an **App Password** (requires 2FA):
<https://myaccount.google.com/apppasswords>

### 2. Save the config
Console: **Settings → Bridges → Email → fill in the form → Save**

### 3. Sender authentication (important)
The `From` header alone is forgeable, so inbound email is trusted only when the
receiving provider's **top `Authentication-Results` line shows DMARC (or aligned
DKIM) pass**. Consequences:

- An **empty whitelist denies every sender** — claim ownership with the PIN
  `/auth <pin>` flow (set `pin` in the email settings) instead of listing
  addresses up front.
- On **Gmail / iCloud / Outlook / Yahoo** this works out of the box: the expected
  authserv-id is **derived from your `imap_host`** (hardened 2026-09-07, R2-B2).
  Reading `imap.gmail.com` trusts only Google's ids, `outlook.office365.com` only
  Microsoft's, and so on. Before this change ANY well-known authserv-id was accepted
  whatever mailbox you were reading, so on a non-stamping provider an attacker could
  simply inject `Authentication-Results: mx.google.com; dmarc=pass` and be trusted.
- On a **self-hosted / non-stamping IMAP** provider — or behind a gateway such as
  Mimecast / Proofpoint — there is no derivable receiver, so you MUST set
  `auth_results_authserv_id` to your receiver's authserv-id; otherwise inbound
  messages fail closed and senders fall back to the PIN flow. An explicit pin always
  wins over the derived family.
- `dev_mode: true` restores the legacy open behaviour **for local testing only**.

How the `Authentication-Results` line is read (hardened 2026-09-07, F-B1): the line is
split into its `;`-separated method clauses (RFC 8601). A `dmarc=` verdict other than
`pass` closes the gate regardless of any DKIM clause; without a DMARC verdict the
fallback accepts only a `dkim=pass` clause whose OWN `header.d=` / `header.i=…@domain`
is aligned with the From domain — a `header.d=` that belongs to a *failed* signature on
the same line no longer counts. RFC 5322 **comments and quoted strings are stripped
before any token is read** (R2-B1, 2026-09-07) and every property name is anchored at a
token boundary, so `dkim=pass header.d=evil.com (comment dkim=pass header.d=example.com)`,
`header.i="header.d=example.com"@evil.com` and a `;` hidden inside a comment can no
longer forge an aligned pass (regression cases in `email/test_inbound_auth.js`).

### 4. What the daemon does with each mail (2026-09-07)
- **Processed-UID memory:** `<corvin_home>/bridges/email/imap_state.json` (0600) records
  every UID a decision was reached for (accepted, rejected, or failed), keyed by the
  mailbox `UIDVALIDITY`. A mail is never re-parsed, so a throwing attachment cannot loop.
  The set is bounded by a **low-water mark** plus a **per-poll download cap**
  (R2-B3, 2026-09-07): it used to evict by count alone, so with more than
  `IMAP_STATE_MAX_UIDS` (5000) unread rejected mails the oldest UIDs fell out and were
  re-downloaded on **every** poll, forever — a DoS an unauthenticated sender could
  sustain. Now every UID below `min_uid` counts as processed (IMAP UIDs are monotonic
  and the poll drains the smallest first), and one poll downloads at most
  `IMAP_POLL_MAX_DOWNLOADS` (200) messages, so a flood is drained in bounded slices.
  The state file is validated on load: only finite non-negative integer UIDs survive,
  and a stringly `uidvalidity` no longer triggers a spurious reset
  (`email/test_imap_state.js`).
- **`\Seen` only for accepted mail:** the read flag is set only when the bridge actually
  took the mail (inbox envelope written or an in-chat command answered). Spoofed /
  unauthorised / rate-limited mail stays **unread** in your mailbox for you to look at.
- **Attachment names** are sanitised to a safe basename (`.`, `..`, empty → `file`;
  ≤ 120 chars, extension kept).
- **Logs carry fingerprints, not addresses:** `voice.log` shows `from=<sha256[:12]>`.
- **Credentials:** precedence is `EMAIL_IMAP_*` / `EMAIL_SMTP_*` env → `settings.json` →
  `GMAIL_APP_PASSWORD` (+ `GMAIL_USER`) — so the app password you already export for the
  rest of CorvinOS works without copying it into a file. Every daemon-side write of
  `settings.json` is atomic and **0600**; run `chmod 600` once on copies you created by
  hand.

---

## Adding a second bridge

You can run multiple bridges simultaneously (e.g. Discord + Telegram):

```bash
# Each bridge is an independent process.
# Start all enabled bridges:
bridge.sh start all

# Or start/stop individually:
bridge.sh start discord
bridge.sh stop discord
```

In the console: **Settings → Bridges** — each bridge has its own **Start / Stop** toggle.

---

## Removing a bridge

Console: **Settings → Bridges → [bridge name] → Remove**

This stops the bridge process and deletes its credentials.
The bridge package (npm / pip) is NOT uninstalled — run `corvin-uninstall` for a full cleanup.

---

## Troubleshooting

### "Bridge did not start" / no response in the chat app
1. Check logs: `bridge.sh logs discord`  (or the bridge name)
2. Verify the token is correct and has the right permissions
3. On Discord: without **Message Content Intent** the bot only sees DMs and @mentions — mention the bot, or enable the intent for full channel reading
4. Restart the bridge: `bridge.sh restart discord`

### Voice notes not being sent
edge-tts and Piper need ffmpeg to convert their output to OGG-Opus. A
bundled `imageio-ffmpeg` binary is used automatically when no system
ffmpeg is found on PATH (or `FFMPEG_BIN`), so this works out of the box on
every platform — including a fresh Windows install, where the installer
intentionally skips installing system ffmpeg. If you still want a system
ffmpeg (e.g. for other tools):
```bash
# Linux / WSL:
sudo apt install ffmpeg
# macOS:
brew install ffmpeg
# Windows:
winget install ffmpeg
```

### TTS without an OpenAI API key
CorvinOS falls back automatically:
`OpenAI TTS → edge-tts (free, Microsoft, internet) → Piper (fully local, offline)`

No action needed — if `OPENAI_API_KEY` is absent, `edge-tts` is used. It is a base
dependency and the installer's TTS step (`ensure_edge_tts`) reinstalls it explicitly,
so the middle tier stays available even when the bridge runs on a separate/pre-existing
Python interpreter that only had `openai`.
To force fully offline TTS: `CORVIN_TTS_PROVIDER=piper` in `service.env`
(requires a Piper voice model — downloaded automatically as part of a normal
`corvin-install` run since ADR-0185 M2/M3; no separate flag needed. Re-run
`corvin-install` to fetch it if it was skipped due to no network at install
time, or set `piper_model_<lang>` in `~/.config/corvin-voice/config.json`
manually.)
Setting `CORVIN_SAY_NO_FALLBACK=1` makes a *pinned* provider hard-fail instead of
falling through to the auto-chain — a strict/isolation switch (default off) used by
`test_say.sh` to prove a specific tier actually produced the audio rather than being
masked by a later tier. It is not needed for normal runtime.

### Windows — "corvin is not recognized as a command"
The `pip install corvinOS` PATH auto-fix runs on every Python start.
If it did not take effect in the current terminal, either:
- Close and re-open the terminal (PowerShell / CMD)
- Or run: `python -m corvinOS` (path-independent fallback)
