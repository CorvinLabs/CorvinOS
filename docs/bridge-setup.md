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

### 2. Enable Privileged Intents
On the same **Bot** page, scroll to **Privileged Gateway Intents** and enable:
- **Message Content Intent** ✓
- **Server Members Intent** ✓ (optional, for user management)

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
3. On Discord: make sure **Message Content Intent** is enabled
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

### Windows — "corvin is not recognized as a command"
The `pip install corvinOS` PATH auto-fix runs on every Python start.
If it did not take effect in the current terminal, either:
- Close and re-open the terminal (PowerShell / CMD)
- Or run: `python -m corvinOS` (path-independent fallback)
